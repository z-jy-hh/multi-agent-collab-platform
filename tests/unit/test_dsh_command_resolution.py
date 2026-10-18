"""Two real-machine finds, pinned as tests.

1. An unresolved dsh JS entry must fail FAST (environment), not hang until the
   60s initialize timeout (it used to spawn `node "" --profile acp`).
2. The version lock may be pinned per machine via DSH_EXPECTED_VERSION — it was
   already listed in command_env.allow, so honouring it is not a new capability.
"""
from __future__ import annotations

import pytest

import agent_bridge.backends.dsh as dsh
from agent_bridge.errors import SpiError
from agent_bridge.registry import load_registry


def _entry():
    return load_registry()["backends"]["dsh"]


def test_missing_bin_fails_fast_with_environment_error(monkeypatch):
    monkeypatch.delenv("DSH_BIN", raising=False)
    with pytest.raises(SpiError) as exc:
        dsh._resolve_command(_entry(), node_bin="node", dsh_bin_js=None)
    assert exc.value.kind == "environment"
    assert exc.value.code == "backend.command_unresolved"


def test_explicit_bin_resolves(monkeypatch):
    monkeypatch.delenv("DSH_BIN", raising=False)
    node, js = dsh._resolve_command(_entry(), node_bin="node",
                                    dsh_bin_js=r"C:\dsh\lib\bin.js")
    assert (node, js) == ("node", r"C:\dsh\lib\bin.js")


def test_dsh_expected_version_env_pins_the_lock(monkeypatch):
    captured = {}

    class FakeManager:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(dsh, "DshSessionManager", FakeManager)
    monkeypatch.setenv("DSH_BIN", r"C:\dsh\lib\bin.js")
    monkeypatch.setenv("DSH_EXPECTED_VERSION", "0.0.1")
    data = load_registry()
    dsh.DshBackend(data["backends"]["dsh"], defaults=data["defaults"],
                   node_bin="node")
    assert captured["expected_agent_version"] == "0.0.1"


def test_registry_lock_is_used_without_the_env(monkeypatch):
    captured = {}

    class FakeManager:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(dsh, "DshSessionManager", FakeManager)
    monkeypatch.setenv("DSH_BIN", r"C:\dsh\lib\bin.js")
    monkeypatch.delenv("DSH_EXPECTED_VERSION", raising=False)
    data = load_registry()
    dsh.DshBackend(data["backends"]["dsh"], defaults=data["defaults"],
                   node_bin="node")
    assert captured["expected_agent_version"] == "0.2.0-rc.2"


def _backend_with_fake_manager(monkeypatch, captured):
    class FakeManager:
        def resume(self, run_id, platform_session_id, dsh_session_id, *, cwd=None):
            captured.update(run_id=run_id, cwd=cwd)

    monkeypatch.setenv("DSH_BIN", r"C:\dsh\lib\bin.js")
    data = load_registry()
    backend = dsh.DshBackend(data["backends"]["dsh"], defaults=data["defaults"],
                             node_bin="node")
    monkeypatch.setattr(backend, "_manager", FakeManager())
    return backend


def test_resume_passes_the_dispatch_cwd(monkeypatch):
    # Real-machine find: the manager's resume() requires keyword-only `cwd`;
    # omitting it raised TypeError, so resume was non-functional despite
    # capabilities.supports_resume=true.
    captured = {}
    backend = _backend_with_fake_manager(monkeypatch, captured)
    backend.resume_session("run-x", "sess-y", platform_session_id="p")
    assert captured["run_id"] == "run-x"
    assert str(captured["cwd"]).replace("\\", "/").endswith(
        "runs/dsh_dispatch/run-x")


def test_resume_honours_an_explicit_cwd(monkeypatch):
    captured = {}
    backend = _backend_with_fake_manager(monkeypatch, captured)
    backend.resume_session("run-x", "sess-y", platform_session_id="p",
                           cwd=r"C:\tmp\custom")
    assert captured["cwd"] == r"C:\tmp\custom"
