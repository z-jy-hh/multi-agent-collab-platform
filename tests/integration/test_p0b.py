"""R17-P0b: F4 real hit + N1 answered/pending distinction + N2 answered_by."""
from __future__ import annotations

import json
import os
import threading
import time
from unittest.mock import patch

import pytest

from agent_bridge.dispatch import DispatchGate
from agent_bridge.errors import SpiError
from agent_bridge.spi import PermissionRequest


# --- F4: backend_audit actually hits matching files ---------------------------

class TestF4BackendAuditReal:
    def test_positive_matching_file(self, tmp_path, monkeypatch):
        """F4 positive: a JSONL file containing the run_id must be found."""
        audit_dir = tmp_path / "audit-log"
        audit_dir.mkdir()
        run_id = "test-run-f4-positive"
        (audit_dir / "match.jsonl").write_text(
            json.dumps({"run_id": run_id, "action": "test"}) + "\n",
            encoding="utf-8")
        from api.routes import get_unified_view
        monkeypatch.setenv("WB_AUDIT_DIR", str(tmp_path / "audit-log"))
        result = get_unified_view(run_id)
        ba = result["sources"]["backend_audit"]
        assert isinstance(ba, dict), f"expected dict, got {ba!r}"
        assert "match.jsonl" in ba.get("matching_files", [])

    def test_discriminating_non_matching_excluded(self, tmp_path, monkeypatch):
        """F4 discriminating: a file NOT containing the run_id must NOT appear."""
        audit_dir = tmp_path / "audit-log"
        audit_dir.mkdir()
        (audit_dir / "match.jsonl").write_text(
            json.dumps({"run_id": "target-run"}) + "\n", encoding="utf-8")
        (audit_dir / "other.jsonl").write_text(
            json.dumps({"run_id": "different-run"}) + "\n", encoding="utf-8")
        from api.routes import get_unified_view
        monkeypatch.setenv("WB_AUDIT_DIR", str(tmp_path / "audit-log"))
        result = get_unified_view("target-run")
        ba = result["sources"]["backend_audit"]
        assert "match.jsonl" in ba.get("matching_files", [])
        assert "other.jsonl" not in ba.get("matching_files", [])

    def test_non_utf8_file_visible_warning(self, tmp_path, monkeypatch):
        """F4: a non-UTF-8 file must produce a VISIBLE warning, not be
        silently skipped."""
        audit_dir = tmp_path / "audit-log"
        audit_dir.mkdir()
        (audit_dir / "bad.jsonl").write_bytes(b"\xa1\x94\x93bad")
        from api.routes import get_unified_view
        monkeypatch.setenv("WB_AUDIT_DIR", str(tmp_path / "audit-log"))
        result = get_unified_view("any-run")
        assert "read_warnings" in result, \
            f"read_warnings missing: {result.get('sources', {}).get('backend_audit')}"
        assert any("bad.jsonl" in w.get("file", "") for w in result["read_warnings"])


# --- N1: pending / answered distinction ----------------------------------------

class _FakeHandle:
    def __init__(self, with_pending=True, answered=False):
        if with_pending:
            self._pending = {
                "request": PermissionRequest(
                    tool_call_id="tc-1", options=("allow-once", "reject-once"),
                    raw={}),
                "event": threading.Event(),
                "decision": None,
                "answered_at": time.time() if answered else None,
            }
        else:
            self._pending = {}


import time
from agent_bridge.spi import PermissionRequest


