"""R17-P4FIX-3 (dsh): pre-run gate tests - real checks, satisfiable, and loud.

Covers: gate-off no-op; healthy gate-on pass (the platform performs the B1
read itself); usage-cap refusal (daily/hourly/concurrency) from REAL counters;
health-read failure refusal; workspace mutex shared across gate instances;
token release; and the lazy-import property that makes monkeypatch tripwires
possible (a module-level import defeated them once - P4FIX-2 e2e failure).
"""
from __future__ import annotations

import pytest

from agent_bridge.dispatch import DispatchGate, dispatch_hetero_enabled
from agent_bridge.dispatch_hooks import dispatch_root_for, pre_run_gate, release_run_gate
from agent_bridge.errors import SpiError


@pytest.fixture(autouse=True)
def _clean_gate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_DISPATCH_ROOT", str(tmp_path))
    DispatchGate._shared_freshness.clear()
    DispatchGate._shared_workspace_locks.clear()
    yield
    DispatchGate._shared_freshness.clear()
    DispatchGate._shared_workspace_locks.clear()


def _registry(daily=600, hourly=120, conc=2, dsh_enabled=True):
    return {
        "platform_limits": {"daily_tool_calls": daily, "hourly_tool_calls": hourly,
                            "max_concurrent_runs_per_backend": conc},
        "backends": {"dsh": {"enabled": dsh_enabled,
                             "cwd": {"template": "runs/dsh_dispatch/{run_id}"}},
                     "wb": {"enabled": False,
                            "cwd": {"template": "runs/wb_dispatch/{run_id}"}}},
        "bindings": {"dsh-exec": "dsh:", "wb-exec": "wb:"},
    }


def _snap(daily=0, hourly=0, active=None):
    return {"day": "d", "daily": daily, "hour": "h", "hourly": hourly,
            "active": active or {}}


def test_gate_off_is_noop(monkeypatch):
    monkeypatch.setenv("DISPATCH_HETERO", "0")
    assert pre_run_gate("single", run_id="r1", registry_data=_registry()) is None
    assert dispatch_hetero_enabled() is False


def test_gate_on_healthy_performs_read_and_locks(monkeypatch):
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setattr("agent_bridge.gate_counters.snapshot", lambda: _snap())
    monkeypatch.setattr("agent_bridge.health.health_read",
                        lambda backend_id, **kw: (True, "synthetic read ok"))
    token = pre_run_gate("single", run_id="r1", registry_data=_registry())
    assert token is not None
    assert token["backends"] == ["dsh"]              # wb disabled -> not gated
    assert len(token["locks"]) == 1
    assert token["reads"][0]["read"] == "performed"
    release_run_gate(token)                          # free the workspace mutex
    token2 = pre_run_gate("single", run_id="r2", registry_data=_registry())
    assert token2["reads"][0]["read"] == "cached"    # TTL reuse
    release_run_gate(token2)


def test_gate_on_daily_cap_exhausted_refuses(monkeypatch):
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setattr("agent_bridge.gate_counters.snapshot", lambda: _snap(daily=600))
    with pytest.raises(SpiError, match="daily_tool_calls exhausted"):
        pre_run_gate("single", run_id="r1", registry_data=_registry())


def test_gate_on_hourly_cap_exhausted_refuses(monkeypatch):
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setattr("agent_bridge.gate_counters.snapshot", lambda: _snap(hourly=120))
    with pytest.raises(SpiError, match="hourly_tool_calls exhausted"):
        pre_run_gate("single", run_id="r1", registry_data=_registry())


def test_gate_on_concurrency_exhausted_refuses(monkeypatch):
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setattr("agent_bridge.gate_counters.snapshot",
                        lambda: _snap(active={"dsh": 2}))
    with pytest.raises(SpiError, match="concurrency exhausted"):
        pre_run_gate("single", run_id="r1", registry_data=_registry())


def test_gate_on_health_read_failure_refuses(monkeypatch):
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setattr("agent_bridge.gate_counters.snapshot", lambda: _snap())
    monkeypatch.setattr("agent_bridge.health.health_read",
                        lambda backend_id, **kw: (False, "synthetic read failure"))
    with pytest.raises(SpiError, match="failed its pre-dispatch health read"):
        pre_run_gate("single", run_id="r1", registry_data=_registry())


def test_gate_on_no_enabled_bound_backend_refuses(monkeypatch):
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setattr("agent_bridge.gate_counters.snapshot", lambda: _snap())
    with pytest.raises(SpiError, match="no bound backend is enabled"):
        pre_run_gate("single", run_id="r1", registry_data=_registry(dsh_enabled=False))


def test_workspace_mutex_is_shared_across_gate_instances(monkeypatch):
    """The pre-run gate and the releaser are different instances, so the lock
    must be module-level: a second run is refused while the first holds it."""
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setattr("agent_bridge.gate_counters.snapshot", lambda: _snap())
    monkeypatch.setattr("agent_bridge.health.health_read",
                        lambda backend_id, **kw: (True, "ok"))
    token = pre_run_gate("single", run_id="r1", registry_data=_registry())
    with pytest.raises(SpiError, match="already locked"):
        pre_run_gate("single", run_id="r2", registry_data=_registry())
    release_run_gate(token)
    token2 = pre_run_gate("single", run_id="r3", registry_data=_registry())
    release_run_gate(token2)


