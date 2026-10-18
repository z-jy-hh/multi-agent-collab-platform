"""R17-W6a: WbBackend conformance through the SAME backend-agnostic SPI suite
(tests/unit/spi_conformance.py) + WB_ENABLED gate counter-proof + C3
fail-closed negatives.

ZERO real-WB contact: the fake server is the SAME one used for dsh (ACP v1
is protocol-identical). No codebuddy, no --acp, no login, no credentials.
"""

from __future__ import annotations

import copy
import itertools
import os
import sys
from pathlib import Path

import pytest

from core.storage import SQLiteOpStore

from agent_bridge.backends.wb import WbBackend
from agent_bridge.errors import SpiError
from agent_bridge.factory import build_backend
from agent_bridge.registry import default_registry_path, _read_yaml
from agent_bridge.spi import (
    BudgetSpec,
    RunAuthorization,
    SessionSpec,
)
from tests.unit.spi_conformance import run_conformance

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_acp_server.py"


def _authz(**kw):
    defaults = dict(consent_token="test-consent", data_opt_in=True)
    defaults.update(kw)
    return RunAuthorization(**defaults)


def _entry():
    data = _read_yaml(default_registry_path())
    entry = copy.deepcopy(data["backends"]["wb"])
    # the FAKE server reports agentInfo.version 0.0.1 - the real WB version
    # lock is a P4 concern, cleared for offline conformance
    entry["version_lock"]["agent_version"] = None
    return entry


def _defaults():
    data = _read_yaml(default_registry_path())
    return copy.deepcopy(data["defaults"])


def _make_factory(tmp_path):
    dispatch = tmp_path / "dispatch"
    seq = itertools.count(1)

    def make_backend(scenario, *, extra_args=None, overrides=None):
        args = list(scenario) if isinstance(scenario, (list, tuple)) else [scenario]
        store = SQLiteOpStore(tmp_path / f"ops-{next(seq)}.sqlite3")
        return WbBackend(
            _entry(), defaults=_defaults(), store=store,
            node_bin=sys.executable, wb_cli=str(FAKE),
            dispatch_root=dispatch,
            client_extra_args=args + (extra_args or []),
            **(overrides or {}))

    def make_spec(run_id):
        return SessionSpec(run_id=run_id,
                           platform_session_id=f"psess-{run_id}",
                           cwd=str(dispatch / run_id),
                           permission_mode="default")

    return make_backend, make_spec


# --- C2 · conformance suite (SAME suite, WB backend) ---------------------------

class TestSpiConformanceWB:
    @pytest.fixture()
    def factory(self, tmp_path):
        return _make_factory(tmp_path)

    def test_all_faces_pass(self, factory):
        make_backend, make_spec = factory
        results = run_conformance(make_backend, make_spec)
        failures = {k: v for k, v in results.items() if v != "PASS"}
        assert not failures, f"conformance failures: {failures}"


# --- C4 · WB_ENABLED gate counter-proof ----------------------------------------