class TestN1PendingAnswered:
    def test_answered_request_returns_409(self):
        """N1: answering an already-answered request => 409 loud rejection."""
        from agent_bridge.session_registry import register
        from api.routes import answer_permission
        from fastapi import HTTPException
        handle = _FakeHandle(with_pending=True, answered=True)
        register("n1-answered", handle)
        with pytest.raises(HTTPException, match="already answered"):
            answer_permission("n1-answered", body={"option_id": "allow-once"})

    def test_pending_permission_returns_none_after_answer(self):
        """N1: after answering via the real method, pending request is cleared."""
        import threading
        import time as _time
        from agent_bridge.backends.dsh import _DshSession
        from agent_bridge.spi import PermissionDecision

        backend = type("B", (), {"_emit": lambda s, t, p: events.append((t, p)),
                                 "backend_id": "dsh"})()
        events = []
        handle = _DshSession.__new__(_DshSession)
        handle._backend = backend
        handle._spec = type("S", (), {"run_id": "n1-test"})()
        handle._pending_lock = threading.Lock()
        req = PermissionRequest(tool_call_id="tc-1",
                                options=("allow-once", "reject-once"), raw={})
        handle._pending = {"request": req, "event": threading.Event(),
                           "decision": None}
        handle.answer_permission(
            req, PermissionDecision(option_id="allow-once",
                                    reason="test", basis="human"))
        assert handle._pending.get("request") is None, \
            f"N1 FAIL: request not cleared: {handle._pending.get('request')!r}"
        assert handle._pending.get("answered_at") is not None

    def test_permission_answered_event_emitted(self):
        """N1: answering emits a permission_answered event."""
        import threading
        from agent_bridge.backends.dsh import _DshSession
        from agent_bridge.spi import PermissionDecision
        events = []
        backend = type("B", (), {"_emit": lambda s, t, p: events.append((t, p)),
                                 "backend_id": "dsh"})()
        handle = _DshSession.__new__(_DshSession)
        handle._backend = backend
        handle._spec = type("S", (), {"run_id": "test-run"})()
        handle._pending_lock = threading.Lock()
        req = PermissionRequest(tool_call_id="tc-1",
                                options=("allow-once", "reject-once"), raw={})
        handle._pending = {"request": req, "event": threading.Event(),
                           "decision": None}
        handle.answer_permission(
            req, PermissionDecision(option_id="allow-once", reason="test",
                                    basis="human"))
        assert any(t == "permission_answered" for t, _ in events)
        assert handle._pending.get("answered_at") is not None
        assert handle._pending.get("request") is None


# --- N2: answered_by traces to decision.basis -----------------------------------

class TestN2AnsweredBy:
    def test_answered_by_human(self):
        from api.routes import answer_permission
        from agent_bridge.session_registry import register
        from agent_bridge.spi import PermissionRequest, PermissionDecision
        import threading
        req = PermissionRequest(tool_call_id="tc-1",
                                options=("allow-once", "reject-once"), raw={})
        event = threading.Event()
        received = {}

        class H:
            _pending = {"request": req, "event": event, "decision": None}
            def answer_permission(self, r, d):
                received["d"] = d
                event.set()

        register("n2-human", H())
        answer_permission("n2-human", body={
            "option_id": "allow-once", "answered_by": "human"})
        assert received["d"].basis == "human"

    def test_answered_by_auto(self):
        from api.routes import answer_permission
        from agent_bridge.session_registry import register
        from agent_bridge.spi import PermissionRequest
        import threading
        req = PermissionRequest(tool_call_id="tc-1",
                                options=("allow-once", "reject-once"), raw={})
        event = threading.Event()
        received = {}

        class H:
            _pending = {"request": req, "event": event, "decision": None}
            def answer_permission(self, r, d):
                received["d"] = d
                event.set()

        register("n2-auto", H())
        answer_permission("n2-auto", body={
            "option_id": "allow-once", "answered_by": "auto"})
        assert received["d"].basis == "auto"

    def test_answered_by_default_unspecified(self):
        from api.routes import answer_permission
        from agent_bridge.session_registry import register
        from agent_bridge.spi import PermissionRequest
        import threading
        req = PermissionRequest(tool_call_id="tc-1",
                                options=("allow-once", "reject-once"), raw={})
        event = threading.Event()
        received = {}

        class H:
            _pending = {"request": req, "event": event, "decision": None}
            def answer_permission(self, r, d):
                received["d"] = d
                event.set()

        register("n2-unspec", H())
        answer_permission("n2-unspec", body={"option_id": "allow-once"})
        assert received["d"].basis == "unspecified"
