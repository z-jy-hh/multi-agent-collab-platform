"""R17-W8a: the heterogeneous dispatch gate (design v3 §7-P4, §3.3, §6.5).

The SINGLE entry point for dispatching work to any backend via the SPI.
Implements the gate chain in FIXED ORDER (design §7-P4 ②):

  1. DISPATCH_HETERO switch (sequencing-aid: at 0, the old path runs)
  2. bindings resolution (B3: the registry bindings are the ONLY truth)
  3. backend enabled check
  4. consent (paid ⇒ requires_authorization)
  5. B1 freshness check (TTL-based; a stale or missing health read rejects)
  6. platform_limits (daily/hourly totals, per-backend concurrency)
  7. version_lock handshake
  8. cwd containment
  9. workspace mutex (same workspace, one writer)
 10. argv flag assertion (no loosening tokens in the generated command)
 11. SPI dispatch (backend.execute_task or backend.open_session)

Every rejection carries a readable reason. The gate itself NEVER catches
exceptions from the backend (those propagate as SpiError to the caller).

B1 freshness: after a successful backend construction + health probe, the
result is cached with a monotonic timestamp. Subsequent dispatches within
``freshness_ttl`` seconds reuse the cache (prevents flapping). After TTL,
the next dispatch triggers a re-read. This does NOT replace per-run
deadlines (turn_timeout / permission_timeout still guard the running turn).

B3 addressing: the routing and audit subject come from ``bindings`` in the
registry (e.g. ``{dsh-exec: "dsh:"}``). Any self-reported ID from the
backend that differs from the registry binding is IGNORED and logged (the
backend_id parameter to this function IS the registry truth).

B6 resume: when ``capabilities.supports_resume`` is true and the same
``role_run_id`` has been dispatched before, the backend attempts to resume
the previous session (via ``manager.resume``). If resume fails (session not
found), the error is LOUD (SpiError), never a silent new session. When
``supports_resume`` is false, a resume request is explicitly rejected.
"""

from __future__ import annotations

import os
import threading
import time

from agent_bridge.errors import SpiError

DISPATCH_ENV = "DISPATCH_HETERO"
FRESHNESS_TTL_DEFAULT = 600.0            # = deadline_prompt_s default


