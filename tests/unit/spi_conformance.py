"""R17-W5b: backend-agnostic SPI conformance suite.

This module imports NOTHING from any backend package (grep-able proof: no
``dsh_bridge`` / ``backends`` / ``factory`` imports). Callers hand in:

- ``make_backend(scenario_args, *, overrides=None) -> AgentBackend`` — builds
  a backend over whatever stack they like (offline fake ACP server, toy
  in-memory, ...). Each call yields an INDEPENDENT backend.
- ``make_spec(run_id) -> SessionSpec`` — builds the session spec (cwd
  included; the suite does not know about dispatch roots).

Every face function raises ``AssertionError`` on failure and
``NotImplementedError`` when the backend deliberately lacks the face; the
runner collects a per-face map:

    PASS | FAIL: <message> | NOT-IMPLEMENTED: <message>

Faces (design v3 §2.3, one per SPI surface + cross-cutting guarantees):
  open_session          prompt_five_kinds_in_order   unknown_archived_verbatim
  permission_allow      permission_reject            cancel_midturn
  turn_deadline         close_idempotent             execute_task_structured
  execute_task_degrades process_death_loud           bad_handshake_loud
  concurrent_prompt     unknown_tier

Optional backend extensions probed via ``getattr`` (missing -> the dependent
faces report NOT-IMPLEMENTED rather than crashing the suite):
  ``handle.wait_permission(timeout) -> PermissionRequest | None``
  ``handle.last_result`` (the composed turn outcome)
"""

from __future__ import annotations

import threading
import time

from agent_bridge.errors import SpiError
from agent_bridge.spi import (
    BudgetSpec,
    PermissionDecision,
    RunAuthorization,
    SessionSpec,
    TaskSpec,
)

CANONICAL_FIVE = ("thought_chunk", "message_chunk", "tool_call",
                  "tool_call_update", "usage_update")


def _authz():
    """Default authorization for conformance faces: consent token present
    (some backends are paid) + data_opt_in=true (some backends egress)."""
    return RunAuthorization(consent_token="suite-default", data_opt_in=True)


def _wait_thread(thread, timeout=30.0):
    thread.join(timeout=timeout)
    return not thread.is_alive()


# -- faces ---------------------------------------------------------------------

def face_open_session(ctx):
    backend = ctx["make_backend"](["updates_then_end"])
    handle = backend.open_session(ctx["make_spec"]("cf-open"))
    assert handle is not None, "open_session returned None"
    handle.close(reason="face done")


def face_prompt_five_kinds_in_order(ctx):
    backend = ctx["make_backend"](["updates_then_end"])
    handle = backend.open_session(ctx["make_spec"]("cf-five"))
    handle.prompt("hello")
    kinds = [e.type for e in handle.events()]
    seen = []
    for want in CANONICAL_FIVE:
        if want in kinds:
            seen.append(kinds.index(want))
    assert len(seen) == 5, f"expected all five kinds, got {sorted(set(kinds))}"
    assert seen == sorted(seen), f"five kinds out of order: {kinds}"
    handle.close(reason="done")


def face_unknown_archived_verbatim(ctx):
    backend = ctx["make_backend"](["unknown_update"])
    handle = backend.open_session(ctx["make_spec"]("cf-unknown"))
    handle.prompt("hello")
    unknowns = [e for e in handle.events() if e.type == "unknown_update"]
    assert unknowns, "no unknown_update event surfaced"
    raw = unknowns[0].raw
    assert raw.get("sessionUpdate") == "future_kind", \
        f"unknown raw not archived verbatim: {raw!r}"
    handle.close(reason="done")


def face_permission_allow(ctx):
    backend = ctx["make_backend"](["permission_roundtrip"])
    handle = backend.open_session(ctx["make_spec"]("cf-perm-a"))
    outcome = {}
    thread = threading.Thread(
        target=lambda: outcome.update(result=handle.prompt("outside write?")),
        daemon=True)
    thread.start()
    req = handle.wait_permission(timeout=15)
    assert req is not None, "no permission request surfaced"
    assert req.tool_call_id, "permission request missing toolCallId"
    assert "allow-once" in req.options, f"options: {req.options}"
    handle.answer_permission(
        req, PermissionDecision(option_id="allow-once", reason="suite",
                                basis="human-hook"))
    assert _wait_thread(thread), "prompt did not finish"
    allow_seen = any(
        getattr(e, "payload", {}).get("decision") == "allow-once"
        for e in handle.events() if "permission" in e.type)
    assert allow_seen, "no audited allow-once decision in the event stream"
    handle.close(reason="done")


