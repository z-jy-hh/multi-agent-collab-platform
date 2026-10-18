"""R17-P0b-C (B2): bounded recovery for stalled runs.

READS EXISTING ARTIFACTS (state.json, trace.jsonl, recovery.json,
gate_counters.json) to derive liveness — the caller does NOT pass status or
timestamps. The ONLY input is the run_id.

State machine (bounded, no infinite retry):
  none -> queued (1st stall, auto-retry) -> action_open (2nd stall, needs owner)
  -> escalated (3rd stall, human escalation event)

Queued is a live path ONLY within ``delivery_timeout`` (600s = deadline_prompt_s
default). After that, queued is no longer alive => attempt_count increments =>
the next assess call produces action_open. action_open requires EXPLICIT
closure (resolve_action) or liveness recovery to return to none/recovered.

Every state transition writes a deterministic op-log entry via
``core.storage.append_op`` (op_id = ``recovery:{run_id}:{fingerprint}:{attempt}``).
``append_op`` returns False for a duplicate op_id, providing idempotency.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from core.paths import run_dir

MAX_ATTEMPTS = 3
DELIVERY_TIMEOUT_S = 600.0
NON_TERMINAL = {"running", "summarized", None, ""}


def _recovery_path(run_id: str) -> Path:
    return Path(run_dir(run_id)) / "recovery.json"


def _fingerprint(run_id: str, status: str, last_event_seq: int,
                 live_paths: list[str]) -> str:
    """Fingerprint describes the SITUATION (external state) only.

    Deliberately EXCLUDES attempt_count and recovery state: those describe
    OUR progress, not the situation. If the situation is unchanged the
    fingerprint is unchanged, so retries are bounded by max_attempts and
    the op-log cannot grow without bound (review B-2.i)."""
    payload = json.dumps(
        [run_id, status, last_event_seq, live_paths],
        ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_recovery(run_id: str) -> dict:
    p = _recovery_path(run_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"run_id": run_id, "state": "none", "attempt_count": 0,
            "fingerprint": None, "last_ts": None}


def save_recovery(run_id: str, data: dict) -> None:
    p = _recovery_path(run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def _read_state(run_id: str) -> str | None:
    """Read status from state.json (existing artifact)."""
    p = Path(run_dir(run_id)) / "state.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("status")
    except Exception:
        return None


def _read_last_event(run_id: str) -> tuple[float | None, int]:
    """Read last event timestamp and seq from trace.jsonl (existing artifact)."""
    p = Path(run_dir(run_id)) / "trace.jsonl"
    if not p.exists():
        return None, 0
    last_ts, last_seq = None, 0
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                last_seq = ev.get("seq", last_seq)
                ts = ev.get("ts") or ev.get("timestamp")
                if ts is not None:
                    last_ts = float(ts)
            except (ValueError, TypeError):
                continue
    except Exception:
        pass
    return last_ts, last_seq


def _read_pending_facts(run_id: str) -> dict:
    """P1-HOSTPICK (B1+B3): ONE trace pass deriving BOTH pending facts.

    Returns {"pending_permission": bool,
             "pending_allocation": {subtask_id: deadline_ts}}.

    - Matches on PARSED event types (not raw-line substring scans — a
      payload that merely mentions "permission_request" must not read as a
      live path), legacy non-JSON lines fall back to raw scan.
    - B3: permission pairing and allocation pairing share the same single
      pass over trace.jsonl (no second read for host_pick liveness).
    - B2: the allocation request payload is SELF-CONTAINED — its
      ``deadline_ts`` travels with the event, so liveness needs nothing
      but the trace.
    - B7 honesty: an unreadable trace cannot honestly support ANY live
      path — both facts come back falsy (never silently swallowed).
    """
    p = Path(run_dir(run_id)) / "trace.jsonl"
    if not p.exists():
        return {"pending_permission": False, "pending_allocation": {}}
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):  # ValueError: UnicodeDecodeError (B7)
        return {"pending_permission": False, "pending_allocation": {}}

    requested = answered = False
    alloc_req: dict[str, float] = {}   # subtask_id -> deadline_ts (B2)
    for line in lines:
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            ev_type = str(ev.get("type", ""))
        except ValueError:
            ev_type = line  # legacy non-JSON line: fall back to raw scan
        if ev_type.endswith("permission_request"):
            requested = True
        elif ev_type.endswith("permission_answered"):
            answered = True
        # --- allocation pairing (P1-HOSTPICK) ---------------------------
        elif ev_type.endswith("allocation_choice_requested"):
            sid = str((ev.get("payload") or ev).get("subtask_id", ""))
            dl = (ev.get("payload") or ev).get("deadline_ts")
            if sid and isinstance(dl, (int, float)):
                alloc_req[sid] = float(dl)
        elif ev_type.endswith("allocation_answer"):
            sid = str((ev.get("payload") or ev).get("subtask_id", ""))
            alloc_req.pop(sid, None)
        elif ev_type.endswith("allocation_fallback"):
            body = ev.get("payload") or ev
            for sid in (body.get("subtask_ids") or []):
                alloc_req.pop(str(sid), None)
        elif ev_type.endswith("allocation_conflict"):
            body = ev.get("payload") or ev
            for pair in (body.get("pairs") or []):
                alloc_req.pop(str(pair.get("subtask_id", "")), None)
        elif ev_type in ("run_finished", "run_error", "run_cancelled"):
            # global terminal after the last request: nothing stays pending
            alloc_req.clear()
    return {"pending_permission": requested and not answered,
            "pending_allocation": alloc_req}


def _read_active_backends() -> dict:
    """Read active backends from gate_counters.json."""
    from agent_bridge.gate_counters import snapshot
    return (snapshot() or {}).get("active") or {}


def derive_liveness(run_id: str, *, now: float | None = None,
                    silence_suspicious_s: float = 600.0,
                    delivery_timeout_s: float = DELIVERY_TIMEOUT_S) -> dict:
    """B1 (read-only): derive liveness for ONE run from EXISTING artifacts.

    The ONLY input is the run_id (plus tunable thresholds); status,
    timestamps and pending-permission facts are NEVER caller-supplied —
    they are derived from state.json / trace.jsonl / recovery.json /
    gate_counters.json (no shadow state).

    Review B-2.v: gate_counters ``active`` is a GLOBAL per-backend count,
    not per-run — a busy OTHER backend must not make THIS run look alive,
    so it is reported for observability but does NOT create a live path.

    Returns a dict; writes nothing. Consumers: assess_and_recover (acts on
    it), GET /api/runs/{id}/unified (the liveness block), and the
    ``--check liveness`` audit.
    """
    now = now or time.time()
    rec = load_recovery(run_id)
    status = _read_state(run_id)
    last_event_ts, last_event_seq = _read_last_event(run_id)
    facts = _read_pending_facts(run_id)   # B3: ONE pass for both facts
    pending_permission = facts["pending_permission"]
    pending_allocation = facts["pending_allocation"]
    active_backends = _read_active_backends()

    live_paths: list[str] = []
    if status in NON_TERMINAL:
        if (last_event_ts is not None
                and (now - last_event_ts) <= silence_suspicious_s):
            live_paths.append("active_run")
        if pending_permission:
            live_paths.append("explicit_wait")
        # P1-HOSTPICK (B2+B4): a host pick wait is alive ONLY within the
        # bounded window the request event itself carried (deadline_ts is
        # self-contained in the payload). After the deadline the run falls
        # through to the normal stall path — an un-answered request can
        # never keep itself alive indefinitely (same invariant as
        # queued_continuation / recovery_action).
        for sid, deadline_ts in pending_allocation.items():
            if deadline_ts >= now:
                live_paths.append("host_pick_wait")
                break

    # Recovery states are CONDITIONALLY alive (review B-2.i root cause):
    # queued only within delivery_timeout, action_open only within 2x.
    # Expired windows fall through to stall — a recovery state can never
    # keep itself alive indefinitely.
    state = rec.get("state", "none")
    if state == "queued":
        queued_at = rec.get("queued_at")
        if queued_at is not None and (now - queued_at) <= delivery_timeout_s:
            live_paths.append("queued_continuation")
    elif state == "action_open":
        action_ts = rec.get("action_open_at")
        if action_ts is not None and (now - action_ts) <= delivery_timeout_s * 2:
            live_paths.append("recovery_action")

    return {
        "run_id": run_id,
        "status": status,
        "last_event_ts": last_event_ts,
        "last_event_seq": last_event_seq,
        "pending_permission": pending_permission,
        "pending_allocation": sorted(pending_allocation),
        "active_backends": active_backends,
        "recovery_state": state,
        "live_paths": live_paths,
        "stalled": not live_paths,
    }


def _write_op_log(run_id: str, fingerprint: str, attempt: int,
                  state: str, store=None) -> bool:
    """Write a deterministic op-log entry. Returns False for duplicate
    (idempotent). Uses core.storage.append_op."""
    if store is None:
        try:
            from core.storage import SQLiteOpStore
            from core.paths import storage_path
            store = SQLiteOpStore(storage_path())
        except Exception:
            return False
    from core.schemas import BlackboardOp
    op_id = f"recovery:{run_id}:{fingerprint[:16]}:{attempt}"
    op = BlackboardOp(
        op_id=op_id, session_id=f"recovery-{run_id}",
        agent_id="agent_bridge.recovery",
        key=f"recovery::{run_id}", value={"state": state, "attempt": attempt,
                                          "fingerprint": fingerprint[:16]},
        clock={}, hlc=(0, 0, "recovery"), ts=time.time(), scope="private")
    return store.append_op(op)


def assess_and_recover(run_id: str, *, store=None,
                       now: float | None = None,
                       silence_suspicious_s: float = 600.0,
                       delivery_timeout_s: float = DELIVERY_TIMEOUT_S,
                       max_attempts: int = MAX_ATTEMPTS,
                       emit=None) -> dict:
    """B1+B2: derive liveness from EXISTING artifacts and run bounded recovery.

    The liveness half is ``derive_liveness`` (read-only); this function acts
    on its result: a stalled run advances the bounded state machine
    none -> queued -> action_open -> escalated, one op-log entry per attempt.
    Never cancels the run.
    """
    now = now or time.time()
    liv = derive_liveness(run_id, now=now,
                          silence_suspicious_s=silence_suspicious_s,
                          delivery_timeout_s=delivery_timeout_s)
    rec = load_recovery(run_id)
    status = liv["status"]
    last_event_seq = liv["last_event_seq"]
    live_paths = liv["live_paths"]

    # Artifact-derived liveness (active_run/explicit_wait/host_pick_wait)
    # is EXTERNAL evidence the situation improved: an active worker, a
    # human permission flow, or a pending host pick that has NOT passed its
    # self-carried deadline (B4: host_pick_wait joins the whitelist so a
    # wait inside its window is treated as "situation improved", not as the
    # machine's own open window — it would otherwise fall into the
    # in-cycle branch and eventually stall-increment while the host is
    # still legitimately deciding). recovery-state continuations
    # (queued_continuation/recovery_action) are the machine's OWN open
    # windows — they keep the cycle alive but are NOT "recovered".
    artifact_live = [p for p in live_paths
                     if p in ("active_run", "explicit_wait",
                              "host_pick_wait")]

    if artifact_live:
        # B-2.iii: liveness recovery — if the run was in a recovery state,
        # transition to "recovered" (not silently staying in the old state)
        if rec.get("state", "none") not in ("none", "recovered"):
            rec["state"] = "recovered"
        rec["live_paths"] = live_paths
        rec["last_ts"] = now
        save_recovery(run_id, rec)
        return rec

    if live_paths:
        # in-cycle: only a recovery window is open — no attempt increment
        # (delivery deadline not yet passed), and no "recovered" either.
        rec["live_paths"] = live_paths
        rec["last_ts"] = now
        save_recovery(run_id, rec)
        return rec

    # stalled: live_paths is empty — explicitly set it (B-2.ii)
    rec["live_paths"] = []

    # bounded recovery with situation fingerprint (no attempt_count)
    attempt_count = rec.get("attempt_count", 0)
    fp = _fingerprint(run_id, status or "unknown", last_event_seq, live_paths)

    # same situation + already at/above max attempts => escalated, no more ops
    if rec.get("fingerprint") == fp and attempt_count >= max_attempts:
        rec["last_ts"] = now
        save_recovery(run_id, rec)
        return rec

    attempt_count += 1
    wrote = _write_op_log(run_id, fp, attempt_count,
                          rec.get("state", "none"), store=store)

    if attempt_count == 1:
        rec["state"] = "queued"
        rec["queued_at"] = now
    elif attempt_count == 2:
        rec["state"] = "action_open"
        rec["action_open_at"] = now
        rec["owner"] = "platform"
        rec["action"] = "manual intervention required"
    else:
        rec["state"] = "escalated"

    rec["fingerprint"] = fp
    rec["attempt_count"] = attempt_count
    rec["last_ts"] = now
    save_recovery(run_id, rec)
    if emit:
        emit("recovery_transition", run_id,
             {"state": rec["state"], "attempt_count": attempt_count,
              "op_log_written": wrote})
    return rec


def resolve_action(run_id: str, *, owner: str = "", note: str = "") -> dict:
    """Explicitly close an action_open recovery (owner-driven)."""
    rec = load_recovery(run_id)
    rec["state"] = "recovered"
    rec["resolved_by"] = owner
    rec["resolved_note"] = note
    rec["last_ts"] = time.time()
    save_recovery(run_id, rec)
    return rec
