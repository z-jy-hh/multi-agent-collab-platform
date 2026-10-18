"""R16-D4/F5: the platform permission policy engine + the ACP
``session/request_permission`` gate.

REAL FRAME CONTRACT (measured twice: dsh-acp/lib/index.js constructs the
params and the reviewer captured real-machine frames): a
``session/request_permission`` carries ONLY ``sessionId`` /
``toolCall.toolCallId`` / ``options`` — **no path, no rawInput, no title**.
Path-based classification ("inside cwd = allow") is therefore NOT possible
in production; the earlier path-scanning policy was tested against synthetic
frames and was green while the real world could only ever hit default-reject.

Policy (F5 redesign, owner work order option ①+②):
- default: REJECT-once (fail-closed — the only evidence-free decision);
- reachable allow paths, exactly two:
  1. the 座舱 permission card (``human_hook``) approves — bounded by the gate
     deadline, else auto-reject + turn cancel (measured V4: the server never
     times out);
  2. the RUN itself was authorized ``danger-full-access`` (owner-approved
     tier, approval=never) — allow-once, audited;
- the answer carries EXACTLY the measured shape
  ``{"outcome": {"outcome": "selected", "optionId": "allow-once"|"reject-once"}}``
  — inventing optionIds is forbidden;
- every answer is audited to the platform op-log (private scope).
"""

from __future__ import annotations

import itertools
import concurrent.futures
import os
import threading
import time
from dataclasses import dataclass

from core.schemas import BlackboardOp

from core.schemas import BlackboardOp

ALLOW = "allow-once"
REJECT = "reject-once"

# F2(复核): the gate is rebuilt every turn, so a per-gate sequence restarts
# at 1 and op_id `dsh-permission-{run}-{seq}` collides with the previous
# turn's audit row → INSERT OR IGNORE silently drops it. A process-global
# counter (plus pid) keeps op_ids unique for the whole bridge lifetime.
_AUDIT_SEQ = itertools.count(1)


@dataclass
class Decision:
    option_id: str                 # allow-once | reject-once (the ONLY options)
    reason: str
    basis: str                     # default-deny | human-hook | run-tier
    latency_ms: float
    timed_out: bool = False


class PermissionPolicy:
    """Decides from the run tier. Constant-time: real frames carry no path
    evidence to inspect (F5), so there is nothing to compute — the allow
    paths are the human hook and the run tier."""

    def __init__(self, cwd: str, mode: str = "workspace-write") -> None:
        self.cwd = cwd                 # kept for audit context (not decidable)
        self.mode = mode               # workspace-write | danger-full-access | read-only

    def decide(self, params: dict) -> Decision:
        t0 = time.perf_counter()
        latency = lambda: (time.perf_counter() - t0) * 1000
        tool_call = params.get("toolCall") or {}
        if not tool_call.get("toolCallId"):
            return Decision(REJECT, "unrecognized permission request shape "
                                    "(no toolCallId) — default deny",
                            "default-deny", latency())
        if self.mode == "danger-full-access":
            return Decision(ALLOW, "run-authorized danger-full-access tier "
                                   "(approval=never); audited",
                            "run-tier", latency())
        return Decision(REJECT, "real frames carry no path evidence — "
                        "fail-closed; allow requires the permission card "
                        "(human hook) or the danger-full-access run tier",
                        "default-deny", latency())


class PermissionGate:
    """Answers ``session/request_permission`` for ONE dsh session.

    - ``human_hook(params) -> option_id | None`` — the 座舱 permission card;
      runs on a worker thread with ``timeout`` — expiry ⇒ reject-once + turn
      cancel (measured V4: the server never times out), audited.
    - ``cancel_session()`` must send the notification-shaped
      ``session/cancel`` (D6).
    """

    def __init__(self, *, run_id: str, platform_session_id: str,
                 dsh_session_id: str, cwd: str, mode: str,
                 store, emit: Callable[[str, dict], None] | None = None,
                 cancel_session: Callable[[], None] | None = None,
                 human_hook: Callable[[dict], str | None] | None = None,
                 timeout: float = 45.0) -> None:
        self.run_id = run_id
        self.platform_session_id = platform_session_id
        self.dsh_session_id = dsh_session_id
        self.cwd = cwd
        self.mode = mode
        self.store = store
        self.emit = emit or (lambda t, p: None)
        self.cancel_session = cancel_session or (lambda: None)
        self.human_hook = human_hook
        self.timeout = timeout
        self._policy = PermissionPolicy(cwd, mode)
        self._seq = 0
        self._lock = threading.Lock()

    def handle_server_request(self, method: str, params: dict) -> dict:
        if method != "session/request_permission":
            raise AcpGateError(f"gate wired for session/request_permission, "
                               f"got {method!r}")
        t0 = time.perf_counter()
        decision, timed_out = self._decide_with_deadline(params)
        latency = (time.perf_counter() - t0) * 1000
        self._audit(params, decision, latency, timed_out)
        if timed_out:
            # measured V4: an unanswered request stalls the turn forever —
            # the deadline path MUST also cancel the turn (notification, D6)
            self.cancel_session()
        return {"outcome": {"outcome": "selected", "optionId": decision.option_id}}

    def _decide_with_deadline(self, params: dict) -> tuple[Decision, bool]:
        auto = self._policy.decide(params)
        if self.human_hook is None:
            return auto, False
        # the host decides — bounded by the gate deadline (measured V4)
        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="dsh-human")
        try:
            future = pool.submit(self.human_hook, params)
            try:
                option = future.result(timeout=self.timeout)
                if option in (ALLOW, REJECT):
                    return Decision(option, "host decision (permission card)",
                                    "human-hook", 0.0), False
                return Decision(REJECT, f"host returned {option!r} — not an "
                                f"allow-once/reject-once option", "human-hook",
                                0.0), False
            except concurrent.futures.TimeoutError:   # SR4: explicit alias of TimeoutError on 3.11
                return Decision(REJECT, f"host did not answer within "
                                f"{self.timeout:.0f}s — auto reject-once",
                                "human-hook", 0.0), True
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _audit(self, params: dict, decision: Decision, latency: float,
               timed_out: bool) -> None:
        tool_call = (params.get("toolCall") or {})
        with self._lock:
            self._seq += 1
            seq = self._seq
        payload = {"ts": time.time(), "run_id": self.run_id,
                   "session_id": self.dsh_session_id,
                   "tool_call_id": tool_call.get("toolCallId"),
                   "decision": decision.option_id, "reason": decision.reason,
                   "basis": decision.basis,
                   "policy_latency_ms": round(latency, 3),
                   "timed_out": timed_out}
        op = BlackboardOp(
            op_id=f"dsh-permission-{self.run_id}-{seq}-{os.getpid()}-{next(_AUDIT_SEQ)}",
            session_id=self.platform_session_id,
            agent_id="dsh_bridge",
            key=f"dsh_permission::{self.run_id}",
            value=payload, clock={}, hlc=(0, 0, "dsh_bridge"),
            ts=time.time(), scope="private")
        self.store.append_op(op)
        self.emit("dsh_permission_request", payload)


class AcpGateError(RuntimeError):
    pass
