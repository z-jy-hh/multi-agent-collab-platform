"""R17-W6a: ``WbBackend`` - the SPI adapter for WorkBuddy (WB), OFFLINE ONLY.

SKELETON (design v3 §7-P3): implements the full SPI surface structurally,
but the REAL WB dispatch (``codebuddy --acp``) is P4+ after real-machine
verification. For now the backend:

- inherits ALL ACP v1 protocol logic from DshBackend (same protocol, same
  session/prompt/permission/cancel/close shapes, same composed
  DshSessionManager + DshTurnRunner + gates);
- overrides ``backend_id`` to ``"wb"``;
- overrides ``__init__`` for the WB command shape (``["{wb_cli}", "--acp"]``
  instead of dsh's ``["{node}", "{dsh_bin}", "--profile", "acp"]``);
- overrides ``execute_task`` to enforce the **egress consent** check
  (``egress=true`` per N3: WB sends content to copilot.tencent.com);
- inherits the fail-closed capability checks (17 flags, all
  ``assumed-fail-closed`` — a registry claiming any flag the composition
  cannot honour is a constructor error);

Red lines: this class NEVER starts the real codebuddy CLI, NEVER calls
--acp, NEVER logs in, NEVER reads ~/.workbuddy/credentials/. The only WB
"contact" is through the FAKE ACP server in offline tests.
"""

from __future__ import annotations

import os

from agent_bridge.errors import SpiError
from agent_bridge.spi import BudgetSpec, RunAuthorization, SessionSpec
from agent_bridge.backends.dsh import (
    DshBackend,
    _capabilities_from_entry,
    _normalize,
)
from dsh_bridge.session import DshSessionManager


class WbBackend(DshBackend):
    """SPI backend for WorkBuddy (WB), OFFLINE skeleton."""

    backend_id = "wb"

    def __init__(self, entry: dict, *, defaults: dict, store=None, emit=None,
                 node_bin: str = "node", wb_cli: str | None = None,
                 dispatch_root=None, acl_runner=None, price_table=None,
                 budget: BudgetSpec | None = None,
                 client_extra_args: list[str] | None = None,
                 permission_timeout: float | None = None,
                 turn_timeout: float | None = None) -> None:
        # build capabilities (same fail-closed checks: 17 flags, composition
        # must be able to honour each one)
        self.capabilities = _capabilities_from_entry(self.backend_id, entry)
        self._entry = entry
        self._defaults = defaults
        self._budget = budget or BudgetSpec()
        self._mode_allowed = frozenset(entry["permission"]["mode_allowed"])

        # WB command resolution: {wb_cli} -> wb_cli param or WB_CLI env.
        # In OFFLINE tests wb_cli is the fake ACP server script; in real
        # dispatch (P4+) it would be the codebuddy CLI path.
        wb_cli_path = wb_cli or os.environ.get("WB_CLI") or ""
        if not wb_cli_path:
            raise SpiError(
                "command placeholder {wb_cli} has no value (set WB_CLI?)",
                kind="environment", backend=self.backend_id)

        version_lock = (entry.get("version_lock") or {}).get("agent_version")
        self._manager = DshSessionManager(
            wb_cli_path, store=store, emit=emit, node_bin=node_bin,
            dispatch_root=dispatch_root,
            expected_agent_version=version_lock, acl_runner=acl_runner,
            client_extra_args=client_extra_args)

        self._permission_timeout = permission_timeout or defaults["deadline_permission_s"]
        self._turn_timeout = turn_timeout or defaults["deadline_prompt_s"]
        self._mode_default = entry["permission"]["mode_default"]
        self._store = store
        self._emit = emit or (lambda t, p: None)
        self._price_table = price_table
        self._sessions: dict = {}

    def execute_task(self, task, *, budget, authz: RunAuthorization):
        """WB-specific: egress consent check (N3) + mode projection.

        ``egress=true`` means dispatching to WB sends workspace content to
        copilot.tencent.com. The data_opt_in flag (N3: per-task opt-in) must
        be explicitly set or the dispatch is refused BEFORE any spawn.

        The task's ``permission_mode`` (which defaults to ``workspace-write``
        in the dataclass) is projected to WB's ``mode_default`` (``default``)
        so the SPI-level closed-set check passes."""
        import dataclasses
        if self.capabilities.egress and not authz.data_opt_in:
            raise SpiError(
                "egress backend requires data_opt_in=true "
                "(N3: dispatch sends workspace content off-machine)",
                kind="authz", backend=self.backend_id)
        task = dataclasses.replace(task,
                                   permission_mode=self._mode_default)
        return super().execute_task(task, budget=budget, authz=authz)

    def resume_session(self, run_id: str, dsh_session_id: str, *,
                       platform_session_id: str) -> None:
        """B6/E8 resume face: WB explicitly REJECTS resume.

        ``capabilities.supports_resume=false`` (assumed-fail-closed, not
        measured on real WB) means the platform must NEVER silently create
        a new session when a resume is requested - it must raise a LOUD
        error explaining why."""
        raise SpiError(
            f"resume not supported by {self.backend_id} "
            f"(capabilities.supports_resume=false, assumed-fail-closed: "
            f"WB resume has NOT been verified on a real machine)",
            kind="dispatch", backend=self.backend_id)

    def answer_permission(self, request, decision) -> None:
        """WB override: N1 — after answering, clear pending + record time."""
        import time as _time
        self._pending["decision"] = decision
        self._pending["answered_at"] = _time.time()
        self._pending["request"] = None
        self._pending["event"].set()
        self._emit("permission_answered", {
            "run_id": self._spec.run_id,
            "option_id": decision.option_id,
            "basis": decision.basis})

    def open_session(self, spec: SessionSpec):
        """WB closed-set check uses the REAL WB mode (``default``); the
        OFFLINE skeleton then passes ``workspace-write`` to the composed
        manager (whose ALLOWED_MODES is dsh-specific). The real WB mode
        dispatch is a P4+ concern after real-machine verification."""
        if spec.permission_mode not in self._mode_allowed:
            raise SpiError(
                f"permission mode {spec.permission_mode!r} is not in the "
                f"registry whitelist {sorted(self._mode_allowed)} (closed set)",
                kind="dispatch", backend=self.backend_id)
        manager_spec = SessionSpec(
            run_id=spec.run_id,
            platform_session_id=spec.platform_session_id,
            cwd=spec.cwd,
            permission_mode="workspace-write")   # manager-compatible for offline
        try:
            session = self._manager.start(
                manager_spec.run_id, manager_spec.platform_session_id,
                cwd=manager_spec.cwd,
                permission_mode=manager_spec.permission_mode)
        except SpiError:
            raise
        except Exception as exc:
            raise _normalize(exc, self.backend_id) from exc
        handle = self._make_handle(session, spec)
        self._sessions[spec.run_id] = handle
        return handle

    def _make_handle(self, session, spec: SessionSpec):
        """Create the session handle (hook for subclass overrides)."""
        from agent_bridge.backends.dsh import _DshSession
        handle = _DshSession(self, session, spec, self._budget)
        return handle
