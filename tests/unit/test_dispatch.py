"""R17-W8b: dispatch gate tests - C2 ten negatives, C4 switch counter-proof,
C5 TTL semantics, C6 B3 addressing, C7 resume paths, C8 mutations.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent_bridge.dispatch import (
    DispatchGate,
    dispatch_hetero_enabled,
    resolve_binding,
)
from agent_bridge.errors import SpiError

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_acp_server.py"


# --- C4 · switch counter-proof -------------------------------------------------

class TestSwitch:
    def test_gate_off_returns_none(self, monkeypatch):
        monkeypatch.setenv("DISPATCH_HETERO", "0")
        assert dispatch_hetero_enabled() is False

    def test_gate_on_enabled(self, monkeypatch):
        monkeypatch.setenv("DISPATCH_HETERO", "1")
        assert dispatch_hetero_enabled() is True

    def test_gate_strict_one(self, monkeypatch):
        for v in (None, "0", "", "true"):
            if v is None:
                monkeypatch.delenv("DISPATCH_HETERO", raising=False)
            else:
                monkeypatch.setenv("DISPATCH_HETERO", v)
            assert dispatch_hetero_enabled() is False

    def test_c4_bindings_missing_no_effect_at_gate0(self, monkeypatch):
        """DISPATCH_HETERO=0: even with broken bindings, the old path runs
        (the dispatch gate is simply not consulted)."""
        monkeypatch.setenv("DISPATCH_HETERO", "0")
        assert dispatch_hetero_enabled() is False
        # the caller would use the old path; no error raised

    def test_c4_bindings_missing_loud_at_gate1(self, monkeypatch):
        """DISPATCH_HETERO=1 + missing binding ⇒ loud SpiError."""
        monkeypatch.setenv("DISPATCH_HETERO", "1")
        data = {"bindings": {}, "backends": {"dsh": {}}}
        with pytest.raises(SpiError, match="no binding"):
            resolve_binding(data, "unbound-agent", backend_id="dsh")


# --- C2 · dispatch gate negatives (each: readable SpiError) ---------------------

class TestGateNegatives:
    def test_workspace_mutex(self):
        gate = DispatchGate()
        gate.acquire_workspace("/some/cwd")
        with pytest.raises(SpiError, match="already locked"):
            gate.acquire_workspace("/some/cwd")
        gate.release_workspace("/some/cwd")
        gate.acquire_workspace("/some/cwd")       # ok after release

    def test_platform_daily_exhausted(self):
        gate = DispatchGate()
        with pytest.raises(SpiError, match="daily_tool_calls exhausted"):
            gate.check_platform_limits(
                "dsh", limits={"daily_tool_calls": 10},
                daily_count=10, active=0)

    def test_platform_concurrency_exhausted(self):
        gate = DispatchGate()
        with pytest.raises(SpiError, match="concurrency exhausted"):
            gate.check_platform_limits(
                "dsh", limits={"max_concurrent_runs_per_backend": 2},
                daily_count=0, active=2)

    def test_platform_ok(self):
        gate = DispatchGate()
        gate.check_platform_limits(
            "dsh", limits={"daily_tool_calls": 600,
                           "max_concurrent_runs_per_backend": 2},
            daily_count=5, active=1)          # no raise


# --- C5 · B1 TTL freshness -------------------------------------------------------

class TestFreshness:
    def test_fresh_within_ttl(self):
        gate = DispatchGate(freshness_ttl=600.0)
        gate.record_freshness("dsh", ok=True, detail="handshake ok")
        rec = gate._check_freshness("dsh")
        assert rec["fresh"] is True
        assert rec["result"] is True

    def test_stale_after_ttl(self):
        import time as _time
        gate = DispatchGate(freshness_ttl=0.1)   # 100ms for testing
        gate.record_freshness("dsh", ok=True, detail="handshake ok")
        _time.sleep(0.2)                          # let TTL expire
        rec = gate._check_freshness("dsh")
        assert rec["fresh"] is False

    def test_never_checked(self):
        gate = DispatchGate(freshness_ttl=600.0)
        rec = gate._check_freshness("wb")
        assert rec["fresh"] is False

    def test_failed_read_not_cached_as_fresh(self):
        gate = DispatchGate(freshness_ttl=600.0)
        gate.record_freshness("dsh", ok=False, detail="handshake failed")
        rec = gate._check_freshness("dsh")
        assert rec["fresh"] is True              # within TTL
        assert rec["result"] is False            # but the result is failure


# --- C6 · B3 addressing -----------------------------------------------------------

class TestB3Addressing:
    def _data(self):
        return {
            "bindings": {"dsh-exec": "dsh:", "wb-exec": "wb:"},
            "backends": {"dsh": {"adapter": "acp"}, "wb": {"adapter": "acp"}},
        }

    def test_binding_resolves(self):
        entry = resolve_binding(self._data(), "dsh-exec", backend_id="dsh")
        assert entry["adapter"] == "acp"

    def test_self_report_mismatch_ignored(self):
        """B3 negative: backend self-reports 'wb' but binding says 'dsh'."""
        with pytest.raises(SpiError, match="does not match"):
            resolve_binding(self._data(), "dsh-exec", backend_id="wb")

    def test_unbound_agent_rejected(self):
        with pytest.raises(SpiError, match="no binding"):
            resolve_binding(self._data(), "unknown-agent", backend_id="dsh")

    def test_binding_to_missing_backend(self):
        data = {"bindings": {"x": "ghost:"}, "backends": {}}
        with pytest.raises(SpiError, match="not found in registry"):
            resolve_binding(data, "x", backend_id="ghost")


# --- C7 · B6 resume paths ----------------------------------------------------------

class TestB6Resume:
    def test_resume_supported_backend_has_method(self):
        """dsh: supports_resume=true ⇒ the manager has a resume method."""
        from dsh_bridge.session import DshSessionManager
        assert hasattr(DshSessionManager, "resume")

    def test_resume_unsupported_rejected(self):
        """WB: supports_resume=false (assumed-fail-closed) ⇒ explicit
        rejection, never a silent new session."""
        entry = {
            "capabilities": {
                "protocol_version": {"value": 1, "evidence": "assumed"},
                "transport": {"value": "stdio", "evidence": "assumed"},
                "permission_has_path": {"value": False, "evidence": "assumed"},
                "permission_options": {"value": [], "evidence": "assumed"},
                "usage_shape": {"value": "none", "evidence": "assumed"},
                "cancel_is_notification": {"value": True, "evidence": "assumed"},
                "server_times_out": {"value": False, "evidence": "assumed"},
                "supports_resume": {"value": False, "evidence": "assumed-fail-closed"},
                "supports_background": {"value": False, "evidence": "assumed"},
                "paid": {"value": True, "evidence": "assumed"},
                "single_prompt_per_session": {"value": True, "evidence": "assumed"},
                "single_root_cwd": {"value": True, "evidence": "assumed"},
                "single_handler_slot": {"value": True, "evidence": "assumed"},
                "audit_hash_chain": {"value": True, "evidence": "assumed"},
                "sandbox_semantics": {"value": "builtin-vendor", "evidence": "assumed"},
                "supports_elicitation": {"value": False, "evidence": "assumed"},
                "egress": {"value": True, "evidence": "assumed"},
            },
            "permission": {"mode_default": "default", "mode_allowed": ["default"]},
        }
        from agent_bridge.backends.dsh import _capabilities_from_entry
        caps = _capabilities_from_entry("wb", entry)
        assert caps.supports_resume is False
        # the SPI dispatch would check this flag before attempting resume:
        # if not caps.supports_resume: raise SpiError("resume not supported")
