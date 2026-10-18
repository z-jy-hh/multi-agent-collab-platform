"""W4-1: ``ACPRuntime`` — role-level native execution on the dsh ACP backend.

Composition, not duplication: every protocol / permission / budget semantic
stays in ``dsh_bridge`` and ``agent_bridge.backends.dsh``.  This module only

* routes agents (``routing.map`` members go native, everybody else goes to the
  injected ``inner`` runtime — CrewAI or Scripted);
* serialises turns on the ONE ACP client (dsh: one prompt at a time) with a
  bounded in-flight counter and a cancel-aware wait loop;
* classifies failures (config / environment / process / timeout / budget /
  host-cancel) and only falls back when explicitly asked to;
* projects the evidence into ``AgentResult.meta`` so ``graphs/builder.py`` can
  put the real ``backend_session_id`` into ``agent_output``.

Red lines: no import of ``graphs``/``api`` (layercheck), no change to
``dsh_bridge``, no new registry keys, no HTTP endpoints.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from core.errors import CancelledRun, ConfigError
from core.paths import storage_path
from core.schemas import AgentResult
from core.storage import SQLiteOpStore

from agent_bridge.errors import SpiError
from agent_bridge.spi import BudgetSpec, SessionSpec

# ---------------------------------------------------------------------------
# defaults / env
# ---------------------------------------------------------------------------
DEFAULT_QUEUE_MAX = 8
DEFAULT_QUEUE_WAIT_S = 600.0
DEFAULT_MAX_TURNS = 8
DEFAULT_WALL_S = 1800.0
CLOSE_GATE_WAIT_S = 5.0
WALL_RESERVE_S = 60.0
_POLL_S = 0.2


class NativeConfigError(ConfigError):
    """Configuration error with a stable machine-readable code (never retried)."""

    def __init__(self, message: str, *, code: str = "native.config") -> None:
        super().__init__(message)
        self.code = code


def runtime_kind(obj: Any) -> str:
    """Mirror ``graphs.builder._runtime_kind`` without importing the graph layer."""
    if obj is None:
        return "none"
    name = type(obj).__name__.lower()
    if "scripted" in name:
        return "scripted"
    if "crewai" in name:
        return "crewai"
    return name or "unknown"


def runtime_from_env(run_id: str, platform_session_id: str, *, inner=None, bus=None,
                     store=None, cancel_event=None) -> "ACPRuntime":
    """Build an ACPRuntime from the ``NATIVE_*`` switches (runner wiring)."""
    mode = (os.environ.get("NATIVE_RUNTIME") or "off").strip().lower()
    if mode not in ("off", "dsh"):
        raise NativeConfigError(f"NATIVE_RUNTIME must be off|dsh, got {mode!r}",
                                code="native.bad_env")
    raw_agents = os.environ.get("NATIVE_AGENTS")
    agents = None
    if raw_agents is not None:
        agents = frozenset(a.strip() for a in raw_agents.split(",") if a.strip())
    fallback = (os.environ.get("NATIVE_FALLBACK") or "off").strip().lower()
    if fallback not in ("off", "crewai"):
        raise NativeConfigError(f"NATIVE_FALLBACK must be off|crewai, got {fallback!r}",
                                code="native.bad_env")
    raw_queue_max = os.environ.get("NATIVE_QUEUE_MAX")
    try:
        queue_max = int(raw_queue_max) if raw_queue_max else DEFAULT_QUEUE_MAX
    except ValueError as exc:
        raise NativeConfigError(
            f"NATIVE_QUEUE_MAX must be an integer, got {raw_queue_max!r}",
            code="native.bad_env") from exc
    raw_wait = os.environ.get("NATIVE_QUEUE_WAIT_S")
    try:
        queue_wait_s = float(raw_wait) if raw_wait else DEFAULT_QUEUE_WAIT_S
    except ValueError as exc:
        raise NativeConfigError(
            f"NATIVE_QUEUE_WAIT_S must be a number, got {raw_wait!r}",
            code="native.bad_env") from exc
    raw_turns = os.environ.get("NATIVE_MAX_TURNS")
    try:
        max_turns = int(raw_turns) if raw_turns else DEFAULT_MAX_TURNS
    except ValueError as exc:
        raise NativeConfigError(
            f"NATIVE_MAX_TURNS must be an integer, got {raw_turns!r}",
            code="native.bad_env") from exc
    return ACPRuntime(
        run_id=run_id, platform_session_id=platform_session_id,
        agents=agents, inner=inner, fallback=fallback, bus=bus, store=store,
        cancel_event=cancel_event,
        queue_max=queue_max, queue_wait_s=queue_wait_s, max_turns=max_turns,
    )


# ---------------------------------------------------------------------------
class ACPRuntime:
    """AgentRuntime adapter that executes ``routing.map`` roles on dsh ACP."""

    backend_id = "dsh"

    def __init__(self, *, run_id: str, platform_session_id: str,
                 backend_id: str = "dsh",
                 agents: frozenset[str] | None = None,
                 inner=None,
                 fallback: str = "off",
                 bus=None, store=None, cancel_event=None,
                 queue_max: int = DEFAULT_QUEUE_MAX,
                 queue_wait_s: float = DEFAULT_QUEUE_WAIT_S,
                 max_turns: int = DEFAULT_MAX_TURNS,
                 wall_s: float = DEFAULT_WALL_S,
                 permission_mode: str = "workspace-write",
                 dispatch_root=None,
                 rotate_at: float = 0.7) -> None:
        if queue_max < 1:
            raise NativeConfigError("NATIVE_QUEUE_MAX must be >= 1",
                                    code="native.bad_env")
        if max_turns < 1:
            raise NativeConfigError("NATIVE_MAX_TURNS must be >= 1",
                                    code="native.bad_env")
        if fallback not in ("off", "crewai"):
            raise NativeConfigError(f"fallback must be off|crewai, got {fallback!r}",
                                    code="native.bad_env")
        if permission_mode not in ("read-only", "workspace-write"):
            raise NativeConfigError(
                f"permission_mode {permission_mode!r} is not in the registry "
                f"whitelist", code="native.bad_mode")
        if not 0.0 <= float(rotate_at) <= 1.0:
            raise NativeConfigError("rotate_at must be in [0, 1]",
                                    code="native.bad_env")
        self._run_id = run_id
        self._platform_session_id = platform_session_id
        self._backend_id = backend_id
        self._agents_arg = agents
        self._inner = inner
        self._fallback_mode = fallback
        self._bus = bus
        self._store = store
        self._cancel_event = cancel_event
        self._queue_max = queue_max
        self._queue_wait_s = queue_wait_s
        self._max_turns = max_turns
        self._wall_s = wall_s
        self._permission_mode = permission_mode
        self._dispatch_root = dispatch_root
        self._rotate_at = float(rotate_at)
        self._rotate_requested = False

        # frozen by preflight()
        self._native_agents: frozenset[str] = frozenset()
        self._skip_in_prepass: frozenset[str] = frozenset()
        self._backend = None
        self._entry: dict = {}
        self._cwd: str | None = None
        self._disabled = False

        # lifecycle / latches
        self._lock = threading.Lock()
        self._turn_gate = threading.Lock()
        self._session_lock = threading.Lock()
        self._handle = None
        self._inflight = 0
        self._waiter_lock = threading.Lock()
        self._turn_seq = 0
        self._active_agent: str | None = None
        self._active_turn_seq = 0
        self._permission_expired_turn: int | None = None
        self._started: float | None = None
        self._dead = False
        self._exhausted = False
        self._degraded = False
        self._fallback_done: set[str] = set()
        self._active_registered = False
        self._closed = False
        self._closing = False
        self._closing_timeout = False

    # -- introspection -----------------------------------------------------
    @property
    def native_agents(self) -> frozenset[str]:
        return self._native_agents

    @property
    def skip_in_prepass(self) -> frozenset[str]:
        return self._skip_in_prepass

    @property
    def degraded(self) -> bool:
        with self._lock:
            return self._degraded

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def fallback_mode(self) -> str:
        return self._fallback_mode

    @property
    def closing_timeout(self) -> bool:
        return self._closing_timeout

    def executor_for(self, agent_id: str) -> str:
        if agent_id in self._native_agents and not self._disabled:
            return "acp"
        return runtime_kind(self._inner)

    def describe(self, agent_id: str) -> dict:
        if agent_id in self._native_agents and not self._disabled:
            return {"backend": self._backend_id}
        return {}

    def degrade(self, *, reason: str = "preflight", code: str = "native.preflight_failed",
                detail: str = "") -> None:
        """Disable the native path for this run (explicit fallback only)."""
        with self._lock:
            self._disabled = True
            self._degraded = True
        self._emit("native_fallback", {
            "agent_id": None, "from": "acp", "to": runtime_kind(self._inner),
            "reason": reason, "code": code, "detail": detail,
        })

    # -- preflight ---------------------------------------------------------
    def preflight(self) -> dict:
        """Validate + freeze the native set. Never spawns a dsh process."""
        from agent_bridge import factory as factory_mod
        from agent_bridge.roots import backend_run_cwd, dispatch_root as _root

        agents = self._agents_arg
        if agents is None:
            from agent_bridge.registry import load_registry
            data = load_registry()
            agents = frozenset((data.get("routing") or {}).get("map") or {})
        else:
            agents = frozenset(agents)

        if not agents:
            # Explicit empty set = native off (a legitimate rollback shape).
            self._disabled = True
            self._native_agents = frozenset()
            self._skip_in_prepass = frozenset()
            return {"native": [], "disabled": True}

        if not factory_mod.registry_gate():
            raise NativeConfigError(
                "NATIVE_RUNTIME=dsh requires AGENT_REGISTRY=1 (registry gate off)",
                code="native.registry_gate")

        from agent_bridge.registry import load_registry
        data = load_registry()
        route_map = frozenset((data.get("routing") or {}).get("map") or {})
        not_bound = sorted(agents - route_map)
        if not_bound:
            raise NativeConfigError(
                f"NATIVE_AGENTS {not_bound!r} are not bound in routing.map "
                f"({sorted(route_map)!r})", code="native.agents_not_bound")

        # Freeze BEFORE building the backend: if the build fails and the runner
        # degrades to `inner`, the pre-pass must still skip these agents (else
        # the role runs twice: pre-pass + inner).
        self._native_agents = frozenset(agents)
        self._skip_in_prepass = frozenset(agents)

        entry = (data.get("backends") or {}).get(self._backend_id)
        if not entry:
            raise NativeConfigError(
                f"backend {self._backend_id!r} is not declared in the registry",
                code="native.backend_missing")
        if not entry.get("enabled"):
            raise NativeConfigError(
                f"backend {self._backend_id!r} is disabled in the registry",
                code="native.backend_disabled")

        defaults = data.get("defaults") or {}
        budgets = entry.get("budgets") or {}
        budget = BudgetSpec(
            turns=self._max_turns,
            wall_s=self._wall_s,
            tool_calls=budgets.get("tool_calls", defaults.get("budget_tool_calls")),
            context_used=budgets.get("context_used"),
        )
        store = self._store
        if store is None:
            store = SQLiteOpStore(storage_path())
            self._store = store

        from agent_bridge.factory import env_overrides
        overrides = dict(env_overrides() or {})
        overrides.setdefault("dispatch_root", str(self._dispatch_root or _root()))
        overrides.setdefault("budget", budget)
        backend = factory_mod.build_backend(
            self._backend_id, store=store, emit=self._emit, overrides=overrides)
        if backend is None:
            raise NativeConfigError(
                f"backend {self._backend_id!r} is unreachable through the factory",
                code="native.backend_unreachable")

        self._backend = backend
        self._entry = entry
        set_sink = getattr(backend, "set_event_sink", None)
        if callable(set_sink):
            set_sink(self._sink)
        root = overrides.get("dispatch_root") or _root()
        self._cwd = str(backend_run_cwd(self._backend_id, entry, self._run_id,
                                        root=root))
        return {"native": sorted(self._native_agents), "cwd": self._cwd,
                "degraded": self.degraded}

    # -- emit --------------------------------------------------------------
    def _emit(self, ev_type: str, payload: dict | None = None) -> None:
        if self._closed or self._bus is None:
            return
        if ev_type == "spi_permission_expired":
            with self._lock:
                self._permission_expired_turn = self._active_turn_seq
        if ev_type == "dsh_session":
            # session-level event: no agent attribution in the payload
            self._bus.emit(ev_type, self._run_id, dict(payload or {}))
            return
        with self._lock:
            agent_id = self._active_agent
        data = dict(payload or {})
        data.setdefault("agent_id", agent_id)
        data.setdefault("backend_id", self._backend_id)
        self._bus.emit(ev_type, self._run_id, data, agent_id=agent_id)

    def _sink(self, event) -> None:
        """DshBackend event sink: normalised ACP frame -> trace event."""
        if self._closed:
            return
        kind = getattr(event, "type", None) or "unknown"
        payload = dict(getattr(event, "payload", {}) or {})
        if kind == "usage_update" and self._rotate_at > 0:
            raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else payload
            try:
                used = float(raw.get("used"))
                size = float(raw.get("size"))
                if size > 0 and used / size >= self._rotate_at:
                    self._rotate_requested = True
            except (TypeError, ValueError):
                pass
        with self._lock:
            agent_id = self._active_agent
            seq = self._active_turn_seq
        self._emit("dsh_turn_event", {
            "run_id": self._run_id,
            "agent_id": agent_id,
            "turn_seq": seq,
            "kind": kind,
            "payload": payload,
        })

    # -- session -----------------------------------------------------------
    def _maybe_rotate(self) -> None:
        """O6: close the current session before the NEXT turn when context is
        above ``rotate_at``; the next ``_ensure_session`` opens a fresh one."""
        with self._lock:
            if not self._rotate_requested or self._handle is None:
                return
            handle = self._handle
            self._handle = None
            self._rotate_requested = False
        try:
            handle.close(reason="context rotation")
        except Exception:
            pass
        self._emit("native_session_rotated", {
            "reason": "context", "backend": self._backend_id,
            "dsh_session_id": getattr(handle, "dsh_session_id", None)})

    def _ensure_session(self):
        self._maybe_rotate()
        if self._dead:
            raise SpiError("native session is dead (previous process death); "
                           "no reconnect", kind="process-died",
                           backend=self._backend_id, code="native.no_session")
        if self._handle is not None:
            return self._handle
        with self._session_lock:
            if self._handle is not None:
                return self._handle
            if self._backend is None:
                raise SpiError("preflight() was not run", kind="dispatch",
                               backend=self._backend_id,
                               code="native.no_preflight")
            spec = SessionSpec(
                run_id=self._run_id,
                platform_session_id=self._platform_session_id,
                cwd=self._cwd,
                permission_mode=self._permission_mode,
            )
            self._handle = self._backend.open_session(spec)
            return self._handle

    # -- queue -------------------------------------------------------------
    def _enter(self) -> None:
        with self._waiter_lock:
            if self._inflight >= self._queue_max:
                raise SpiError(
                    f"native queue is full ({self._inflight} >= {self._queue_max})",
                    kind="budget", backend=self._backend_id,
                    code="native.queue_full")
            self._inflight += 1
        try:
            deadline = time.monotonic() + self._queue_wait_s
            while True:
                if self._cancel_event is not None and self._cancel_event.is_set():
                    raise CancelledRun("run cancelled while waiting for a native turn")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SpiError(
                        f"timed out waiting for the native turn gate "
                        f"({self._queue_wait_s:.0f}s)", kind="budget",
                        backend=self._backend_id, code="native.queue_timeout")
                if self._turn_gate.acquire(timeout=min(_POLL_S, remaining)):
                    return
        except BaseException:
            with self._waiter_lock:
                self._inflight = max(0, self._inflight - 1)
            raise

    def _leave(self, acquired: bool) -> None:
        if not acquired:
            return
        try:
            self._turn_gate.release()
        except RuntimeError:
            pass
        with self._waiter_lock:
            self._inflight = max(0, self._inflight - 1)

    # -- AgentRuntime ------------------------------------------------------
    def execute(self, agent_id: str, prompt: str, context: dict) -> AgentResult:
        if self._closed or self._closing:
            raise SpiError("native runtime is closed", kind="dispatch",
                           backend=self._backend_id, code="native.closed")
        if agent_id not in self._native_agents:
            return self._run_inner(agent_id, prompt, context)
        if self._disabled:
            # The whole run degraded at preflight: this agent is native-owned but
            # executes on the inner runtime - mark it as a fallback so the
            # per-agent evidence stays honest.
            return self._run_inner(agent_id, prompt, context, fallback=True)
        if self._dead:
            # Fast-fail latch: never reconnect. With an explicit fallback, the
            # remaining native agents go to the inner runtime (once).
            if (self._fallback_mode == "crewai" and self._inner is not None
                    and agent_id not in self._fallback_done):
                with self._lock:
                    self._fallback_done.add(agent_id)
                    self._degraded = True
                self._emit("native_fallback", {
                    "agent_id": agent_id, "from": "acp",
                    "to": runtime_kind(self._inner), "reason": "process-died",
                    "code": "native.no_session",
                    "backend_session_id": getattr(self._handle, "dsh_session_id", None),
                })
                return self._run_inner(agent_id, prompt, context, fallback=True)
            raise SpiError("native session is dead (previous process death); "
                           "no reconnect", kind="process-died",
                           backend=self._backend_id, code="native.no_session")
        return self._execute_native(agent_id, prompt, context)

    def native_turn(self, text: str, *, agent_id: str = "platform-llm") -> str:
        """Low-level turn used by NativeLLMAdapter (no AgentRuntime routing)."""
        return self._execute_native(agent_id, text, {}).output

    # -- native turn -------------------------------------------------------
    def _execute_native(self, agent_id: str, prompt: str, context: dict) -> AgentResult:
        if self._dead:
            raise SpiError("native session is dead (previous process death); "
                           "no reconnect", kind="process-died",
                           backend=self._backend_id, code="native.no_session")
        if self._exhausted:
            raise SpiError("native turn budget is exhausted for this run",
                           kind="budget", backend=self._backend_id,
                           code="native.exhausted")
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise CancelledRun("run cancelled before native turn")
        acquired = False
        seq = 0
        try:
            self._enter()
            acquired = True
            if self._exhausted:
                raise SpiError("native turn budget is exhausted for this run",
                               kind="budget", backend=self._backend_id,
                               code="native.exhausted")
            now = time.monotonic()
            if self._started is None:
                self._started = now
            remaining = self._wall_s - (now - self._started)
            if remaining <= WALL_RESERVE_S:
                raise SpiError(
                    f"run wall budget nearly exhausted ({remaining:.0f}s left)",
                    kind="budget", backend=self._backend_id,
                    code="native.wall_exhausted")
            handle = self._ensure_session()
            setter = getattr(self._backend, "set_turn_timeout", None)
            if callable(setter):
                setter(min(600.0, max(30.0, remaining)))

            with self._lock:
                self._turn_seq += 1
                seq = self._turn_seq
                self._active_turn_seq = seq
                self._active_agent = agent_id
            if not self._active_registered:
                try:
                    from agent_bridge import gate_counters
                    gate_counters.register_active(self._backend_id)
                    self._active_registered = True
                except Exception:
                    pass
            try:
                from agent_bridge import gate_counters
                gate_counters.record_dispatch(self._backend_id)
            except Exception:
                pass
            self._start_watchdog(handle, seq)
            try:
                handle.prompt(prompt)
            finally:
                with self._lock:
                    self._active_agent = None
                    self._active_turn_seq = 0

            result = getattr(handle, "last_result", None)
            if result is None:
                raise SpiError("native turn produced no result", kind="error",
                               backend=self._backend_id, code="native.no_result")
            self._raise_for_result(result, seq)
            meta = {
                "runtime": "acp",
                "executor": "acp",
                "backend": self._backend_id,
                "backend_session_id": getattr(handle, "dsh_session_id", None),
                "session_scope": f"{self._run_id}:{agent_id}",
                "stop_reason": result.stop_reason,
                "usage": dict(result.usage or {}),
                "fallback": False,
                "prompt_chars": len(prompt),
            }
            return AgentResult(agent_id=agent_id,
                               output=result.final_text or "", meta=meta)
        except CancelledRun:
            raise
        except SpiError as exc:
            return self._fail(agent_id, prompt, context, exc, seq=seq)
        except Exception as exc:  # never leak a raw dsh exception to the graph
            err = SpiError(f"{type(exc).__name__}: {exc}", kind="error",
                           backend=self._backend_id, cause=exc,
                           code="native.unexpected")
            return self._fail(agent_id, prompt, context, err, seq=seq)
        finally:
            self._leave(acquired)

    def _raise_for_result(self, result, seq: int) -> None:
        if result.error:
            raise SpiError(result.error, kind="process-died",
                           backend=self._backend_id, code="native.process_died")
        if result.stop_reason == "cancelled":
            if self._cancel_event is not None and self._cancel_event.is_set():
                raise CancelledRun("run cancelled during the native turn")
            with self._lock:
                permission_hit = self._permission_expired_turn == seq
            if permission_hit:
                raise SpiError("permission request was not answered in time",
                               kind="timeout", backend=self._backend_id,
                               code="native.permission_timeout")
            if getattr(result, "turn_timed_out", False):
                raise SpiError("native turn timed out", kind="timeout",
                               backend=self._backend_id, code="native.turn_timeout")
            if getattr(result, "budget_exceeded", False):
                raise SpiError("native budget exceeded", kind="budget",
                               backend=self._backend_id, code="native.budget")
            raise SpiError("native turn cancelled", kind="remote",
                           backend=self._backend_id, code="native.cancelled")

    def _fail(self, agent_id: str, prompt: str, context: dict, exc: SpiError,
              *, seq: int) -> AgentResult:
        if exc.kind == "process-died":
            self._dead = True
        if exc.kind == "budget" and exc.code != "native.queue_full":
            self._exhausted = True
        self._emit("native_role_error", {
            "agent_id": agent_id, "backend": self._backend_id,
            "kind": exc.kind, "code": exc.code, "error": str(exc),
            "backend_session_id": getattr(self._handle, "dsh_session_id", None),
            "turn_seq": seq,
        })
        if self._can_fallback(agent_id, exc):
            with self._lock:
                self._fallback_done.add(agent_id)
                self._degraded = True
            try:
                res = self._run_inner(agent_id, prompt, context, fallback=True)
            except Exception as inner_exc:
                raise SpiError(
                    f"native failed ({exc.code}) and fallback also failed: "
                    f"{type(inner_exc).__name__}: {inner_exc}",
                    kind=exc.kind, backend=self._backend_id, cause=inner_exc,
                    code="native.fallback_failed") from inner_exc
            self._emit("native_fallback", {
                "agent_id": agent_id, "from": "acp",
                "to": runtime_kind(self._inner), "reason": exc.kind,
                "code": exc.code,
                "backend_session_id": getattr(self._handle, "dsh_session_id", None),
            })
            return res
        raise exc

    def _can_fallback(self, agent_id: str, exc: SpiError) -> bool:
        if self._fallback_mode != "crewai" or self._inner is None:
            return False
        if exc.kind == "config" or exc.code in ("native.config", "native.bad_env",
                                                "native.bad_mode"):
            return False
        if agent_id in self._fallback_done:
            return False
        return True

    def _run_inner(self, agent_id: str, prompt: str, context: dict,
                   *, fallback: bool = False) -> AgentResult:
        if self._inner is None:
            raise SpiError(
                f"agent {agent_id!r} is not native and no inner runtime is "
                f"configured", kind="dispatch", backend=self._backend_id,
                code="native.no_inner")
        res = self._inner.execute(agent_id, prompt, context)
        kind = runtime_kind(self._inner)
        meta = dict(getattr(res, "meta", {}) or {})
        meta.update({
            "runtime": kind,
            "executor": kind,
            "backend": None,
            "backend_session_id": None,
            "fallback": fallback,
            "prompt_chars": len(prompt),
        })
        return AgentResult(agent_id=agent_id, output=res.output, meta=meta)

    # -- watchdog / close --------------------------------------------------
    def _start_watchdog(self, handle, seq: int) -> None:
        if self._cancel_event is None:
            return
        ev = self._cancel_event

        def _watch():
            while not ev.wait(_POLL_S):
                with self._lock:
                    active = self._active_turn_seq
                if self._closed or active != seq:
                    return
                if ev.is_set():
                    try:
                        handle.cancel(reason="host cancel")
                    except Exception:
                        pass
                    return

        threading.Thread(target=_watch, name=f"w4-cancel-{seq}",
                         daemon=True).start()

    def close(self, *, reason: str = "run finished") -> None:
        if self._closed:
            return
        self._closing = True
        handle = self._handle
        if handle is not None:
            try:
                handle.cancel(reason=reason)
            except Exception:
                pass
        acquired = self._turn_gate.acquire(timeout=CLOSE_GATE_WAIT_S)
        if acquired:
            self._turn_gate.release()
        else:
            self._closing_timeout = True
            self._emit("native_close_timeout", {"waited_s": CLOSE_GATE_WAIT_S})
        if handle is not None:
            try:
                handle.close(reason=reason)
            except Exception:
                pass
        if self._backend is not None:
            try:
                self._backend.shutdown()
            except Exception:
                pass
        if self._active_registered:
            try:
                from agent_bridge import gate_counters
                gate_counters.unregister_active(self._backend_id)
            except Exception:
                pass
            self._active_registered = False
        self._closed = True


class NativeLLMAdapter:
    """``core.llm.LLMClient`` face over one ACP turn.

    W4-1 defines the interface but does NOT wire it: dsh has no forced JSON
    output, and plan/review are platform-LLM nodes (a different risk surface).
    ``json_mode`` is therefore advisory — the caller must parse/repair and a
    parse failure is the caller's to raise, never a fabricated ``{}``.
    """

    def __init__(self, runtime: ACPRuntime, *, agent_id: str = "platform-llm") -> None:
        self._runtime = runtime
        self._agent_id = agent_id

    def complete(self, prompt: str, *, system: str = "", json_mode: bool = False) -> str:
        text = f"{system}\n{prompt}".strip() if system else prompt
        return self._runtime.native_turn(text, agent_id=self._agent_id)
