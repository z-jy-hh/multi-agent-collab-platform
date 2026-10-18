"""Memory layer: namespaces, idempotent writes, op log, late-joiner backfill.

C10 invariants, enforced at exactly two points:
- ``AgentMemoryGuard`` — private memory is strictly bound to one
  (session_id, agent_id); any read/write aimed at another namespace raises
  ``NamespaceViolation``.
- The blackboard is session-level shared and *publish-only*: there is no API
  to overwrite another agent's private memory; publishes are append-only ops
  merged by the pure reducer in core/state.py.

The guard does NOT forbid all cross-namespace reads — the blackboard is
exactly the sanctioned shared channel (C10 wording).
"""

from __future__ import annotations

from core import ids
from core.crdt import HLC, HLCClock, MVEntry, clock_compare, clock_increment, clock_merge, is_unseen
from core.crdt import ClockOrder
from core.errors import NamespaceViolation
from core.schemas import BlackboardOp
from core.storage import BaseOpStore, InMemoryOpStore


class MemoryStore:
    """Raw store façade. Infra code uses this directly; agents only ever hold
    an ``AgentMemoryGuard`` bound to their own identity."""

    def __init__(self, store: BaseOpStore | None = None) -> None:
        self._store: BaseOpStore = store if store is not None else InMemoryOpStore()
        self._clocks: dict[tuple[str, str], HLCClock] = {}
        self._vc_locks: dict[str, object] = {}

    # -- internals ---------------------------------------------------------
    def _hlc_for(self, session_id: str, agent_id: str) -> HLCClock:
        key = (session_id, agent_id)
        if key not in self._clocks:
            self._clocks[key] = HLCClock(f"{session_id}::{agent_id}")
        return self._clocks[key]

    def _next_clock(self, session_id: str, agent_id: str) -> dict[str, int]:
        last = self.merged_clock(session_id, agent_id=agent_id)
        return clock_increment(last, agent_id)

    # -- writes (idempotent by op_id, C6/D2) --------------------------------
    def publish_blackboard(
        self,
        session_id: str,
        agent_id: str,
        key: str,
        value,
        op_id: str,
    ) -> BlackboardOp | None:
        """Publish to the session blackboard. Returns None when op_id dup."""
        op = BlackboardOp(
            op_id=op_id,
            session_id=session_id,
            agent_id=agent_id,
            key=key,
            value=value,
            clock=self._next_clock(session_id, agent_id),
            hlc=self._hlc_for(session_id, agent_id).tick().as_tuple(),
            scope="blackboard",
        )
        return op if self._store.append_op(op) else None

    def private_write(
        self,
        session_id: str,
        agent_id: str,
        key: str,
        value,
        op_id: str,
    ) -> BlackboardOp | None:
        op = BlackboardOp(
            op_id=op_id,
            session_id=session_id,
            agent_id=agent_id,
            key=key,
            value=value,
            clock=self._next_clock(session_id, agent_id),
            hlc=self._hlc_for(session_id, agent_id).tick().as_tuple(),
            scope="private",
        )
        return op if self._store.append_op(op) else None

    # -- reads ---------------------------------------------------------------
    def private_entries(self, session_id: str, agent_id: str) -> list[BlackboardOp]:
        return [
            o
            for o in self._store.ops_for_session(session_id, scope="private")
            if o.agent_id == agent_id
        ]

    def blackboard_ops(self, session_id: str) -> list[BlackboardOp]:
        return self._store.ops_for_session(session_id, scope="blackboard")

    def merged_clock(self, session_id: str, agent_id: str | None = None) -> dict[str, int]:
        """Merged vector clock: whole session, or one agent's components only."""
        merged: dict[str, int] = {}
        for op in self._store.ops_for_session(session_id):
            if agent_id is not None and op.agent_id != agent_id:
                continue
            merged = clock_merge(merged, op.clock)
        return merged

    def entries_for_key(self, session_id: str, key: str) -> list[MVEntry]:
        ops = [o for o in self.blackboard_ops(session_id) if o.key == key]
        return [
            MVEntry(
                value=o.value,
                clock=dict(o.clock),
                hlc=HLC.from_tuple(o.hlc),
                op_id=o.op_id,
                agent_id=o.agent_id,
            )
            for o in ops
        ]

    # -- late joiner (C10/D6) --------------------------------------------------
    def ops_since(self, session_id: str, watermark: dict[str, int]) -> list[BlackboardOp]:
        """Backfill: every blackboard op whose clock is NOT dominated by the
        watermark — i.e. anything the joiner has not provably seen."""
        return [
            op
            for op in self.blackboard_ops(session_id)
            if is_unseen(op.clock, watermark)
        ]

    def watermark_for(self, session_id: str, agent_id: str) -> dict[str, int]:
        """What this agent has already seen = its own merged clock."""
        return self.merged_clock(session_id, agent_id=agent_id)

    # -- infra ---------------------------------------------------------------
    @property
    def op_count(self) -> int:
        return self._store.ops_count()

    def guard(self, session_id: str, agent_id: str) -> "AgentMemoryGuard":
        return AgentMemoryGuard(self, session_id, agent_id)


