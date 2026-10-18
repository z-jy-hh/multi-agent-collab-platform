"""W3-2: declared `modes` are arbitrated before the backend is built.

A declared-but-unsupported mode must SKIP loudly (no-common-mode); a supported
one must be recorded (`mode_negotiated`) and the backend still executes.
"""
from __future__ import annotations

from agent_bridge.hetero_dispatch import dispatch_bound_leaves
from agent_bridge.spi import TaskResult

from tests.unit.test_hetero_dispatch import FakeBackend, FakeBus, _registry


def _registry_with_modes(modes):
    data = _registry()
    data["backends"]["dsh"]["modes"] = modes
    return data


def _patch(monkeypatch):
    monkeypatch.setattr("agent_bridge.hetero_dispatch._default_store", lambda: object())
    calls = {}

    def fake_build(backend_id, *, store=None, emit=None, overrides=None):
        calls["built"] = calls.get("built", 0) + 1
        return FakeBackend()

    monkeypatch.setattr("agent_bridge.factory.build_backend", fake_build)
    return calls


def test_supported_mode_is_recorded_and_backend_runs(monkeypatch):
    calls = _patch(monkeypatch)
    bus = FakeBus()
    dispatch_bound_leaves(run_id="run-1", session_id=None, task_text="t", bus=bus,
                          registry_data=_registry_with_modes(
                              [{"adapter": "acp", "transport": "stdio",
                                "protocol_version": 1}]))
    assert "mode_negotiated" in bus.types()
    assert calls.get("built") == 1, "a negotiated backend must still execute"


def test_unsupported_mode_skips_without_building(monkeypatch):
    calls = _patch(monkeypatch)
    bus = FakeBus()
    dispatch_bound_leaves(run_id="run-1", session_id=None, task_text="t", bus=bus,
                          registry_data=_registry_with_modes(
                              [{"adapter": "acp", "transport": "streamable-http",
                                "protocol_version": 1}]))
    skipped = [p for t, p in bus.events if t == "hetero_dispatch_skipped"]
    # other bindings may be skipped for unrelated reasons (e.g. wb disabled),
    # so look for OUR reason rather than assuming event order.
    assert any(p.get("reason") == "no-common-mode" for p in skipped)
    ours = next(p for p in skipped if p.get("reason") == "no-common-mode")
    assert ours["backend_id"] == "dsh"
    assert ours["supported"] and ours["declared"]
    assert calls.get("built") is None, "must not build a backend we cannot talk to"
    assert "mode_negotiated" not in bus.types()


def test_no_modes_declared_keeps_the_old_path(monkeypatch):
    calls = _patch(monkeypatch)
    bus = FakeBus()
    dispatch_bound_leaves(run_id="run-1", session_id=None, task_text="t", bus=bus,
                          registry_data=_registry())
    assert "mode_negotiated" not in bus.types()
    assert calls.get("built") == 1
