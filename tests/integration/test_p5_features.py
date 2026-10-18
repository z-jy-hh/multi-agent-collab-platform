"""R17-W9/P0: P5 feature tests — api-dependent, excluded from layercheck."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from api.events import _should_emit_event, dsh_acp_enabled
from agent_bridge.errors import SpiError
from agent_bridge.spi import PermissionRequest


class TestDshAcpEnabled:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("DSH_ACP_ENABLED", raising=False)
        assert dsh_acp_enabled() is False

    def test_on_only_when_one(self, monkeypatch):
        for v in ("0", "", "true", "yes", " 1"):
            monkeypatch.setenv("DSH_ACP_ENABLED", v)
            assert dsh_acp_enabled() is False
        monkeypatch.setenv("DSH_ACP_ENABLED", "1")
        assert dsh_acp_enabled() is True

    def test_filter_blocks_dsh_events_when_off(self, monkeypatch):
        monkeypatch.setenv("DSH_ACP_ENABLED", "0")
        assert _should_emit_event("dsh_message_chunk") is False
        assert _should_emit_event("dsh_tool_call") is False
        assert _should_emit_event("hetero_dispatch_started") is True
        assert _should_emit_event("run_finished") is True

    def test_filter_exempts_must_answer_events(self, monkeypatch):
        """F2: permission requests must ALWAYS be visible even at gate 0."""
        monkeypatch.setenv("DSH_ACP_ENABLED", "0")
        assert _should_emit_event("dsh_permission_request") is True
        assert _should_emit_event("spi_permission_request") is True

    def test_filter_allows_all_when_on(self, monkeypatch):
        monkeypatch.setenv("DSH_ACP_ENABLED", "1")
        for t in ("dsh_message_chunk", "dsh_tool_call", "hetero_dispatch_started",
                  "run_finished", "dsh_permission_request"):
            assert _should_emit_event(t) is True


class TestUnifiedRunView:
    """C3 + F4/F5: three-source aggregation with precise matching."""

    def _call(self, run_id):
        from api.routes import get_unified_view
        return get_unified_view(run_id)

    def test_missing_sources_labelled(self):
        result = self._call("nonexistent-run-id-9999")
        assert result["run_id"] == "nonexistent-run-id-9999"
        assert result["sources"]["platform"]["trace.jsonl"] == "MISSING"
        assert result["sources"]["platform"]["state.json"] == "MISSING"
        # F5: dsh_session must NOT false-positive on prefix match
        assert result["sources"]["dsh_session"] == "MISSING"

    def test_f4_backend_audit_run_specific(self):
        """F4: backend_audit must be MISSING for a nonexistent run (no
        matching entries in the global audit-log directory)."""
        result = self._call("nonexistent-run-id-9999")
        assert result["sources"]["backend_audit"] == "MISSING", \
            f"backend_audit should be MISSING: {result['sources']['backend_audit']}"
        assert "backend_audit" in result["missing"]

    def test_platform_source_structure(self):
        result = self._call("any-run")
        p = result["sources"]["platform"]
        assert "trace.jsonl" in p and "health.json" in p and "state.json" in p

    def test_missing_list_only_for_actually_missing(self):
        result = self._call("any-run")
        for key in result["missing"]:
            assert key in result["sources"]
            assert result["sources"][key] == "MISSING"


class TestPermissionEndpointReal:
    """F1/F3: the permission card endpoint must ACTUALLY deliver answers."""

    def test_no_session_returns_404(self):
        from api.routes import answer_permission
        from fastapi import HTTPException
        with pytest.raises(HTTPException, match="no active session handle"):
            answer_permission("nonexistent-run",
                              body={"option_id": "allow-once"})

    def test_no_pending_returns_404(self):
        from agent_bridge.session_registry import register
        from api.routes import answer_permission
        from fastapi import HTTPException

        class FakeHandle:
            _pending = {"request": None, "answered_at": None, "decision": None}
        register("test-no-pending", FakeHandle())
        with pytest.raises(HTTPException, match="no pending"):
            answer_permission("test-no-pending",
                              body={"option_id": "allow-once"})

    def test_invented_option_rejected_422(self):
        """F1: an optionId not offered by the server => 422 fail-closed."""
        from agent_bridge.session_registry import register
        from agent_bridge.spi import PermissionRequest
        from api.routes import answer_permission
        from fastapi import HTTPException

        req = PermissionRequest(
            tool_call_id="call-1", options=("allow-once", "reject-once"), raw={})
        event = threading.Event()

        class FakeHandle:
            _pending = {"request": req, "event": event, "decision": None}

        register("test-invent", FakeHandle())
        with pytest.raises(HTTPException, match="not offered by the server"):
            answer_permission("test-invent",
                              body={"option_id": "INVENTED-option"})

    def test_valid_option_delivered(self):
        """F1 positive: a valid optionId is delivered through the handle."""
        from agent_bridge.session_registry import register
        from agent_bridge.spi import PermissionRequest
        from api.routes import answer_permission

        req = PermissionRequest(
            tool_call_id="call-1", options=("allow-once", "reject-once"), raw={})
        event = threading.Event()
        received = {}

        class FakeHandle:
            _pending = {"request": req, "event": event, "decision": None}
            def answer_permission(self, r, d):
                received["decision"] = d
                event.set()

        register("test-valid", FakeHandle())
        result = answer_permission("test-valid", body={"option_id": "allow-once"})
        assert result["status"] == "answered"
        assert result["option_id"] == "allow-once"
        assert event.is_set()
        assert received["decision"].option_id == "allow-once"


class TestDispatchConsoleHTML:
    def test_console_elements_present(self):
        html = Path("api/static/index.html").read_text(encoding="utf-8")
        # W1a-2: the permission card moved from the side panel into the asking
        # agent's window, so the panel element is replaced by the mount contract.
        for elem in ("dc-panel", "dc-events", "dc-unified",
                     "dcConnect", "dcAnswer", "dcLoadUnified", "dcToggle"):
            assert elem in html, f"dispatch console element {elem!r} missing"
        assert "mountPerm(" in html, "permission cards must be mounted per window"
        assert "dc-perm-card" not in html, "the old side-panel mount must be gone"

    def test_no_real_wb_contact(self):
        html = Path("api/static/index.html").read_text(encoding="utf-8")
        assert "codebuddy" not in html.lower()
        assert "subprocess" not in html