class AgentMemoryGuard:
    """The only memory handle agents ever receive (C10 enforcement point)."""

    def __init__(self, store: MemoryStore, session_id: str, agent_id: str) -> None:
        self._store = store
        self.session_id = session_id
        self.agent_id = agent_id

    # -- private memory: bound identity, hard check --------------------------
    def private_write(self, key: str, value, op_id: str) -> BlackboardOp | None:
        return self._write_as(self.session_id, self.agent_id, key, value, op_id)

    def private_read(self, key: str) -> list[BlackboardOp]:
        return self._read_as(self.session_id, self.agent_id, key)

    def private_write_to(self, session_id: str, agent_id: str, key: str, value, op_id: str) -> BlackboardOp | None:
        """Uniform API; cross-namespace targets raise NamespaceViolation."""
        return self._write_as(session_id, agent_id, key, value, op_id)

    def private_read_from(self, session_id: str, agent_id: str, key: str) -> list[BlackboardOp]:
        return self._read_as(session_id, agent_id, key)

    def _write_as(self, session_id: str, agent_id: str, key: str, value, op_id: str):
        if (session_id, agent_id) != (self.session_id, self.agent_id):
            raise NamespaceViolation(
                f"agent {self.agent_id!r} (session {self.session_id!r}) tried to "
                f"write namespace ({session_id!r}, {agent_id!r})"
            )
        return self._store.private_write(session_id, agent_id, key, value, op_id)

    def _read_as(self, session_id: str, agent_id: str, key: str) -> list[BlackboardOp]:
        if (session_id, agent_id) != (self.session_id, self.agent_id):
            raise NamespaceViolation(
                f"agent {self.agent_id!r} (session {self.session_id!r}) tried to "
                f"read namespace ({session_id!r}, {agent_id!r})"
            )
        return [o for o in self._store.private_entries(session_id, agent_id) if o.key == key]

    # -- blackboard: session-level shared, publish-only (C10) -----------------
    def publish(self, key: str, value, op_id: str) -> BlackboardOp | None:
        return self._store.publish_blackboard(self.session_id, self.agent_id, key, value, op_id)

    def read_blackboard(self, session_id: str | None = None):
        """Sanctioned shared read — only for the bound session."""
        sid = session_id or self.session_id
        if sid != self.session_id:
            raise NamespaceViolation(
                f"agent {self.agent_id!r} tried to read blackboard of session {sid!r}"
            )
        return self._store.blackboard_ops(self.session_id)

    def backfill_from(self, watermark: dict[str, int]) -> list[BlackboardOp]:
        """Late joiner: pull everything unseen since its watermark."""
        return self._store.ops_since(self.session_id, watermark)

    @staticmethod
    def derive_op_id(run_id: str, node: str, key: str, version_index: int) -> str:
        return ids.derive_op_id(run_id, node, key, version_index)


__all__ = [
    "MemoryStore",
    "AgentMemoryGuard",
    "ClockOrder",
    "clock_compare",
    "clock_merge",
]
