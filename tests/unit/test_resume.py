"""R17-P4F3: B6/E8 resume face tests - dsh resume succeeds (offline fake),
WB resume explicitly rejects. Criterion: NEVER silently create a new session.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_bridge.errors import SpiError
from tests.unit.test_spi_dsh import _make_factory as _dsh_factory
from tests.unit.test_spi_wb import _make_factory as _wb_factory


class TestDshResume:
    """dsh: supports_resume=true (measured session/list+resume)."""

    def test_resume_session_method_exists(self, tmp_path):
        make_backend, _ = _dsh_factory(tmp_path)
        backend = make_backend(["happy"])
        assert hasattr(backend, "resume_session"), \
            "DshBackend must have resume_session (B6/E8 face)"

    def test_resume_capabilities_check(self, tmp_path):
        make_backend, _ = _dsh_factory(tmp_path)
        backend = make_backend(["happy"])
        assert backend.capabilities.supports_resume is True, \
            "dsh capabilities.supports_resume should be true (measured)"

    def test_resume_rejects_unknown_session(self, tmp_path):
        """Resume with a session ID the fake doesn't know ⇒ LOUD error
        (never a silent new session)."""
        make_backend, _ = _dsh_factory(tmp_path)
        backend = make_backend(["happy"])
        with pytest.raises(SpiError) as excinfo:
            backend.resume_session(
                "unknown-run", "nonexistent-session-id",
                platform_session_id="psess-unknown")
        assert "not found" in str(excinfo.value).lower() or \
               "not supported" in str(excinfo.value).lower() or \
               excinfo.value.kind in ("dispatch", "error"), \
            f"expected a loud resume error, got: {excinfo.value}"


class TestWbResumeReject:
    """WB: supports_resume=false (assumed-fail-closed) ⇒ explicit REJECTION."""

    def test_wb_resume_rejects(self, tmp_path):
        make_backend, _ = _wb_factory(tmp_path)
        backend = make_backend(["happy"])
        assert backend.capabilities.supports_resume is False, \
            "WB capabilities.supports_resume should be false (assumed-fail-closed)"
        with pytest.raises(SpiError, match="resume not supported"):
            backend.resume_session(
                "wb-run", "wb-session-id", platform_session_id="psess-wb")

    def test_wb_resume_error_mentions_assumed(self, tmp_path):
        """The rejection must EXPLAIN why (assumed-fail-closed, not measured)."""
        make_backend, _ = _wb_factory(tmp_path)
        backend = make_backend(["happy"])
        with pytest.raises(SpiError, match="assumed-fail-closed"):
            backend.resume_session(
                "wb-run", "wb-sid", platform_session_id="p")
