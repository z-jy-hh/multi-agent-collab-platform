"""R16-D6: the turn runner — drives ONE prompt turn on a dsh session with
budget/cancel/timeout, every path idempotent (taskbook D6 + owner work order ⑥).

Wired together here (and only here):
- ``UpdatePump`` (D3) routes ``session/update`` → platform events + final text
  + usage;
- ``BudgetGate`` (D5) consumes usage; tripping it cancels the turn (latched);
- ``PermissionGate`` (D4) answers ``session/request_permission`` with the
  platform policy (+ 座舱 human hook) and its own deadline;
- cancellation is ALWAYS the notification-shaped ``session/cancel`` (D6:
  request-shaped cancel is silently ignored — measured V5');
- the turn deadline fires the same notification cancel; the prompt request
  itself waits turn_timeout + grace so the cancelled stopReason can arrive;
- ``stopReason`` is normalized: ``end_turn`` / ``cancelled`` / other verbatim.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.schemas import validate_against_schema

from dsh_bridge.acp_client import AcpProcessDied
from dsh_bridge.budget import BudgetGate
from dsh_bridge.events_map import MappedEvent, UpdatePump
from dsh_bridge.permissions import PermissionGate
from dsh_bridge.session import DshDispatchError, DshRunSession


@dataclass
class TurnResult:
    run_id: str
    stop_reason: str | None = None          # end_turn | cancelled | verbatim
    final_text: str = ""
    usage: dict = field(default_factory=dict)
    budget_exceeded: bool = False
    budget_reason: str | None = None
    turn_timed_out: bool = False
    event_kinds: list[str] = field(default_factory=list)
    unknown_update_kinds: dict = field(default_factory=dict)
    result_data: dict | None = None          # D7: validated structured result
    result_validation_errors: list = field(default_factory=list)
    error: str | None = None                 # loud failure (process death …)


class DshTurnRunner:
    """One runner per session; run ONE turn per call (dsh: one prompt per
    session at a time)."""

    def __init__(self, session: DshRunSession, *,
                 emit: callable = None,
                 store=None,
                 price_table: dict | None = None,
                 budget_tokens: int | None = None,
                 budget_money: float | None = None,
                 budget_context_used: int | None = None,
                 permission_timeout: float = 45.0,
                 human_hook: callable = None,
                 turn_timeout: float = 600.0,
                 result_schema: dict | None = None,
                 result_filename: str = "dsh_result.json") -> None:
        self.session = session
        self._emit = emit or (lambda t, p: None)
        self._store = store
        self._price_table = price_table
        self._budget_tokens = budget_tokens
        self._budget_money = budget_money
        self._budget_context_used = budget_context_used
        self._permission_timeout = permission_timeout
        self._human_hook = human_hook
        self._turn_timeout = turn_timeout
        self._result_schema = result_schema
        self._result_filename = result_filename
        self._cancel_lock = threading.Lock()
        self._cancelled = False
        self._deadline_fired = False

    # -- cancellation (D6: notification form, idempotent) ----------------------

    def cancel(self) -> None:
        with self._cancel_lock:
            if self._cancelled:
                return
            self._cancelled = True
        self.session.client.notify(
            "session/cancel", {"sessionId": self.session.dsh_session_id})

    # -- the turn ----------------------------------------------------------------

    def run_turn(self, task_text: str) -> TurnResult:
        client = self.session.client
        # SR2(自查): one AcpClient has ONE handler slot (pump/gate are
        # per-turn) — two concurrent turns on the same client would
        # cross-wire events and permission answers. Single-flight, loud.
        if not client._turn_lock.acquire(blocking=False):
            raise DshDispatchError(
                "another turn is already active on this ACP client — "
                "one turn per client at a time (SR2)")
        try:
            # SR1(自查): per-turn state reset — the cancelled latch used to
            # survive across turns, turning the SECOND turn's cancel into a
            # no-op (a budget/deadline trip would then hang forever)
            self._cancelled = False
            self._deadline_fired = False
            return self._run_turn_inner(task_text)
        finally:
            client._turn_lock.release()

    def _run_turn_inner(self, task_text: str) -> TurnResult:
        client = self.session.client
        budget = BudgetGate(model="default", price_table=self._price_table,
                            budget_tokens=self._budget_tokens,
                            budget_money=self._budget_money,
                            budget_context_used=self._budget_context_used)
        events: list[MappedEvent] = []

        def on_event(mapped: MappedEvent) -> None:
            events.append(mapped)
            self._emit("dsh_turn_event", {
                "run_id": self.session.run_id,
                "kind": mapped.type,
                "payload": mapped.payload})

        def on_usage(usage: dict) -> None:
            # fail-safe: ANY raise here is contained by the client (the run
            # must not die) but would SKIP the budget cancel → invisible hang
            # (R16 实测教训). Structure so even a broken emit can't skip it.
            state = None
            try:
                state = budget.accumulate(usage)
                if state.unrecognized_count:
                    # F7: an unknown usage shape must WARN, never count zero
                    self._emit("dsh_usage_unrecognized", {
                        "run_id": self.session.run_id,
                        "count": state.unrecognized_count,
                        "examples": state.unrecognized_examples})
            except Exception as exc:
                try:
                    self._emit("dsh_bridge_error", {
                        "run_id": self.session.run_id,
                        "where": "budget.accumulate",
                        "error": f"{type(exc).__name__}: {exc}"})
                except Exception:
                    pass
                self.cancel()
                return
            if state.exceeded:
                try:
                    self._emit("dsh_budget_exceeded", {
                        "run_id": self.session.run_id,
                        "reason": state.exceeded_reason})
                except Exception:
                    pass
                self.cancel()               # D5/F7: over budget → cancel first

        pump = UpdatePump(self.session.dsh_session_id,
                          on_event=on_event, on_usage=on_usage)
        gate = PermissionGate(
            run_id=self.session.run_id,
            platform_session_id=self.session.platform_session_id,
            dsh_session_id=self.session.dsh_session_id,
            cwd=self.session.cwd, mode=self.session.permission_mode,
            store=self._store, emit=self._emit,
            cancel_session=self.cancel,
            human_hook=self._human_hook, timeout=self._permission_timeout)
        client.set_handlers(on_notification=pump.handle_notification,
                            on_server_request=gate.handle_server_request)

        self._deadline_fired = False

        def on_deadline() -> None:
            self._deadline_fired = True
            self.cancel()                        # same notification-shaped path

        timer = threading.Timer(self._turn_timeout, on_deadline)
        timer.daemon = True
        result = None
        error: str | None = None
        timer.start()
        try:
            result = client.request(
                "session/prompt",
                {"sessionId": self.session.dsh_session_id,
                 "prompt": [{"type": "text", "text": task_text}]},
                timeout=self._turn_timeout + 60)   # grace for the cancel round-trip
        except AcpProcessDied as exc:
            error = str(exc)                       # loud, never silent restart
        finally:
            timer.cancel()
        # F4 barrier: the prompt returned, but its notifications are still in
        # the async dispatch queue — deliver them BEFORE snapshotting the
        # TurnResult (event_kinds/final_text must be complete, not partial)
        client._notif_queue.join()

        stop_reason = (result or {}).get("stopReason")
        if error is not None:
            stop_reason = None                     # verbatim absence on death
        # budget state defensively: a broken gate (D5 test / preview drift)
        # must degrade the TurnResult, not crash the bridge
        budget_state = getattr(budget, "state", None)
        turn_result = TurnResult(
            run_id=self.session.run_id,
            stop_reason=stop_reason,
            final_text="".join(pump.final_text),
            usage=dict(budget_state.__dict__) if budget_state else {},
            budget_exceeded=getattr(budget_state, "exceeded", False),
            budget_reason=getattr(budget_state, "exceeded_reason", None),
            turn_timed_out=(self._deadline_fired and stop_reason == "cancelled"),
            event_kinds=[e.type for e in events],
            unknown_update_kinds=dict(pump.unknown_kinds),
            error=error)

        if self._result_schema is not None and error is None:
            data, errors = self._read_structured_result()
            turn_result.result_data = data
            turn_result.result_validation_errors = errors
        return turn_result

    # -- D7: structured result contract -----------------------------------------

    def _read_structured_result(self) -> tuple[dict | None, list]:
        """The agent was asked (prompt contract) to write its structured
        result to result_filename inside the session cwd; the platform reads
        and validates it against ITS OWN schema. Validation failure degrades
        to text-only (retry is the caller's choice)."""
        path = Path(self.session.cwd) / self._result_filename
        if not path.exists():
            return None, ["result file missing: " + str(path)]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            return None, [f"result file is not valid JSON: {exc}"]
        errors = validate_against_schema(data, self._result_schema)
        if errors:
            return None, errors
        return data, []