def face_permission_reject(ctx):
    backend = ctx["make_backend"](["permission_roundtrip"])
    handle = backend.open_session(ctx["make_spec"]("cf-perm-r"))
    thread = threading.Thread(
        target=lambda: outcome.update(result=handle.prompt("outside write?")),
        daemon=True)
    outcome = {}
    thread.start()
    req = handle.wait_permission(timeout=15)
    assert req is not None, "no permission request surfaced"
    handle.answer_permission(
        req, PermissionDecision(option_id="reject-once", reason="suite",
                                basis="human-hook"))
    assert _wait_thread(thread)
    reject_seen = any(
        getattr(e, "payload", {}).get("decision") == "reject-once"
        for e in handle.events() if "permission" in e.type)
    assert reject_seen, "no audited reject-once decision"
    handle.close(reason="done")


def face_cancel_midturn(ctx):
    backend = ctx["make_backend"](["cancel_shape"])
    handle = backend.open_session(ctx["make_spec"]("cf-cancel"))
    outcome = {}
    thread = threading.Thread(
        target=lambda: outcome.update(result=handle.prompt("count forever")),
        daemon=True)
    thread.start()
    time.sleep(0.3)                     # let the prompt start
    handle.cancel(reason="suite cancel")
    assert _wait_thread(thread), "prompt did not finish after cancel"
    lr = getattr(handle, "last_result", None)
    if lr is not None:
        assert lr.stop_reason == "cancelled", \
            f"expected cancelled, got {lr.stop_reason!r}"
    handle.close(reason="done")


def face_turn_deadline(ctx):
    """P4-F2: deterministic deadline face.

    The old version used turn_timeout=1.5s (wall-clock), which was flaky
    under system load. The fix: use a generous 3.0s timeout (the fake
    server's cancel_shape scenario never replies on its own, so the
    deadline ALWAYS fires) and assert on the TurnResult flag rather than
    racing a wall clock. Flaky criteria: flaky must be located and fixed,
    never accepted by rerunning."""
    backend = ctx["make_backend"](["cancel_shape"],
                                  overrides={"turn_timeout": 3.0})
    handle = backend.open_session(ctx["make_spec"]("cf-deadline"))
    outcome = {}
    thread = threading.Thread(
        target=lambda: outcome.update(result=handle.prompt("never ends")),
        daemon=True)
    thread.start()
    assert _wait_thread(thread, timeout=30), "deadline did not fire in time"
    lr = getattr(handle, "last_result", None)
    if lr is not None:
        assert lr.turn_timed_out is True, \
            f"expected turn_timed_out=True, got {lr.turn_timed_out!r}"
    handle.close(reason="done")


def face_close_idempotent(ctx):
    backend = ctx["make_backend"](["happy"])
    handle = backend.open_session(ctx["make_spec"]("cf-close"))
    handle.close(reason="first")
    handle.close(reason="second")       # must not raise


def face_execute_task_structured(ctx):
    import json
    import os
    spec = ctx["make_spec"]("cf-exec-ok")
    target = os.path.join(spec.cwd, "dsh_result.json")
    backend = ctx["make_backend"](
        ["writes_result", target, json.dumps({"answer": 42})])
    result = backend.execute_task(
        TaskSpec(run_id="cf-exec-ok", platform_session_id="psess-cf-exec-ok",
                 task_text="write the result", cwd=spec.cwd,
                 result_schema={"type": "object"}),
        budget=ctx.get("budget") or BudgetSpec(),
        authz=_authz())
    assert result.error is None, f"unexpected error: {result.error!r}"
    assert result.result_data == {"answer": 42}, \
        f"structured result: {result.result_data!r}"
    assert result.validation_errors == []


def face_execute_task_degrades(ctx):
    import json
    import os
    spec = ctx["make_spec"]("cf-exec-bad")
    target = os.path.join(spec.cwd, "dsh_result.json")
    backend = ctx["make_backend"](
        ["writes_result", target, json.dumps({"nope": 1})])
    result = backend.execute_task(
        TaskSpec(run_id="cf-exec-bad", platform_session_id="psess-cf-exec-bad",
                 task_text="write the result", cwd=spec.cwd,
                 result_schema={"type": "object", "properties": {
                     "answer": {"type": "number"}}, "required": ["answer"]}),
        budget=ctx.get("budget") or BudgetSpec(),
        authz=_authz())
    assert result.result_data is None, \
        f"invalid result must degrade, got {result.result_data!r}"
    assert result.validation_errors, "degraded result must carry errors"