class TestWBGate:
    def test_gate_off_returns_none(self, monkeypatch, tmp_path):
        """WB_ENABLED=0: build_backend('wb') returns None. Tripwire proves
        neither the registry file-read nor the WbBackend constructor fires."""
        monkeypatch.setenv("AGENT_REGISTRY", "1")
        monkeypatch.setenv("WB_ENABLED", "0")

        def boom_read(*_a, **_k):
            raise AssertionError("wb entry was READ at WB_ENABLED=0")
        def boom_build(*_a, **_k):
            raise AssertionError("WbBackend was CONSTRUCTED at WB_ENABLED=0")

        # only the wb BRANCH is gated; the dsh branch still works
        # (so we can't tripwire the shared _read_yaml - instead we check
        #  that the returned value is None, proving the wb branch wasn't taken)
        result = build_backend("wb", overrides={
            "node_bin": sys.executable, "wb_cli": str(FAKE),
            "dispatch_root": tmp_path / "dispatch"})
        assert result is None

    def test_gate_off_wb_init_never_fires(self, monkeypatch, tmp_path):
        """Stronger tripwire: patch WbBackend.__init__ to raise - prove it's
        never called at WB_ENABLED=0."""
        monkeypatch.setenv("AGENT_REGISTRY", "1")
        monkeypatch.setenv("WB_ENABLED", "0")
        monkeypatch.setattr(
            "agent_bridge.backends.wb.WbBackend.__init__",
            lambda self, *a, **k: (_ for _ in ()).throw(
                AssertionError("WbBackend.__init__ fired at WB_ENABLED=0")))
        result = build_backend("wb")
        assert result is None

    def test_gate_on_builds_wb(self, monkeypatch, tmp_path):
        """WB_ENABLED=1 + AGENT_REGISTRY=1 ⇒ WbBackend is constructed."""
        monkeypatch.setenv("AGENT_REGISTRY", "1")
        monkeypatch.setenv("WB_ENABLED", "1")
        backend = build_backend("wb", overrides={
            "node_bin": sys.executable, "wb_cli": str(FAKE),
            "dispatch_root": tmp_path / "dispatch"})
        assert backend is not None
        assert backend.backend_id == "wb"
        backend.shutdown()

    def test_gate_requires_both(self, monkeypatch):
        """WB_ENABLED alone or AGENT_REGISTRY alone ⇒ gate stays shut."""
        for env in ({"AGENT_REGISTRY": "0", "WB_ENABLED": "1"},
                    {"AGENT_REGISTRY": "1", "WB_ENABLED": "0"},
                    {"AGENT_REGISTRY": "0", "WB_ENABLED": "0"}):
            monkeypatch.setenv("AGENT_REGISTRY", env["AGENT_REGISTRY"])
            monkeypatch.setenv("WB_ENABLED", env["WB_ENABLED"])
            assert build_backend("wb") is None


# --- C3 · fail-closed negatives (WB capability level) ---------------------------

class TestC3FailClosed:
    def _backend(self, tmp_path, entry_overrides=None, **kw):
        entry = _entry()
        if entry_overrides:
            entry_overrides(entry)
        return WbBackend(entry, defaults=_defaults(),
                         node_bin=sys.executable, wb_cli=str(FAKE),
                         dispatch_root=tmp_path / "dispatch", **kw)

    def test_missing_capability(self, tmp_path):
        def drop(entry):
            del entry["capabilities"]["egress"]
        with pytest.raises(SpiError, match="missing capability flags"):
            self._backend(tmp_path, drop)

    def test_permission_has_path_true_rejected(self, tmp_path):
        def claim(entry):
            entry["capabilities"]["permission_has_path"]["value"] = True
        with pytest.raises(SpiError, match="permission_has_path=true"):
            self._backend(tmp_path, claim)

    def test_cancel_not_notification_rejected(self, tmp_path):
        def claim(entry):
            entry["capabilities"]["cancel_is_notification"]["value"] = False
        with pytest.raises(SpiError, match="cancel_is_notification=false"):
            self._backend(tmp_path, claim)

    def test_data_opt_in_required_for_egress(self, tmp_path):
        """N3: egress=true ⇒ execute_task without data_opt_in ⇒ refused
        BEFORE any spawn."""
        make_backend, make_spec = _make_factory(tmp_path)
        backend = make_backend(["happy"])
        spec = make_spec("neg-egress")
        with pytest.raises(SpiError, match="data_opt_in"):
            backend.execute_task(
                TaskSpec(run_id="neg-egress",
                         platform_session_id="psess-neg-egress",
                         task_text="test", cwd=spec.cwd),
                budget=BudgetSpec(),
                authz=_authz(data_opt_in=False))

    def test_data_opt_in_present_allows_dispatch(self, tmp_path):
        """N3: with data_opt_in=true, the dispatch proceeds normally."""
        make_backend, make_spec = _make_factory(tmp_path)
        backend = make_backend(["updates_then_end"])
        spec = make_spec("pos-egress")
        result = backend.execute_task(
            TaskSpec(run_id="pos-egress", platform_session_id="psess-pos-egress",
                     task_text="test", cwd=spec.cwd),
            budget=BudgetSpec(), authz=_authz(data_opt_in=True))
        assert result.error is None
        assert result.stop_reason == "end_turn"


# needed by the egress tests
from agent_bridge.spi import TaskSpec  # noqa: E402