class DispatchGate:
    """Stateful dispatch gate: holds workspace locks and platform-level
    counters. B1 freshness uses a MODULE-LEVEL cache (shared across
    DispatchGate instances) so that a health check recorded by one instance
    is visible to later instances (including those created inside
    ``pre_run_gate``)."""

    # module-level shared freshness cache (B1)
    _shared_freshness: dict[str, dict] = {}
    # module-level shared workspace locks (N13): the pre-run gate and the
    # releaser are different instances, so an instance-level dict would make
    # the mutex decorative (P4FIX-3 fix). The FILE lock next to it covers
    # other processes (P4FIX-4, reviewer finding S5).
    _shared_workspace_locks: dict[str, bool] = {}
    _shared_lock_files: dict[str, int] = {}

    def __init__(self, *, freshness_ttl: float = FRESHNESS_TTL_DEFAULT) -> None:
        self.freshness_ttl = freshness_ttl
        self._lock = threading.Lock()

    def _check_freshness(self, backend_id: str) -> dict:
        """B1: return a consistent freshness record. ``fresh=True`` means a
        successful health read exists within TTL; ``fresh=False`` means the
        caller must perform a new health read before dispatching."""
        with self._lock:
            rec = self._shared_freshness.get(backend_id)
            if rec and (time.monotonic() - rec["ts"]) < self.freshness_ttl:
                return {"fresh": True, "result": rec["result"],
                        "detail": rec["detail"], "ts": rec["ts"]}
        return {"fresh": False, "result": None, "detail": None, "ts": None}

    def record_freshness(self, backend_id: str, *, ok: bool, detail: str) -> None:
        with self._lock:
            self._shared_freshness[backend_id] = {
                "result": ok, "ts": time.monotonic(), "detail": detail}

    def acquire_workspace(self, cwd: str) -> None:
        """B3/N13 workspace mutex: same cwd, one writer - in this process AND
        across processes (reviewer finding S5: an in-process dict alone is
        decorative once the platform runs more than one worker)."""
        with self._lock:
            if self._shared_workspace_locks.get(cwd):
                raise SpiError(
                    f"workspace {cwd!r} is already locked by another session "
                    f"(N13 workspace mutex)", kind="dispatch", backend="gate")
            self._shared_workspace_locks[cwd] = True
        try:
            fd = _acquire_file_lock(cwd)
        except SpiError:
            with self._lock:
                self._shared_workspace_locks.pop(cwd, None)
            raise
        with self._lock:
            self._shared_lock_files[cwd] = fd

    def release_workspace(self, cwd: str) -> None:
        with self._lock:
            fd = self._shared_lock_files.pop(cwd, None)
            self._shared_workspace_locks.pop(cwd, None)
        if fd is not None:
            _release_file_lock(fd)

    def check_platform_limits(self, backend_id: str, *, limits: dict,
                              daily_count: int, active: int,
                              hourly_count: int = 0) -> None:
        """D4: platform-level caps (daily/hourly totals, concurrency).

        ``daily_count``/``hourly_count``/``active`` come from real usage
        (``agent_bridge.gate_counters``), never from "is the cap non-zero"
        (P4FIX-2 finding F4)."""
        daily = limits.get("daily_tool_calls")
        if daily is not None and daily_count >= daily:
            raise SpiError(
                f"platform daily_tool_calls exhausted ({daily_count} >= {daily})",
                kind="budget", backend=backend_id)
        hourly = limits.get("hourly_tool_calls")
        if hourly is not None and hourly_count >= hourly:
            raise SpiError(
                f"platform hourly_tool_calls exhausted ({hourly_count} >= {hourly})",
                kind="budget", backend=backend_id)
        conc = limits.get("max_concurrent_runs_per_backend")
        if conc is not None and active >= conc:
            raise SpiError(
                f"per-backend concurrency exhausted ({active} >= {conc})",
                kind="budget", backend=backend_id)


def dispatch_hetero_enabled() -> bool:
    """True ONLY when ``DISPATCH_HETERO=1``. At 0, the old (pre-P4) path
    runs entirely - no bindings resolution, no SPI dispatch, no gate."""
    return os.environ.get(DISPATCH_ENV, "0") == "1"


def _lock_path(cwd: str) -> str:
    return os.path.join(cwd, ".gate.lock")


def _acquire_file_lock(cwd: str) -> int:
    """Best-effort cross-process exclusive lock on ``<cwd>/.gate.lock``."""
    os.makedirs(cwd, exist_ok=True)
    fd = os.open(_lock_path(cwd), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise SpiError(
            f"workspace {cwd!r} is locked by another PROCESS "
            f"(N13 cross-process mutex): {exc}", kind="dispatch", backend="gate") from exc
    return fd


def _release_file_lock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


def resolve_binding(data: dict, agent_id: str, *, backend_id: str) -> dict:
    """B3: resolve a binding from the registry. The ``backend_id`` parameter
    IS the registry truth; any self-reported ID that differs is IGNORED.

    Returns the matching backend entry. Raises if the binding is missing
    or the target backend is not declared."""
    bindings = data.get("bindings") or {}
    bound = bindings.get(agent_id)
    if bound is None:
        raise SpiError(
            f"agent_id {agent_id!r} has no binding in the registry "
            f"(B3: routing comes from bindings, not self-reports)",
            kind="dispatch", backend=backend_id)
    target = bound.rstrip(":")
    if target != backend_id:
        # B3 negative: self-reported ID differs from registry binding
        raise SpiError(
            f"binding {agent_id!r} → {bound!r} does not match backend "
            f"{backend_id!r} (B3: registry bindings are the only truth)",
            kind="dispatch", backend=backend_id)
    entry = (data.get("backends") or {}).get(target)
    if entry is None:
        raise SpiError(f"backend {target!r} not found in registry backends",
                       kind="dispatch", backend=backend_id)
    return entry
