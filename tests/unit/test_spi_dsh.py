"""R17-W5b: DshBackend conformance through the backend-agnostic SPI suite
(tests/unit/spi_conformance.py) + C5 fail-closed negatives + the dsh-specific
budget trip (usage_shape=context_used_size -> budget_context_used).

The suite imports NOTHING from dsh_bridge - this file is the ONLY place the
two worlds meet (the adapter factory + the assertion that the suite really
is decoupled).
"""

from __future__ import annotations

import copy
import itertools
import sys
import threading
from pathlib import Path

import pytest

from core.storage import SQLiteOpStore

from agent_bridge.backends.dsh import DshBackend
from agent_bridge.errors import SpiError
from agent_bridge.registry import default_registry_path, _read_yaml
from agent_bridge.spi import (
    BudgetSpec,
    PermissionDecision,
    PermissionRequest,
    RunAuthorization,
    SessionSpec,
    TaskSpec,
)
from tests.unit.spi_conformance import run_conformance

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_acp_server.py"


def _authz():
    return RunAuthorization(consent_token=None, data_opt_in=True)


def _entry():
    data = _read_yaml(default_registry_path())
    return copy.deepcopy(data["backends"]["dsh"])


def _defaults():
    data = _read_yaml(default_registry_path())
    return copy.deepcopy(data["defaults"])


def _make_factory(tmp_path):
    """Returns make_backend(scenario, *, extra_args, overrides) and
    make_spec(run_id) wired to the FAKE server (offline, no real model)."""
    dispatch = tmp_path / "dispatch"
    store_seq = itertools.count(1)

    def make_backend(scenario, *, extra_args=None, overrides=None):
        entry = _entry()
        # the FAKE server reports agentInfo.version 0.0.1 - the real-dsh
        # version lock (0.2.0-rc.2) is a P4 dispatch concern, cleared here
        entry["version_lock"]["agent_version"] = None
        args = list(scenario) if isinstance(scenario, (list, tuple)) else [scenario]
        kwargs = dict(overrides or {})
        store = SQLiteOpStore(tmp_path / f"ops-{next(store_seq)}.sqlite3")
        return DshBackend(
            entry, defaults=_defaults(),
            store=store,
            node_bin=sys.executable, dsh_bin_js=str(FAKE),
            dispatch_root=dispatch,
            client_extra_args=args + (extra_args or []),
            **kwargs)

    def make_spec(run_id):
        return SessionSpec(run_id=run_id,
                           platform_session_id=f"psess-{run_id}",
                           cwd=str(dispatch / run_id),
                           permission_mode="workspace-write")

    return make_backend, make_spec


# --- C2 · conformance suite (all faces must PASS) ------------------------------

class TestSpiConformance:
    @pytest.fixture()
    def factory(self, tmp_path):
        return _make_factory(tmp_path)

    def test_all_faces_pass(self, factory):
        make_backend, make_spec = factory
        results = run_conformance(make_backend, make_spec)
        failures = {k: v for k, v in results.items() if v != "PASS"}
        assert not failures, f"conformance failures: {failures}"

    def test_suite_is_decoupled_from_backends(self):
        """The suite module must not IMPORT any backend package (C2 proof:
        AST-level import check — docstring mentions don't count)."""
        import ast
        import inspect
        import sys
        mod = sys.modules[run_conformance.__module__]
        tree = ast.parse(inspect.getsource(mod))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        for banned in ("dsh_bridge", "agent_bridge.backends",
                       "agent_bridge.factory"):
            assert not any(name == banned or name.startswith(banned + ".")
                           for name in imported), \
                f"suite imports {banned!r} - it must stay backend-agnostic"

    def test_partial_backend_reports_not_implemented(self, tmp_path):
        """A backend that deliberately lacks faces gets per-face
        NOT-IMPLEMENTED, never a suite crash (C2 decoupling proof)."""
        class PartialBackend:
            backend_id = "partial"

            def open_session(self, spec):
                raise NotImplementedError("partial: no sessions")

            def execute_task(self, task, *, budget, authz):
                raise NotImplementedError("partial: no tasks")

        make_backend = lambda scenario, **kw: PartialBackend()
        make_spec = lambda rid: SessionSpec(
            run_id=rid, platform_session_id=f"p-{rid}",
            cwd=str(tmp_path / rid), permission_mode="workspace-write")
        results = run_conformance(make_backend, make_spec)
        assert all(v.startswith("NOT-IMPLEMENTED") for v in results.values()), \
            f"expected all NOT-IMPLEMENTED, got {results}"


# --- C5 · fail-closed negatives (backend level) ---------------------------------

class TestC5FailClosed:
    def _backend(self, tmp_path, entry_overrides=None, **kw):
        entry = _entry()
        if entry_overrides:
            entry_overrides(entry)
        return DshBackend(entry, defaults=_defaults(),
                          node_bin=sys.executable, dsh_bin_js=str(FAKE),
                          dispatch_root=tmp_path / "dispatch", **kw)

    def test_missing_capability_flag(self, tmp_path):
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

    def test_unknown_tier_loud(self, tmp_path):
        backend = self._backend(tmp_path)
        spec = SessionSpec(run_id="neg-tier", platform_session_id="p-neg-tier",
                           cwd=str(tmp_path / "dispatch" / "neg-tier"),
                           permission_mode="danger-full-access")
        with pytest.raises(SpiError, match="closed set"):
            backend.open_session(spec)

    def test_unoffered_option_rejected(self, tmp_path):
        """Answering with an optionId the server never offered must be
        rejected BEFORE it can reach the wire (F-2: no invented optionIds)."""
        make_backend, make_spec = _make_factory(tmp_path)
        backend = make_backend(["permission_roundtrip"])
        handle = backend.open_session(make_spec("neg-opt"))
        outcome = {}
        thread = threading.Thread(
            target=lambda: outcome.update(result=handle.prompt("outside?")),
            daemon=True)
        thread.start()
        req = handle.wait_permission(timeout=15)
        assert req is not None, "no permission request surfaced"
        with pytest.raises(SpiError, match="not offered by the server"):
            handle.answer_permission(
                req, PermissionDecision(option_id="allow-always",
                                        reason="invented", basis="human-hook"))
        # the bogus answer must NOT have been consumed: the real one still works
        handle.answer_permission(
            req, PermissionDecision(option_id="reject-once", reason="suite",
                                    basis="human-hook"))
        thread.join(timeout=30)
        assert not thread.is_alive()
        backend.shutdown()


# --- dsh-specific · budget trip through the usage_shape branch ------------------

class TestBudgetTrip:
    def test_context_used_budget_trips(self, tmp_path):
        """usage_shape=context_used_size: the usage_burst scenario sends the
        REAL wire shape {used, size}; a tight budget_context_used must trip
        and cancel the turn (the usage_shape branch in _build_runner)."""
        make_backend, make_spec = _make_factory(tmp_path)
        backend = make_backend(["usage_burst"])
        spec = make_spec("bt-1")
        result = backend.execute_task(
            TaskSpec(run_id="bt-1", platform_session_id="psess-bt-1",
                     task_text="burst", cwd=spec.cwd),
            budget=BudgetSpec(context_used=5000), authz=_authz())
        assert result.stop_reason == "cancelled", \
            f"expected cancelled, got {result.stop_reason!r}"
        assert result.usage.get("exceeded") is True, \
            f"usage should carry exceeded=True: {result.usage!r}"


def _authz():
    from agent_bridge.spi import RunAuthorization
    return RunAuthorization(consent_token=None, data_opt_in=True)
