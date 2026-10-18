"""CRDT primitives: vector clocks, HLC, MV-register and LWW fallback (C11).

- Vector clocks only give a PARTIAL order: four states, never a bare boolean.
- LWW tiebreak uses HLC (wall, counter, agent_id): a total order that stays
  monotonic under wall-clock rollback. Bare ``time.time()`` is forbidden.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable


class ClockOrder(str, Enum):
    DOMINATES = "dominates"
    DOMINATED = "dominated"
    CONCURRENT = "concurrent"
    EQUAL = "equal"


def clock_compare(a: dict[str, int], b: dict[str, int]) -> ClockOrder:
    """Four-state comparison (C11). Pure function."""
    keys = set(a) | set(b)
    a_ge = all(a.get(k, 0) >= b.get(k, 0) for k in keys)
    b_ge = all(b.get(k, 0) >= a.get(k, 0) for k in keys)
    if a_ge and b_ge:
        return ClockOrder.EQUAL
    if a_ge:
        return ClockOrder.DOMINATES
    if b_ge:
        return ClockOrder.DOMINATED
    return ClockOrder.CONCURRENT


def clock_merge(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    """Component-wise max. Pure function."""
    return {k: max(a.get(k, 0), b.get(k, 0)) for k in set(a) | set(b)}


def clock_increment(clock: dict[str, int], agent_id: str) -> dict[str, int]:
    """Return a new clock with agent_id's component bumped. Pure function."""
    out = dict(clock)
    out[agent_id] = out.get(agent_id, 0) + 1
    return out


def is_unseen(op_clock: dict[str, int], watermark: dict[str, int]) -> bool:
    """Late-joiner predicate: an op is unseen when the watermark does NOT
    dominate the op clock (history backfill, C10/D6). Pure function."""
    return clock_compare(watermark, op_clock) in (
        ClockOrder.CONCURRENT,
        ClockOrder.DOMINATED,
    )


@dataclass(frozen=True, order=True)
class HLC:
    """Hybrid logical clock stamp: (wall_ms, counter, agent_id).

    ``order=True`` gives the stable total order used by LWW (C11).
    """

    wall: int
    counter: int
    agent_id: str

    def as_tuple(self) -> tuple[int, int, str]:
        return (self.wall, self.counter, self.agent_id)

    @classmethod
    def from_tuple(cls, t: Iterable) -> "HLC":
        wall, counter, agent = t
        return cls(int(wall), int(counter), str(agent))


class HLCClock:
    """Per-agent monotonic HLC generator.

    Thread-safe. On wall-clock rollback or same-millisecond bursts the wall
    stays at the last seen value and the counter advances, so successive
    stamps strictly increase — never a random winner (C11).
    """

    def __init__(
        self,
        agent_id: str,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._now = now_ms if now_ms is not None else (lambda: int(time.time() * 1000))
        self._last_wall = self._now()
        self._counter = -1
        self._lock = threading.Lock()

    def tick(self) -> HLC:
        with self._lock:
            wall = self._now()
            if wall > self._last_wall:
                self._last_wall = wall
                self._counter = 0
            else:
                # rollback or same ms: hold the wall, bump the counter
                self._counter += 1
            return HLC(self._last_wall, self._counter, self._agent_id)


@dataclass
class MVEntry:
    """One value in a multi-value register slot."""

    value: Any
    clock: dict[str, int]
    hlc: HLC
    op_id: str
    agent_id: str

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "clock": dict(self.clock),
            "hlc": self.hlc.as_tuple(),
            "op_id": self.op_id,
            "agent_id": self.agent_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MVEntry":
        return cls(
            value=d["value"],
            clock=dict(d["clock"]),
            hlc=HLC.from_tuple(d["hlc"]),
            op_id=d["op_id"],
            agent_id=d["agent_id"],
        )


def dedupe_entries(entries: list[MVEntry]) -> list[MVEntry]:
    """Same op_id written twice collapses to one entry. Pure function."""
    seen: dict[str, MVEntry] = {}
    for e in entries:
        seen.setdefault(e.op_id, e)
    return list(seen.values())


def mv_merge(entries: list[MVEntry]) -> list[MVEntry]:
    """Multi-value register merge: keep every entry that is not dominated by
    another entry of the same slot. Concurrent values all survive (D4).
    Pure function."""

    def _dominates(a: MVEntry, b: MVEntry) -> bool:
        return clock_compare(a.clock, b.clock) is ClockOrder.DOMINATES

    unique = dedupe_entries(entries)
    survivors: list[MVEntry] = []
    for e in unique:
        dominated = any(_dominates(other, e) for other in unique if other is not e)
        if not dominated:
            survivors.append(e)
    survivors.sort(key=lambda x: x.hlc)
    return survivors


def lww_pick(entries: list[MVEntry]) -> MVEntry:
    """Last-writer-wins fallback with a STABLE total order (C11):
    max by (wall, counter, agent_id). Pure function."""
    unique = dedupe_entries(entries)
    if not unique:
        raise ValueError("lww_pick on empty entries")
    return max(unique, key=lambda e: e.hlc)


def concurrent_pairs(entries: list[MVEntry]) -> list[tuple[MVEntry, MVEntry]]:
    """All pairs of entries in mutual conflict (neither dominates). Pure function."""
    out: list[tuple[MVEntry, MVEntry]] = []
    unique = dedupe_entries(entries)
    for i, a in enumerate(unique):
        for b in unique[i + 1:]:
            if clock_compare(a.clock, b.clock) is ClockOrder.CONCURRENT:
                out.append((a, b))
    return out
