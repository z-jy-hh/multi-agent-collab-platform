"""G12: the shipped registry must dispatch each mapped agent exactly once.

Before this change `bindings` carried placeholder agents (dsh-exec/wb-exec)
that ALWAYS dispatched, while routing.map dispatched the real agent_id — so a
single run opened two identical dsh sessions.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_bridge.dispatch_hooks import (bound_backends, pre_run_gate,
                                         release_run_gate)
from agent_bridge.errors import SpiError
from agent_bridge.hetero_dispatch import dispatch_bound_leaves
from agent_bridge.registry import load_registry

from tests.unit.test_hetero_dispatch import FakeBackend, FakeBus

REPO = Path(__file__).resolve().parents[2]


def test_placeholder_bindings_are_gone():
    data = load_registry()
    assert data.get("bindings") == {}, \
        "placeholder *-exec bindings must not remain (duplicate dispatch)"


def test_routing_map_targets_are_real_agent_cards():
    cards_path = REPO / "configs" / "agents.yaml"
    if (REPO / ".layercheck_scope").exists():
        # Only the trimmed layercheck copy may skip (configs/ is upper-layer).
        pytest.skip("trimmed layercheck copy: configs/ is intentionally absent")
    if not cards_path.exists():
        pytest.fail("configs/agents.yaml is missing in the repo — "
                    "the routing.map guard cannot run (do not skip this)")
    data = load_registry()
    cards = {a["agent_id"] for a in
             yaml.safe_load(cards_path.read_text(
                 encoding="utf-8"))["agents"]}
    route_map = (data.get("routing") or {}).get("map") or {}
    # An empty map would make this loop vacuous (the guard must not pass by
    # iterating zero keys: that is exactly how the mapping could be deleted).
    assert route_map, "routing.map must not be empty (this guard would be vacuous)"
    for agent_id in route_map:
        assert agent_id in cards, f"routing.map key {agent_id!r} is not a real agent card"


def test_bound_backends_includes_routing_map():
    # Mutation guard: deleting the routing.map loop from bound_backends must
    # fail here (the gate would then see an empty bound set).
    data = {"backends": {"dsh": {"enabled": True}}, "bindings": {},
            "routing": {"map": {"researcher": "dsh"}}}
    assert bound_backends(data) == {"researcher": "dsh"}


def test_bound_backends_keeps_legacy_bindings():
    data = {"backends": {"dsh": {"enabled": True}}, "bindings": {"old": "dsh:"},
            "routing": {"map": {}}}
    assert bound_backends(data) == {"old": "dsh"}


def test_one_dispatch_per_mapped_agent(monkeypatch):
    monkeypatch.setattr("agent_bridge.hetero_dispatch._default_store", lambda: object())
    monkeypatch.setattr("agent_bridge.factory.build_backend",
                        lambda *a, **kw: FakeBackend())
    bus = FakeBus()
    dispatch_bound_leaves(run_id="run-1", session_id=None, task_text="t", bus=bus,
                          registry_data=load_registry())

    started = [p for t, p in bus.events if t == "hetero_dispatch_started"]
    assert len(started) == 1, f"expected exactly one dispatch, got {started}"
    assert started[0]["agent_id"] == "researcher"


def test_pre_run_gate_refuses_when_the_mapped_backend_is_disabled(monkeypatch):
    monkeypatch.setenv("AGENT_REGISTRY", "1")
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    data = load_registry()
    data["backends"]["dsh"]["enabled"] = False
    with pytest.raises(SpiError) as exc:
        pre_run_gate("supervisor", registry_data=data)
    assert "no bound backend is enabled" in str(exc.value)

