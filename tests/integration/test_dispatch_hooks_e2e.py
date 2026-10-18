"""R17-P4FIX-3 e2e: the pre-run gate through the REAL create_run entry.

These tests exercise the full stack (api + gate) and are excluded from
layercheck's trimmed env (which deliberately lacks upper layers like api/).

Why this file was rewritten: the P4FIX-2 version seeded freshness for ``dsh``
only while the registry also binds ``wb``, and it relied on monkeypatching
``agent_bridge.dispatch.DispatchGate`` while the hook imported the class at
module level - so the patch never applied and the test failed deterministically
in the suite. Both causes are fixed: the hook imports the class inside the
function, the gate only considers bound+ENABLED backends, and the failing
conditions here are synthetic (caps / health read) instead of cache seeding.
"""
from __future__ import annotations

import pytest

from agent_bridge.errors import SpiError


def _snap(daily=0, hourly=0, active=None):
    return {"day": "d", "daily": daily, "hour": "h", "hourly": hourly,
            "active": active or {}}


class TestE2ECreateRunGate:
    def test_e2e_gate_off_run_created(self, monkeypatch, tmp_path):
        """DISPATCH_HETERO=0: create_run succeeds even with the gate booby-trapped."""
        monkeypatch.setenv("DISPATCH_HETERO", "0")
        monkeypatch.setenv("DSH_DISPATCH_ROOT", str(tmp_path))
        monkeypatch.setattr(
            "agent_bridge.dispatch.DispatchGate",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("GATE CONSULTED at DISPATCH_HETERO=0")))
        from api.routes import create_run, RunRequest
        resp = create_run(RunRequest(mode="single", task="test gate off", llm="fake"))
        assert "run_id" in resp

    def test_e2e_gate_on_cap_exhausted_refused(self, monkeypatch, tmp_path):
        """DISPATCH_HETERO=1 + daily cap exhausted (real counter path) => refused."""
        monkeypatch.setenv("DISPATCH_HETERO", "1")
        monkeypatch.setenv("DSH_DISPATCH_ROOT", str(tmp_path))
        monkeypatch.setattr("agent_bridge.gate_counters.snapshot", lambda: _snap(daily=600))
        from api.routes import create_run, RunRequest
        with pytest.raises(SpiError, match="daily_tool_calls exhausted"):
            create_run(RunRequest(mode="single", task="test gate on", llm="fake"))

    def test_e2e_gate_on_healthy_run_created(self, monkeypatch, tmp_path):
        """DISPATCH_HETERO=1 + healthy (synthetic read ok) => run created.

        The hetero leaf dispatch itself is stubbed: this test is about the gate
        being SATISFIABLE by the platform (P4FIX-2 finding F2), not about
        spawning a real backend inside the API test.
        """
        monkeypatch.setenv("DISPATCH_HETERO", "1")
        monkeypatch.setenv("DSH_DISPATCH_ROOT", str(tmp_path))
        monkeypatch.setattr("agent_bridge.gate_counters.snapshot", lambda: _snap())
        monkeypatch.setattr("agent_bridge.health.health_read",
                            lambda backend_id, **kw: (True, "synthetic read ok"))
        monkeypatch.setattr("agent_bridge.hetero_dispatch.dispatch_bound_leaves",
                            lambda **kw: {})
        from agent_bridge.dispatch import DispatchGate
        DispatchGate._shared_freshness.clear()
        DispatchGate._shared_workspace_locks.clear()
        from api.routes import create_run, RunRequest
        resp = create_run(RunRequest(mode="single", task="test gate healthy", llm="fake"))
        assert "run_id" in resp