def test_gate_imports_dispatchgate_lazily_for_tripwires(monkeypatch):
    """A monkeypatch on the dispatch module's class MUST be picked up."""
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setattr(
        "agent_bridge.dispatch.DispatchGate",
        lambda *a, **k: (_ for _ in ()).throw(
            SpiError("PATCHED GATE", kind="dispatch", backend="gate")))
    with pytest.raises(SpiError, match="PATCHED GATE"):
        pre_run_gate("single", run_id="r1", registry_data=_registry())


# --- P4FIX-4: reviewer findings S1 / S5 / S6 -------------------------------

def _two_backend_registry():
    data = _registry()
    data["backends"]["wb"] = {"enabled": True,
                              "cwd": {"template": "runs/wb_dispatch/{run_id}"}}
    return data


def test_partial_lock_failure_does_not_leak(monkeypatch):
    """S1: if a later backend's lock fails, earlier locks must be released."""
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setattr("agent_bridge.gate_counters.snapshot", lambda: _snap())
    monkeypatch.setattr("agent_bridge.health.health_read", lambda backend_id, **kw: (True, "ok"))
    gate = DispatchGate()
    # pre-lock the SECOND backend's workspace so the loop fails after locking dsh
    wb_cwd = dispatch_root_for("wb", _two_backend_registry())
    gate.acquire_workspace(wb_cwd)
    try:
        with pytest.raises(SpiError, match="already locked"):
            pre_run_gate("single", run_id="r1", registry_data=_two_backend_registry())
        dsh_cwd = dispatch_root_for("dsh", _two_backend_registry())
        assert dsh_cwd not in DispatchGate._shared_workspace_locks, "S1: dsh lock leaked"
    finally:
        gate.release_workspace(wb_cwd)


def test_corrupt_counter_file_refuses(monkeypatch, tmp_path):
    """S6: an unreadable counter file must not mean 'zero usage'."""
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setenv("DSH_DISPATCH_ROOT", str(tmp_path))
    (tmp_path / "gate_counters.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr("agent_bridge.health.health_read", lambda backend_id, **kw: (True, "ok"))
    with pytest.raises(SpiError, match="counter file is unreadable/corrupt"):
        pre_run_gate("single", run_id="r1", registry_data=_registry())
    monkeypatch.setenv("DISPATCH_COUNTERS_ALLOW_CORRUPT", "1")
    token = pre_run_gate("single", run_id="r2", registry_data=_registry())
    assert token is not None
    release_run_gate(token)


def test_cross_process_workspace_lock_is_enforced(monkeypatch, tmp_path):
    """S5: a lock held by ANOTHER PROCESS must refuse this one."""
    import subprocess
    import sys

    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setenv("DSH_DISPATCH_ROOT", str(tmp_path))
    cwd = dispatch_root_for("dsh", _registry())
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import msvcrt,os,sys,time;"
         "p=sys.argv[1];os.makedirs(p,exist_ok=True);"
         "fd=os.open(os.path.join(p,'.gate.lock'),os.O_RDWR|os.O_CREAT);"
         "os.lseek(fd,0,0);msvcrt.locking(fd,msvcrt.LK_NBLCK,1);"
         "print('LOCKED',flush=True);time.sleep(4)",
         cwd],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "LOCKED"
        gate = DispatchGate()
        with pytest.raises(SpiError, match="locked by another PROCESS"):
            gate.acquire_workspace(cwd)
    finally:
        holder.kill()
        holder.wait(timeout=10)
        DispatchGate._shared_workspace_locks.clear()


def test_two_backends_sharing_a_workspace_is_reported_clearly(monkeypatch):
    """P4FIX-4 reconciliation: an identical cwd template must fail with an
    explicit 'share the workspace' message, not a confusing 'already locked'
    (that confusion made an S1 probe look like a lock leak)."""
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    monkeypatch.setattr("agent_bridge.gate_counters.snapshot", lambda: _snap())
    monkeypatch.setattr("agent_bridge.health.health_read", lambda backend_id, **kw: (True, "ok"))
    data = _registry()
    data["backends"]["wb"] = {"enabled": True,
                              "cwd": {"template": "runs/dsh_dispatch/{run_id}"}}
    with pytest.raises(SpiError, match="share the workspace"):
        pre_run_gate("single", run_id="r1", registry_data=data)


def test_registry_lint_rejects_duplicate_cwd_templates():
    """The misconfiguration is caught at lint time, before any run."""
    from agent_bridge.registry import SCHEMA_ID, lint_registry
    data = {
        "schema": SCHEMA_ID,
        "platform_limits": {"consent": "required", "daily_tool_calls": 10,
                            "hourly_tool_calls": 5, "daily_wall_s": 60,
                            "max_concurrent_runs_per_backend": 1},
        "backends": {
            "a": {"enabled": True, "cwd": {"template": "runs/shared/{run_id}"}},
            "b": {"enabled": True, "cwd": {"template": "runs/shared/{run_id}"}},
        },
        "bindings": {},
    }
    errs = lint_registry(data)
    assert any("E-cwd-duplicate" in e for e in errs), errs
