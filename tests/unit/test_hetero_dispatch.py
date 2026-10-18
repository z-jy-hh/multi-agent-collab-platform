"""R17-P4FIX-3 (dsh): per-subtask heterogeneous dispatch tests.

The point of this module is that ``execute_task`` is REALLY called: a fake
backend injected through the factory records the call, the result is emitted as
a run event, disabled backends are skipped LOUDLY, and a failing backend is
reported instead of being swallowed.
"""
from __future__ import annotations

import pytest

from agent_bridge.errors import SpiError
from agent_bridge.hetero_dispatch import dispatch_bound_leaves
from agent_bridge.spi import TaskResult


class FakeBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def emit(self, type_: str, run_id: str, payload: dict, agent_id=None):  # noqa: A002
        self.events.append((type_, payload or {}))

    def types(self) -> list[str]:
        return [t for t, _ in self.events]


class FakeBackend:
    backend_id = "dsh"

    def __init__(self, *, result=None, raise_exc=None):
        self.calls: list[dict] = []
        self._result = result or TaskResult(stop_reason="end_turn", final_text="leaf done",
                                            audit_refs={"op_ids": ["op-1"]})
        self._raise = raise_exc

    def execute_task(self, task, *, budget, authz):
        self.calls.append({"task": task, "budget": budget, "authz": authz})
        if self._raise:
            raise self._raise
        return self._result


def _registry(dsh_enabled=True, wb_enabled=False):
    return {
        "backends": {
            "dsh": {"enabled": dsh_enabled, "cwd": {"template": "runs/dsh_dispatch/{run_id}"}},
            "wb": {"enabled": wb_enabled, "cwd": {"template": "runs/wb_dispatch/{run_id}"}},
        },
        "bindings": {"dsh-exec": "dsh:", "wb-exec": "wb:"},
        "defaults": {"deadline_prompt_s": 5.0},
    }


@pytest.fixture(autouse=True)
def _isolate_counters(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_DISPATCH_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_CONSENT_TOKEN", raising=False)
    monkeypatch.delenv("DISPATCH_DATA_OPT_IN", raising=False)
    yield


def test_bound_leaf_is_really_dispatched(monkeypatch):
    fake = FakeBackend()
    monkeypatch.setattr("agent_bridge.factory.build_backend", lambda *a, **k: fake)
    bus = FakeBus()
    results = dispatch_bound_leaves(run_id="run-1", session_id="sess-1", task_text="do it",
                                    bus=bus, registry_data=_registry())
    assert "dsh-exec" in results
    assert fake.calls and fake.calls[0]["task"].task_text == "do it"
    assert fake.calls[0]["authz"].consent_token is None      # default: no consent
    assert "hetero_dispatch_started" in bus.types()
    assert "hetero_dispatch_result" in bus.types()
    payload = dict(bus.events)[ "hetero_dispatch_result"]
    assert payload["backend_id"] == "dsh" and payload["final_text"] == "leaf done"
    # counters moved (the pre-run cap check reads them)
    from agent_bridge import gate_counters
    assert gate_counters.snapshot()["daily"] >= 1
    assert gate_counters.snapshot()["active"] == {}


def test_disabled_backend_is_skipped_loudly(monkeypatch):
    monkeypatch.setattr("agent_bridge.factory.build_backend",
                        lambda *a, **k: pytest.fail("must not construct a disabled backend"))
    bus = FakeBus()
    results = dispatch_bound_leaves(run_id="run-1", session_id=None, task_text="t",
                                    bus=bus, registry_data=_registry(dsh_enabled=False))
    assert results == {}
    skipped = [p for t, p in bus.events if t == "hetero_dispatch_skipped"]
    assert len(skipped) == 2                                  # dsh + wb both disabled


def test_failing_leaf_is_reported_not_swallowed(monkeypatch):
    fake = FakeBackend(raise_exc=SpiError("boom", kind="dispatch", backend="dsh"))
    monkeypatch.setattr("agent_bridge.factory.build_backend", lambda *a, **k: fake)
    bus = FakeBus()
    results = dispatch_bound_leaves(run_id="run-1", session_id=None, task_text="t",
                                    bus=bus, registry_data=_registry())
    assert results == {}
    errors = [p for t, p in bus.events if t == "hetero_dispatch_error"]
    assert errors and "boom" in errors[0]["error"]
    from agent_bridge import gate_counters
    assert gate_counters.snapshot()["active"] == {}            # released in finally


def test_strict_mode_propagates(monkeypatch):
    fake = FakeBackend(raise_exc=SpiError("boom", kind="dispatch", backend="dsh"))
    monkeypatch.setattr("agent_bridge.factory.build_backend", lambda *a, **k: fake)
    with pytest.raises(SpiError, match="boom"):
        dispatch_bound_leaves(run_id="run-1", session_id=None, task_text="t",
                              bus=FakeBus(), registry_data=_registry(), strict=True)


def test_one_store_per_call_and_dispatch_root_override(monkeypatch):
    """P4FIX-4 (reviewer S2/S3): the op store is built ONCE per call and the
    backend receives the same dispatch root the gate/counters use."""
    calls = {"store": 0, "overrides": []}

    def fake_store():
        calls["store"] += 1
        return object()

    def fake_build(backend_id, *, store=None, emit=None, overrides=None):
        calls["overrides"].append(overrides or {})
        calls["emit"] = emit
        return FakeBackend()

    monkeypatch.setattr("agent_bridge.hetero_dispatch._default_store", fake_store)
    monkeypatch.setattr("agent_bridge.factory.build_backend", fake_build)
    bus = FakeBus()
    dispatch_bound_leaves(run_id="run-1", session_id=None, task_text="t",
                          bus=bus, registry_data=_registry())
    assert calls["store"] == 1, "store must be constructed once per call"
    assert calls["overrides"], "backend must receive overrides"
    # M2: the backend must get a working emit so permission/session events
    # reach the bus (regression guard for the no-op fallback).
    assert callable(calls.get("emit")), "backend must receive emit (M2)"
    calls["emit"]("spi_permission_request", {"tool_call_id": "tc-1"})
    assert "spi_permission_request" in bus.types(), "emit must reach the event bus"
    perm_payload = [p for t, p in bus.events if t == "spi_permission_request"][0]
    assert perm_payload.get("agent_id") == "dsh-exec", \
        "the permission request must carry the asking agent (else the card mounts in __flow__)"
    assert perm_payload.get("backend_id") == "dsh"
    from agent_bridge.roots import dispatch_root
    assert calls["overrides"][0].get("dispatch_root") == str(dispatch_root())


def test_error_event_carries_traceback_tail(monkeypatch):
    """P4FIX-4 (reviewer S8): the error payload keeps a traceback tail."""
    fake = FakeBackend(raise_exc=SpiError("boom", kind="dispatch", backend="dsh"))
    monkeypatch.setattr("agent_bridge.factory.build_backend", lambda *a, **k: fake)
    bus = FakeBus()
    dispatch_bound_leaves(run_id="run-1", session_id=None, task_text="t",
                          bus=bus, registry_data=_registry())
    payload = [p for t, p in bus.events if t == "hetero_dispatch_error"][0]
    assert payload.get("traceback_tail"), "traceback tail missing"
