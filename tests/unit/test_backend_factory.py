"""R17-W5c: factory gate semantics - the EXECUTABLE counter-proof for C4.

Proves (not asserts) that ``AGENT_REGISTRY=0`` keeps the registry file
UNREAD and the backend UNCONSTRUCTED, while ``=1`` runs the full SPI path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent_bridge.errors import SpiError
from agent_bridge.factory import build_backend, registry_gate
from agent_bridge.registry import RegistryError

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_acp_server.py"


@pytest.fixture()
def _clean_gate(monkeypatch):
    monkeypatch.setenv("AGENT_REGISTRY", "0")
    yield


def test_gate_is_strictly_one(monkeypatch):
    for value in (None, "0", "", "true", "yes", " 1"):
        if value is None:
            monkeypatch.delenv("AGENT_REGISTRY", raising=False)
        else:
            monkeypatch.setenv("AGENT_REGISTRY", value)
        assert registry_gate() is False
    monkeypatch.setenv("AGENT_REGISTRY", "1")
    assert registry_gate() is True


def test_c4_gate_off_no_read_no_build(tmp_path, monkeypatch):
    """C4 TRIPWIRE: at gate 0 neither the registry's file-read point nor the
    backend constructor may fire - even with a PERFECT registry on disk."""
    def boom_read(*_a, **_k):
        raise AssertionError("registry file was READ at gate 0")
    def boom_build(*_a, **_k):
        raise AssertionError("backend was CONSTRUCTED at gate 0")
    monkeypatch.setattr("agent_bridge.registry._read_yaml", boom_read)
    monkeypatch.setattr("agent_bridge.backends.dsh.DshBackend", boom_build)
    monkeypatch.setenv("AGENT_REGISTRY", "0")
    result = build_backend("dsh")
    assert result is None               # caller falls back to the old path


def test_c4_gate_on_reads_and_builds(tmp_path, monkeypatch):
    """C4 mirror: gate 1 DOES read the registry and construct the backend."""
    monkeypatch.setenv("AGENT_REGISTRY", "1")
    called = {"read": False}
    real_read = __import__("agent_bridge.registry",
                           fromlist=["_read_yaml"])._read_yaml

    def spy_read(path):
        called["read"] = True
        return real_read(path)

    monkeypatch.setattr("agent_bridge.registry._read_yaml", spy_read)
    backend = build_backend("dsh", overrides={
        "node_bin": sys.executable, "dsh_bin_js": str(FAKE),
        "dispatch_root": tmp_path / "dispatch"})
    assert called["read"] is True
    assert backend is not None
    assert backend.backend_id == "dsh"
    backend.shutdown()


def test_c4_gate_on_invalid_registry_loud(tmp_path, monkeypatch):
    """Gate 1 + a broken registry ⇒ the failure is LOUD (RegistryError), and
    the broken file was actually parsed (not silently skipped)."""
    monkeypatch.setenv("AGENT_REGISTRY", "1")
    monkeypatch.setattr("agent_bridge.registry.default_registry_path",
                        lambda: tmp_path / "bad.yaml")
    (tmp_path / "bad.yaml").write_text(
        "schema: agent_bridge.registry/v1\nbackends: {}\n"
        "platform_limits: {consent: off}\n", encoding="utf-8")
    with pytest.raises(RegistryError):
        build_backend("dsh")


def test_c5_egress_missing_rejected_at_factory(tmp_path, monkeypatch):
    """C5: a registry whose dsh entry lacks the egress flag must be rejected
    by the lint BEFORE any backend construction (factory chain e2e)."""
    monkeypatch.setenv("AGENT_REGISTRY", "1")
    import yaml
    registry_file = tmp_path / "registry.yaml"
    data = yaml.safe_load(
        Path("agents/registry.yaml").read_text(encoding="utf-8"))
    del data["backends"]["dsh"]["capabilities"]["egress"]
    registry_file.write_text(yaml.safe_dump(data, allow_unicode=True),
                             encoding="utf-8")
    monkeypatch.setattr("agent_bridge.registry.default_registry_path",
                        lambda: registry_file)
    with pytest.raises(RegistryError) as excinfo:
        build_backend("dsh")
    assert any("egress" in e for e in excinfo.value.errors), \
        f"expected an egress error, got {excinfo.value.errors}"


def test_c5_unknown_backend_id_loud(monkeypatch):
    monkeypatch.setenv("AGENT_REGISTRY", "1")
    with pytest.raises(SpiError, match="not in registry"):
        build_backend("nonexistent")
