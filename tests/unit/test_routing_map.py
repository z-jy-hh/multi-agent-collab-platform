"""G12: `routing.map` binds REAL agent_ids (the 9 cards) to backends.

Additive: legacy `bindings` keep working, and an unknown backend is refused by
lint (fail-closed) rather than silently dropped.
"""
from __future__ import annotations

import copy

import pytest

from agent_bridge.hetero_dispatch import dispatch_bound_leaves
from agent_bridge.registry import lint_registry, load_registry

from tests.unit.test_hetero_dispatch import FakeBackend, FakeBus, _registry


def _base():
    return copy.deepcopy(load_registry())


def test_lint_accepts_a_valid_routing_map():
    data = _base()
    data["routing"]["map"] = {"researcher": "dsh"}
    assert lint_registry(data) == []


def test_lint_rejects_an_undeclared_backend_in_the_map():
    data = _base()
    data["routing"]["map"] = {"researcher": "nope"}
    assert any("routing.map.researcher" in e for e in lint_registry(data))


def test_lint_rejects_a_non_mapping_map():
    data = _base()
    data["routing"]["map"] = ["researcher", "dsh"]
    assert any("routing.map" in e for e in lint_registry(data))


def test_dispatch_uses_the_routing_map_for_real_agent_ids(monkeypatch):
    calls = {}

    def fake_build(backend_id, *, store=None, emit=None, overrides=None):
        calls["built"] = calls.get("built", 0) + 1
        return FakeBackend()

    monkeypatch.setattr("agent_bridge.hetero_dispatch._default_store", lambda: object())
    monkeypatch.setattr("agent_bridge.factory.build_backend", fake_build)

    data = _registry()
    data["routing"] = dict(data.get("routing") or {})
    data["routing"]["map"] = {"researcher": "dsh"}
    bus = FakeBus()
    dispatch_bound_leaves(run_id="run-1", session_id=None, task_text="t",
                          bus=bus, registry_data=data)

    dispatched = {p.get("agent_id") for t, p in bus.events
                  if t in ("hetero_dispatch_started", "hetero_dispatch_result")}
    assert "researcher" in dispatched, "the real agent_id must be dispatched"