def face_process_death_loud(ctx):
    backend = ctx["make_backend"](["die_mid_turn"])
    result = backend.execute_task(
        TaskSpec(run_id="cf-die", platform_session_id="psess-cf-die",
                 task_text="die", cwd=ctx["make_spec"]("cf-die").cwd),
        budget=ctx.get("budget") or BudgetSpec(),
        authz=_authz())
    assert result.error, "process death must surface as a LOUD error"
    assert "process" in result.error.lower() or "exit" in result.error.lower(), \
        f"error text should mention the process: {result.error!r}"


def face_bad_handshake_loud(ctx):
    backend = ctx["make_backend"](["bad_protocol"])
    try:
        backend.open_session(ctx["make_spec"]("cf-bad"))
        raise AssertionError("bad_protocol handshake must be rejected")
    except SpiError as exc:
        assert exc.kind in ("protocol", "environment"), \
            f"expected protocol/environment kind, got {exc.kind!r}"


def face_concurrent_prompt_loud(ctx):
    backend = ctx["make_backend"](["cancel_shape"])
    handle = backend.open_session(ctx["make_spec"]("cf-concurrent"))
    outcome = {}
    thread = threading.Thread(
        target=lambda: outcome.update(result=handle.prompt("first")),
        daemon=True)
    thread.start()
    time.sleep(0.3)
    try:
        handle.prompt("second — must be rejected")
        raise AssertionError("concurrent prompt must be rejected loudly")
    except SpiError as exc:
        assert exc.kind == "dispatch", f"expected dispatch, got {exc.kind!r}"
    finally:
        handle.cancel(reason="suite cleanup")
        _wait_thread(thread)
    handle.close(reason="done")


def face_unknown_tier_loud(ctx):
    backend = ctx["make_backend"](["happy"])
    spec = ctx["make_spec"]("cf-tier")
    bad = SessionSpec(run_id=spec.run_id,
                      platform_session_id=spec.platform_session_id,
                      cwd=spec.cwd, permission_mode="danger-full-access")
    try:
        backend.open_session(bad)
        raise AssertionError("danger-full-access must be rejected by the "
                             "registry closed set")
    except SpiError as exc:
        assert exc.kind == "dispatch", f"expected dispatch, got {exc.kind!r}"


FACES = {
    "open_session": face_open_session,
    "prompt_five_kinds_in_order": face_prompt_five_kinds_in_order,
    "unknown_archived_verbatim": face_unknown_archived_verbatim,
    "permission_allow": face_permission_allow,
    "permission_reject": face_permission_reject,
    "cancel_midturn": face_cancel_midturn,
    "turn_deadline": face_turn_deadline,
    "close_idempotent": face_close_idempotent,
    "execute_task_structured": face_execute_task_structured,
    "execute_task_degrades": face_execute_task_degrades,
    "process_death_loud": face_process_death_loud,
    "bad_handshake_loud": face_bad_handshake_loud,
    "concurrent_prompt_loud": face_concurrent_prompt_loud,
    "unknown_tier_loud": face_unknown_tier_loud,
}


def run_conformance(make_backend, make_spec, *,
                    budget=None, faces=None) -> dict[str, str]:
    """Run every face; returns {face_name: "PASS"|"FAIL: …"|
    "NOT-IMPLEMENTED: …"}. ``budget`` flows to faces that need a BudgetSpec;
    ``faces`` restricts the set (None = all)."""
    ctx = {"make_backend": make_backend, "make_spec": make_spec,
           "budget": budget}
    selected = faces or FACES
    results: dict[str, str] = {}
    for name, fn in selected.items():
        try:
            fn(ctx)
            results[name] = "PASS"
        except NotImplementedError as exc:
            results[name] = f"NOT-IMPLEMENTED: {exc}"
        except AssertionError as exc:
            results[name] = f"FAIL: {exc}"
        except SpiError as exc:
            results[name] = f"FAIL: SpiError[{exc.kind}] {exc}"
        except Exception as exc:            # suite must never die on one face
            results[name] = f"FAIL: {type(exc).__name__}: {exc}"
    return results
