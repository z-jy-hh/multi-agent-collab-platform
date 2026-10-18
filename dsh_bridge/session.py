"""R16-D2: dsh session lifecycle bound to platform runs.

Contract (taskbook D2 + owner work order ②):
- one dispatch = ONE dsh session with an IMMUTABLE cwd; the cwd must be an
  absolute directory the platform prepared under the dispatch root
  (``runs/dsh_dispatch/<run_id>/``) — dsh's writable boundary IS the session
  cwd (single root; no multi-root whitelist);
- ``run_id ↔ dsh sessionId ↔ cwd ↔ permission_mode`` is recorded to the
  platform op-log as a PRIVATE-scope blackboard op (same source of truth the
  inspector reads) and emitted as a platform event;
- resume goes ``session/list {cwd}`` → ``session/resume``; unknown/cross-cwd
  sessions are a LOUD error; ``session/load``/delete/fork are not supported
  by dsh and are never used;
- environment failures (cwd not preparable, spawn failure) raise
  ``DshEnvironmentError`` — D8 reports those SEPARATELY from task failures
  (owner work order: the sandbox-ACL defect must never be reported as a
  failed task).

Permission modes: default ``workspace-write``; ``danger-full-access`` only
when the caller explicitly passes it (audited by the caller, red line 1).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import itertools
from dataclasses import dataclass, field
from pathlib import Path

from core.paths import PROJECT_ROOT
from core.storage import SQLiteOpStore

from dsh_bridge.acp_client import AcpClient, AcpProcessDied, AcpProtocolError

# F2(复核): monotonic suffix for mapping op_ids (same collision class as the
# permission audits)
_MAPPING_SEQ = itertools.count(1)

ALLOWED_MODES = ("read-only", "workspace-write", "danger-full-access")


class DshDispatchError(RuntimeError):
    """Bad dispatch request (validation, unknown session, protocol)."""


class DshEnvironmentError(RuntimeError):
    """The HOST ENVIRONMENT cannot run a dispatch (cwd not preparable,
    spawn failure, sandbox provisioning). Reported separately from task
    failures — never conflated (owner work order, red line 13)."""


def default_dispatch_root() -> Path:
    """The dispatch root — repository-internal, gitignored (owner decision):
    every dispatch cwd lives under ``runs/dsh_dispatch/<run_id>/``.
    (F1: renamed from ``dispatch_root`` — the old name was shadowed by the
    ``DshSessionManager`` parameter of the same name, so the no-argument
    constructor crashed with ``TypeError: 'NoneType' object is not callable``.)"""
    override = os.environ.get("DSH_DISPATCH_ROOT")
    return Path(override) if override else PROJECT_ROOT / "runs" / "dsh_dispatch"


# backwards-compatible alias (selfcheck imports by the old name)
dispatch_root = default_dispatch_root


def cwd_smoke(cwd: Path, acl_runner=None) -> None:
    """Pre-dispatch restricted-writability smoke (red line 13 / regression
    sentinel): the directory must exist and accept create+delete, AND (F6,
    reviewer round 3) its sandbox provisioning must work — plain file
    create/delete passes with Authenticated-Users Modify while dsh's
    ``SetNamedSecurityInfoW/grantWrite`` provisioning fails (WinError 5) on
    an unauthorized root, so the file probe alone is a SENTINEL BLIND SPOT.
    Probe: ``icacls <dir> /setintegritylevel Low`` (per the reviewer's
    suggestion); a non-zero exit (access-denied class) ⇒
    ``DshEnvironmentError`` — the sandbox-ACL defect class.
    ``acl_runner(cmd) -> (returncode, output)`` is injectable for offline
    tests; default runs the real icacls."""
    try:
        cwd.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=str(cwd), prefix=".dsh_cwd_probe_")
        os.close(fd)
        os.remove(probe)
    except OSError as exc:
        raise DshEnvironmentError(
            f"dispatch cwd is not usable (create/delete probe failed): {cwd} — "
            f"{type(exc).__name__}: {exc}. This is an ENVIRONMENT problem "
            f"(e.g. the sandbox-ACL defect); it is NOT a task failure.") from exc
    runner = acl_runner or _default_acl_runner
    try:
        code, output = runner(["icacls", str(cwd), "/setintegritylevel", "Low"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DshEnvironmentError(
            f"sandbox ACL probe could not run at {cwd}: "
            f"{type(exc).__name__}: {exc}") from exc
    if code != 0:
        raise DshEnvironmentError(
            f"sandbox ACL probe failed at {cwd} (exit {code}): "
            f"{(output or '').strip()[:200]} — the dispatch root likely lacks "
            f"the provisioning rights dsh needs (file create/delete alone "
            f"passes on such roots; the ACL grant itself does not)")


def _default_acl_runner(cmd: list[str]) -> tuple[int, str]:
    import subprocess as _sub
    # 中文 Windows 的 icacls 输出是本地(GBK)编码 ⇒ 强制 utf-8/replace,
    # 否则 capture 的 reader 线程会解码崩掉（本机实测）
    proc = _sub.run(cmd, capture_output=True, text=True, timeout=15,
                    encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stderr or "") + (proc.stdout or "")


@dataclass
class DshRunSession:
    run_id: str
    platform_session_id: str
    cwd: str
    permission_mode: str
    dsh_session_id: str | None = None
    client: AcpClient | None = None
    extra: dict = field(default_factory=dict)

    def to_mapping(self) -> dict:
        return {"run_id": self.run_id,
                "platform_session_id": self.platform_session_id,
                "dsh_session_id": self.dsh_session_id,
                "cwd": self.cwd,
                "permission_mode": self.permission_mode,
                "recorded_at": time.time()}


class DshSessionManager:
    """Owns the shared ACP connection and the run↔session mappings."""

    def __init__(self, dsh_bin_js: str, *,
                 store: SQLiteOpStore,
                 emit: Callable[[str, dict], None] | None = None,
                 dispatch_root: Path | None = None,
                 node_bin: str = "node",
                 expected_agent_version: str | None = None,
                 client_extra_args: list[str] | None = None,
                 acl_runner=None) -> None:
        self._dsh_bin_js = dsh_bin_js
        self._store = store
        self._emit = emit or (lambda t, p: None)
        self._root = Path(dispatch_root) if dispatch_root \
            else default_dispatch_root()   # F1: the FUNCTION, not the shadowed param
        self._node = node_bin
        self._expected_agent_version = expected_agent_version
        self._client_extra_args = list(client_extra_args or [])
        self._acl_runner = acl_runner          # F6: injectable ACL probe
        self._client: AcpClient | None = None
        self._sessions: dict[str, DshRunSession] = {}   # run_id -> session

    # -- helpers ---------------------------------------------------------------

    def _prepare_cwd(self, run_id: str, cwd: str | None) -> Path:
        if cwd is None:
            cwd_path = self._root / run_id
        else:
            cwd_path = Path(cwd)
            if not cwd_path.is_absolute():
                raise DshDispatchError(f"dispatch cwd must be absolute: {cwd!r}")
        resolved = cwd_path.resolve()
        if resolved != self._root.resolve() and \
                self._root.resolve() not in resolved.parents:
            raise DshDispatchError(
                f"dispatch cwd must live under the dispatch root "
                f"{self._root} (dsh writable boundary = session cwd, single "
                f"root): {resolved}")
        cwd_smoke(resolved, acl_runner=self._acl_runner)
        return resolved

    def _client_for(self, permission_mode: str) -> AcpClient:
        if permission_mode not in ALLOWED_MODES:
            raise DshDispatchError(
                f"unknown permission mode {permission_mode!r}; "
                f"allowed: {ALLOWED_MODES}")
        if self._client is None:
            self._client = AcpClient(
                [self._node, self._dsh_bin_js, "--profile", "acp"]
                + self._client_extra_args,
                env_extra={"DSH_PERMISSION_MODE": permission_mode})
            try:
                self._client.initialize(
                    expected_protocol_version=1,
                    expected_agent_version=self._expected_agent_version)
            except AcpProtocolError:
                self._client.close()
                self._client = None
                raise
            except (OSError, AcpProcessDied) as exc:
                # spawn/early-death = ENVIRONMENT problem, not a task failure
                self._client.close()
                self._client = None
                raise DshEnvironmentError(
                    f"cannot run dsh acp subprocess: {exc}") from exc
        return self._client

    def _record(self, session: DshRunSession, action: str) -> None:
        """run_id↔sessionId↔cwd↔mode → platform op-log (private scope,
        queryable via ops_for_session) + platform event.
        F2(复核): the op_id carries a monotonic suffix — start→close→start
        (or repeated resumes) on the same run used to collide on
        `dsh-session-{run}-{action}` and silently lose every mapping but
        the first (op_id PRIMARY KEY + INSERT OR IGNORE)."""
        mapping = session.to_mapping()
        mapping["action"] = action
        from core.schemas import BlackboardOp

        op = BlackboardOp(
            op_id=f"dsh-session-{session.run_id}-{action}-{int(time.time() * 1000)}-{next(_MAPPING_SEQ)}",
            session_id=session.platform_session_id,
            agent_id="dsh_bridge",
            key=f"dsh_session::{session.run_id}",
            value=mapping,
            clock={},
            hlc=(0, 0, "dsh_bridge"),
            ts=time.time(),
            scope="private")
        self._store.append_op(op)
        self._emit("dsh_session", mapping)

    # -- lifecycle ---------------------------------------------------------------

    def start(self, run_id: str, platform_session_id: str, *,
              cwd: str | None = None,
              permission_mode: str = "workspace-write") -> DshRunSession:
        if run_id in self._sessions:
            raise DshDispatchError(f"run {run_id} already has a dsh session")
        cwd_path = self._prepare_cwd(run_id, cwd)
        client = self._client_for(permission_mode)
        result = client.request("session/new",
                                {"cwd": str(cwd_path), "mcpServers": []})
        session = DshRunSession(
            run_id=run_id, platform_session_id=platform_session_id,
            cwd=str(cwd_path), permission_mode=permission_mode,
            dsh_session_id=(result or {}).get("sessionId"), client=client)
        if not session.dsh_session_id:
            raise DshDispatchError(
                f"session/new returned no sessionId: {result!r}")
        self._sessions[run_id] = session
        self._record(session, "new")
        return session

    def resume(self, run_id: str, platform_session_id: str, dsh_session_id: str,
               *, cwd: str, permission_mode: str = "workspace-write") -> DshRunSession:
        cwd_path = self._prepare_cwd(run_id, cwd)
        client = self._client_for(permission_mode)
        listing = client.request("session/list", {"cwd": str(cwd_path)})
        known = {s.get("sessionId") for s in (listing or {}).get("sessions", [])}
        if dsh_session_id not in known:
            raise DshDispatchError(
                f"dsh session {dsh_session_id!r} not found under cwd "
                f"{cwd_path} (cross-cwd sessions are rejected by contract)")
        client.request("session/resume",
                       {"sessionId": dsh_session_id, "cwd": str(cwd_path),
                        "mcpServers": []})
        session = DshRunSession(
            run_id=run_id, platform_session_id=platform_session_id,
            cwd=str(cwd_path), permission_mode=permission_mode,
            dsh_session_id=dsh_session_id, client=client)
        self._sessions[run_id] = session
        self._record(session, "resume")
        return session

    def get(self, run_id: str) -> DshRunSession:
        session = self._sessions.get(run_id)
        if session is None:
            raise DshDispatchError(f"no dsh session for run {run_id}")
        return session

    def close_session(self, run_id: str) -> None:
        session = self._sessions.pop(run_id, None)
        if session is None:
            return
        session.client.request("session/close",
                               {"sessionId": session.dsh_session_id})
        self._record(session, "close")

    def shutdown(self) -> None:
        for run_id in list(self._sessions):
            try:
                self.close_session(run_id)
            except Exception:
                pass
        if self._client is not None:
            self._client.close()
            self._client = None
