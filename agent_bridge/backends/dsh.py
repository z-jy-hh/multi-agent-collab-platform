"""R17-W5a: ``DshBackend`` - the SPI adapter that COMPOSES dsh_bridge.

COMPOSITION, NOT DUPLICATION (work order section 2 item 1): every protocol,
budget, permission, cancellation and deadline semantic stays in ``dsh_bridge``
(DshSessionManager / DshTurnRunner / PermissionGate / BudgetGate). This
adapter only:

- projects registry configuration onto the composed constructors
  (``node_bin`` / ``dsh_bin_js`` from the command template, ``mode_allowed``
  as the dispatch-side tier whitelist, ``version_lock`` into the handshake,
  ``deadline_*`` into the runner's gate/timer parameters);
- branches on CAPABILITY FLAGS (design section 2.4): ``usage_shape`` decides
  which budget knobs are projected (a shape never fed by the wire cannot be
  projected - the registry lint already rejects that, and this adapter
  re-checks); ``permission_has_path`` must be ``false`` (the adapter has no
  path evidence and never branches on paths - a registry claiming otherwise
  is a constructor error); ``cancel_is_notification`` must be ``true`` (the
  composed runner only knows the notification-shaped cancel, measured V5');
- answers ``session/request_permission`` through a thread-safe slot fed by
  :meth:`_DshSession.answer_permission` (the SPI face) and drained by a
  human-hook adapter inside the composed gate - the gate keeps its own 45 s
  deadline, so an unanswered SPI permission still auto-rejects + cancels
  (measured V4);
- normalises errors (``agent_bridge/errors.py``) and enforces the
  PLATFORM-side budget counters (``turns`` / ``tool_calls`` / ``wall_s``),
  which no backend reports by itself.

``dsh_bridge/`` is untouched (P2 red line): zero imports changed, zero
signatures changed - the P0 blob anchors still apply and C3 re-proves them.
"""

from __future__ import annotations

import os
import re
import threading
import time

from agent_bridge.errors import SpiError
from agent_bridge.spi import (
    AgentEvent,
    BudgetSpec,
    CapabilityFlags,
    PermissionDecision,
    PermissionRequest,
    RunAuthorization,
    SessionSpec,
    TaskResult,
    TaskSpec,
)
from dsh_bridge.acp_client import (
    AcpError,
    AcpProcessDied,
    AcpProtocolError,
    AcpRemoteError,
    AcpTimeout,
)
from dsh_bridge.runner import DshTurnRunner
from dsh_bridge.session import DshDispatchError, DshEnvironmentError, DshSessionManager

_KIND_BY_EXC = (
    (AcpProtocolError, "protocol"),
    (AcpRemoteError, "remote"),
    (AcpProcessDied, "process-died"),
    (AcpTimeout, "timeout"),
    (DshEnvironmentError, "environment"),
    (DshDispatchError, "dispatch"),
    (AcpError, "error"),
)


def _normalize(exc: BaseException, backend: str) -> SpiError:
    """Work order item 3: dsh exception families -> one SPI error type.

    N6: an explicit ``code`` on the original SpiError must survive normalization
    (otherwise the machine-readable reason is silently downgraded to kind.error).
    """
    for exc_type, kind in _KIND_BY_EXC:
        if isinstance(exc, exc_type):
            return SpiError(str(exc), kind=kind, backend=backend, cause=exc,
                            code=getattr(exc, "code", None))
    return SpiError(f"{type(exc).__name__}: {exc}", kind="error", backend=backend,
                    cause=exc, code=getattr(exc, "code", None))

_CAPABILITY_NAMES = (
    "protocol_version", "transport", "permission_has_path", "permission_options",
    "usage_shape", "cancel_is_notification", "server_times_out", "supports_resume",
    "supports_background", "paid", "single_prompt_per_session", "single_root_cwd",
    "single_handler_slot", "audit_hash_chain", "sandbox_semantics",
    "supports_elicitation", "egress",
)

# what the COMPOSITION can honour - a registry claiming anything else is a
# constructor error (fail-closed, C5), never a silent downgrade
_UNSUPPORTED_IF_TRUE = ("permission_has_path",)
_MUST_BE_TRUE = ("cancel_is_notification", "single_handler_slot",
                 "single_prompt_per_session", "single_root_cwd")


def _capabilities_from_entry(backend: str, entry: dict) -> CapabilityFlags:
    caps = entry.get("capabilities") or {}
    missing = [n for n in _CAPABILITY_NAMES if n not in caps]
    if missing:
        raise SpiError(f"registry entry missing capability flags: {missing}",
                       kind="dispatch", backend=backend)
    for name in _UNSUPPORTED_IF_TRUE:
        if caps[name]["value"] is True:
            raise SpiError(
                f"registry claims {name}=true but the composition cannot "
                f"honour it (no path evidence / no such branch)", kind="dispatch",
                backend=backend)
    for name in _MUST_BE_TRUE:
        if caps[name]["value"] is not True:
            raise SpiError(
                f"registry claims {name}=false but the composition only "
                f"implements the flagged behaviour", kind="dispatch",
                backend=backend)
    return CapabilityFlags(
        protocol_version=caps["protocol_version"]["value"],
        transport=caps["transport"]["value"],
        permission_has_path=caps["permission_has_path"]["value"],
        permission_options=frozenset(caps["permission_options"]["value"]),
        usage_shape=caps["usage_shape"]["value"],
        cancel_is_notification=caps["cancel_is_notification"]["value"],
        server_times_out=caps["server_times_out"]["value"],
        supports_resume=caps["supports_resume"]["value"],
        supports_background=caps["supports_background"]["value"],
        paid=caps["paid"]["value"],
        single_prompt_per_session=caps["single_prompt_per_session"]["value"],
        single_root_cwd=caps["single_root_cwd"]["value"],
        single_handler_slot=caps["single_handler_slot"]["value"],
        audit_hash_chain=caps["audit_hash_chain"]["value"],
        sandbox_semantics=caps["sandbox_semantics"]["value"],
        supports_elicitation=caps["supports_elicitation"]["value"],
        egress=caps["egress"]["value"],
        evidence={n: caps[n]["evidence"] for n in _CAPABILITY_NAMES},
    )


def _resolve_command(entry: dict, *, node_bin: str, dsh_bin_js: str | None) -> tuple[str, str]:
    """Project the registry command template onto the composed constructor.

    DshSessionManager spawns ``[node_bin, dsh_bin_js, "--profile", "acp"]``
    itself; the template must therefore be exactly that shape (placeholders
    plus the literal tail) or the seam is wrong - fail-closed."""
    command = entry.get("command") or []
    placeholder_values = {"node": node_bin,
                          "dsh_bin": dsh_bin_js or os.environ.get("DSH_BIN") or ""}
    # Fail fast: an empty binary used to spawn `node "" --profile acp`, which
    # hung until the 60s initialize timeout and looked like a protocol bug.
    if not placeholder_values["dsh_bin"]:
        raise SpiError("dsh JS entry is unresolved (set DSH_BIN to the "
                       "deepseek-harness bin.js path)", kind="environment",
                       backend="dsh", code="backend.command_unresolved")
    resolved = []
    for arg in command:
        match = re.fullmatch(r"\{([a-z_]+)\}", arg)
        if match:
            value = placeholder_values.get(match.group(1))
            if value is None:
                raise SpiError(f"command placeholder {{{match.group(1)}}} has no "
                               f"value (set DSH_BIN?)", kind="environment",
                               backend=entry.get("backend_id", "dsh"))
            resolved.append(value)
        else:
            resolved.append(arg)
    if resolved[:2] != [placeholder_values["node"], placeholder_values["dsh_bin"]] \
            or resolved[2:] != ["--profile", "acp"]:
        raise SpiError(f"registry command tail {resolved[2:]!r} does not match "
                       f"the composed constructor's hardcoded ['--profile', 'acp']",
                       kind="dispatch", backend="dsh")
    return resolved[0], resolved[1]


class _BudgetEnforcer:
    """Platform-side counters (turns / tool_calls / wall_s) - the part of
    BudgetSpec NO backend reports. Mid-turn enforcement rides the event
    stream (tool_calls), per-turn caps between prompts (turns), and the
    run-level wall clock is checked between turns; the in-turn wall deadline
    is the runner's own ``turn_timeout`` (deadline_prompt_s)."""

    def __init__(self, budget: BudgetSpec) -> None:
        self.budget = budget
        self.tool_calls = 0
        self.turns = 0
        self.started: float | None = None
        self.tripped: str | None = None

    def start_turn(self) -> None:
        if self.budget.turns is not None and self.turns >= self.budget.turns:
            raise SpiError(f"turns budget exhausted ({self.turns} >= "
                           f"{self.budget.turns})", kind="budget", backend="dsh")
        if self.budget.wall_s is not None and self.started is not None:
            if time.monotonic() - self.started > self.budget.wall_s:
                raise SpiError(f"wall clock budget exhausted "
                               f"({self.budget.wall_s:.0f}s)", kind="budget",
                               backend="dsh")
        self.started = self.started or time.monotonic()
        self.turns += 1

    def on_event(self, type_name: str) -> None:
        if type_name == "tool_call" and self.budget.tool_calls is not None:
            self.tool_calls += 1
            if self.tool_calls > self.budget.tool_calls and not self.tripped:
                self.tripped = (f"tool_calls budget exceeded "
                                f"({self.tool_calls} > {self.budget.tool_calls})")


class _DshSession:
    """SPI session handle wrapping ONE ``DshRunSession`` (composition).

    Satisfies ``agent_bridge.spi.AgentSessionHandle`` structurally (the
    5 session-level methods); no Protocol inheritance needed."""

    def __init__(self, backend: "DshBackend", session, spec: SessionSpec,
                 budget: BudgetSpec) -> None:
        self._backend = backend
        self._session = session
        self._spec = spec
        self._enforcer = _BudgetEnforcer(budget)
        self._events: list[AgentEvent] = []
        self._lock = threading.Lock()
        self._in_flight = False
        self._pending: dict = {}          # request/decision slot for the gate
        self._pending_lock = threading.Lock()
        self._runner = None
        self._last_result = None
        self._result_schema = None            # set per-task by execute_task
        self._closed = False

    # -- SPI: prompt ------------------------------------------------------------

    def prompt(self, text: str) -> None:
        if self._closed:
            raise SpiError("session is closed", kind="dispatch", backend="dsh")
        with self._lock:
            if self._in_flight:
                raise SpiError(
                    "another prompt is already in flight on this session "
                    "(single_prompt_per_session)", kind="dispatch", backend="dsh")
            self._in_flight = True
        try:
            self._enforcer.start_turn()
            runner = self._backend._build_runner(self._session, self)
            self._runner = runner
            result = runner.run_turn(text)
            self._last_result = result
            if result.error:
                # process death (or any loud composed failure) - NEVER silent
                raise SpiError(result.error, kind="process-died", backend="dsh")
        except SpiError:
            raise
        except Exception as exc:              # dsh families -> one SPI error
            raise _normalize(exc, self._backend.backend_id) from exc
        finally:
            with self._lock:
                self._in_flight = False

    # -- SPI: events ------------------------------------------------------------

    def events(self):
        return iter(list(self._events))

    def _collect(self, event_type: str, payload: dict) -> None:
        """Runner/gate emit hook -> normalised AgentEvent + budget counters.
        The five measured update kinds lose their ``dsh_`` prefix so the
        conformance suite stays backend-agnostic; bridge-level events keep
        their full name. ``raw`` is ALWAYS the original ACP update dict (the
        mapper's envelope is unwrapped) so nothing can be lost."""
        if event_type == "dsh_turn_event":
            kind = payload.get("kind", "")
            name = kind[4:] if kind.startswith("dsh_") else kind
            mapped_payload = payload.get("payload") or {}
            inner_raw = mapped_payload.get("raw")
            raw = inner_raw if isinstance(inner_raw, dict) else mapped_payload
            ev = AgentEvent(type=name, payload=mapped_payload, raw=raw,
                            ts=time.time())
            self._events.append(ev)
            self._enforcer.on_event(name)
            if self._enforcer.tripped:
                self._backend._emit("spi_budget_exceeded", {
                    "run_id": self._spec.run_id, "reason": self._enforcer.tripped})
                self.cancel(reason=self._enforcer.tripped)
        else:
            ev = AgentEvent(type=event_type, payload=payload, raw=payload,
                            ts=time.time())
            self._events.append(ev)
        # W4-1: live event sink (the run-level runtime forwards these to the
        # platform bus; a broken sink must never kill the turn).
        sink = getattr(self._backend, "_event_sink", None)
        if sink is not None:
            try:
                sink(ev)
            except Exception as exc:          # contained, but visible
                try:
                    self._backend._emit("dsh_bridge_error", {
                        "run_id": self._spec.run_id, "where": "event_sink",
                        "error": f"{type(exc).__name__}: {exc}"})
                except Exception:
                    pass

    # -- SPI: permission (the answer_permission face) ----------------------------

    def wait_permission(self, timeout: float = 30.0):
        """OPTIONAL extension (conformance convenience): block until a
        permission request is pending, return it (or None on timeout)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._pending_lock:
                if self._pending.get("request") is not None:
                    return self._pending["request"]
            time.sleep(0.02)
        return None

    def answer_permission(self, request: PermissionRequest,
                          decision: PermissionDecision) -> None:
        with self._pending_lock:
            pending = self._pending.get("request")
            if pending is None or pending != request:
                raise SpiError("no matching permission request is pending",
                               kind="dispatch", backend="dsh")
            if decision.option_id not in request.options:
                # only optionIds the SERVER offered may ever go out (F-2)
                raise SpiError(f"option_id {decision.option_id!r} was not "
                               f"offered by the server {request.options!r}",
                               kind="dispatch", backend="dsh")
            self._pending["decision"] = decision
            self._pending["answered_at"] = time.time()
            self._pending["request"] = None      # N1: clear pending (answered)
            self._pending["event"].set()
            self._backend._emit("permission_answered", {
                "run_id": self._spec.run_id,
                "tool_call_id": decision.option_id and request.tool_call_id,
                "option_id": decision.option_id,
                "basis": decision.basis})

    def _human_hook(self, params: dict):
        """Adapter drained by the composed PermissionGate (its own 45s
        deadline; an unanswered hook auto-rejects and cancels - measured V4).
        ``permission_has_path`` is false: only toolCallId/options are read."""
        request = PermissionRequest(
            tool_call_id=(params.get("toolCall") or {}).get("toolCallId"),
            options=tuple(o.get("optionId") for o in params.get("options", [])),
            raw=params)
        event = threading.Event()
        with self._pending_lock:
            self._pending = {"request": request, "event": event,
                             "decision": None}
        self._backend._emit("spi_permission_request", {
            "run_id": self._spec.run_id, "tool_call_id": request.tool_call_id,
            "options": list(request.options)})
        if not event.wait(timeout=self._backend._permission_timeout):
            # W0-6: distinguish "nobody answered" from "a human rejected".
            # F6: only clear the slot if it still holds THIS request; a newer
            # request may have replaced it, and wiping it would make that one
            # unanswerable (and its card would be cleared by our expired event).
            timed_out = False
            with self._pending_lock:
                if self._pending.get("request") is request:
                    self._pending = {}
                    timed_out = True
            if timed_out:
                self._backend._emit("spi_permission_expired", {
                    "run_id": self._spec.run_id,
                    "tool_call_id": request.tool_call_id,
                    "timeout_s": self._backend._permission_timeout})
            return None                      # gate turns None into reject-once
        decision = self._pending.get("decision")
        return decision.option_id if decision is not None else None

    # -- SPI: cancel / close -----------------------------------------------------

    def cancel(self, *, reason: str) -> None:
        if self._runner is not None:
            # notification-shaped by construction (runner D6); the capability
            # flag is asserted at construction time
            self._runner.cancel()

    def close(self, *, reason: str) -> None:
        if self._closed:
            return                            # idempotent
        self._closed = True
        # W4-1: wake a permission hook that is blocked in the 45 s wait, so a
        # run-level close cannot be delayed by a card nobody will answer.
        with self._pending_lock:
            pending = self._pending
            self._pending = {}
        event = (pending or {}).get("event")
        if event is not None:
            try:
                event.set()
            except Exception:
                pass
        try:
            self._backend._manager.close_session(self._spec.run_id)
        except Exception as exc:              # best-effort: a dead client must
            self._backend._emit("spi_close_failed", {   # not break the close
                "run_id": self._spec.run_id, "error": str(exc)})
        finally:
            # W4-1: the registry is keyed by run_id and used to outlive the
            # session (F9) - a closed handle must not answer a stale card.
            try:
                from agent_bridge.session_registry import unregister
                unregister(self._spec.run_id)
            except Exception:
                pass

    # -- composition surface (not SPI; used by execute_task / tests) -------------

    @property
    def last_result(self):
        return self._last_result

    @property
    def dsh_session_id(self) -> str | None:
        """The real ACP session id (evidence for agent_output.backend_session_id)."""
        return getattr(self._session, "dsh_session_id", None)


class DshBackend:
    """SPI backend for dsh, composed from the existing dsh_bridge parts."""

    backend_id = "dsh"

    def __init__(self, entry: dict, *, defaults: dict, store=None, emit=None,
                 node_bin: str = "node", dsh_bin_js: str | None = None,
                 dispatch_root=None, acl_runner=None, price_table=None,
                 budget: BudgetSpec | None = None,
                 client_extra_args: list[str] | None = None,
                 permission_timeout: float | None = None,
                 turn_timeout: float | None = None) -> None:
        self.capabilities = _capabilities_from_entry(self.backend_id, entry)
        self._entry = entry
        self._defaults = defaults
        self._budget = budget or BudgetSpec()
        self._mode_allowed = frozenset(
            entry["permission"]["mode_allowed"])     # closed set from registry
        node, bin_js = _resolve_command(entry, node_bin=node_bin,
                                        dsh_bin_js=dsh_bin_js)
        # The lock may be pinned per machine: DSH_EXPECTED_VERSION was always in
        # command_env.allow, so honour it instead of forcing a registry edit when
        # the deployed CLI reports a different (but known) version.
        version_lock = (os.environ.get("DSH_EXPECTED_VERSION")
                        or (entry.get("version_lock") or {}).get("agent_version"))
        self._manager = DshSessionManager(
            bin_js, store=store, emit=emit, node_bin=node,
            dispatch_root=dispatch_root,
            expected_agent_version=version_lock, acl_runner=acl_runner,
            client_extra_args=client_extra_args)
        self._permission_timeout = permission_timeout or defaults["deadline_permission_s"]
        self._turn_timeout = turn_timeout or defaults["deadline_prompt_s"]
        self._store = store
        self._emit = emit or (lambda t, p: None)
        self._price_table = price_table
        self._sessions: dict[str, _DshSession] = {}
        self._event_sink = None

    # -- W4-1 additive surface (never changes the SPI signatures) -----------------

    def set_event_sink(self, sink) -> None:
        """Forward every normalised ACP frame to a run-level sink (optional)."""
        self._event_sink = sink

    def set_turn_timeout(self, seconds: float) -> None:
        """Clamp the NEXT turn's deadline (run wall budget); additive setter."""
        self._turn_timeout = float(seconds)

    # -- SPI: session factory ------------------------------------------------------

    def open_session(self, spec: SessionSpec) -> AgentSessionHandle:
        if spec.permission_mode not in self._mode_allowed:
            raise SpiError(
                f"permission mode {spec.permission_mode!r} is not in the "
                f"registry whitelist {sorted(self._mode_allowed)} (closed set)",
                kind="dispatch", backend=self.backend_id)
        try:
            session = self._manager.start(spec.run_id, spec.platform_session_id,
                                          cwd=spec.cwd,
                                          permission_mode=spec.permission_mode)
        except SpiError:
            raise
        except Exception as exc:              # dsh families -> one SPI error
            raise _normalize(exc, self.backend_id) from exc
        handle = _DshSession(self, session, spec, self._budget)
        self._sessions[spec.run_id] = handle
        # R17-P0a: register the handle so the API permission card can find it
        from agent_bridge.session_registry import register
        register(spec.run_id, handle)
        return handle

    # -- composition seam: the usage_shape branch (design section 2.4) ----------
    #
    # The registry lint (P1) already rejects budgets that can never fire for a
    # backend's usage_shape; this branch is the runtime half - ONLY knobs the
    # wire actually feeds are projected into the composed runner, everything
    # else stays with the platform-side counters in the enforcer.

    def _build_runner(self, session, handle: "_DshSession") -> DshTurnRunner:
        budget = handle._enforcer.budget
        kwargs: dict = {}
        shape = self.capabilities.usage_shape
        if shape == "context_used_size" and budget.context_used is not None:
            kwargs["budget_context_used"] = budget.context_used
        elif shape == "token_family":
            if budget.tokens is not None:
                kwargs["budget_tokens"] = budget.tokens
            if budget.money is not None:
                kwargs["budget_money"] = budget.money
        # shape == "none": no backend-fed budget exists to project
        return DshTurnRunner(
            session, emit=handle._collect, store=self._store,
            human_hook=handle._human_hook,
            permission_timeout=self._permission_timeout,
            turn_timeout=self._turn_timeout,
            result_schema=handle._result_schema,
            price_table=self._price_table, **kwargs)

    # -- SPI: one-shot task ---------------------------------------------------------

    def execute_task(self, task: TaskSpec, *, budget: BudgetSpec,
                     authz: RunAuthorization) -> TaskResult:
        if self.capabilities.paid and authz.consent_token is None:
            raise SpiError("paid backend requires a consent token",
                           kind="authz", backend=self.backend_id)
        handle = self.open_session(SessionSpec(
            run_id=task.run_id, platform_session_id=task.platform_session_id,
            cwd=task.cwd, permission_mode=task.permission_mode))
        handle._enforcer.budget = budget              # per-task budget projection
        handle._result_schema = task.result_schema    # per-task result contract
        audit_refs = {"backend": self.backend_id,
                      "session_key": f"dsh_session::{task.run_id}",
                      "backend_session_id": handle._session.dsh_session_id}
        try:
            handle.prompt(task.task_text)
            result = handle.last_result
            usage = dict(result.usage) if result else {}
            return TaskResult(
                stop_reason=result.stop_reason if result else None,
                final_text=result.final_text if result else "",
                usage=usage,
                events=list(handle.events()),
                result_data=result.result_data if result else None,
                validation_errors=result.result_validation_errors if result else [],
                error=result.error if result else None,
                audit_refs=audit_refs)
        except SpiError as exc:
            return TaskResult(error=str(exc),
                              events=list(handle.events()), audit_refs=audit_refs)
        finally:
            handle.close(reason="execute_task finished")

    # -- optional SPI lifecycle -------------------------------------------------------

    def resume_session(self, run_id: str, dsh_session_id: str, *,
                       platform_session_id: str,
                       cwd: str | None = None) -> None:
        """B6/E8 resume face: continue a previous session.

        Only available when ``capabilities.supports_resume`` is true (dsh:
        measured true via session/list+resume). If the session is not found,
        the error is LOUD (SpiError from the manager) - never a silent new
        session.

        NOTE (real-machine find): the manager requires ``cwd`` (single-root
        contract); omitting it raised TypeError instead of resuming. The default
        mirrors the dispatch boundary used by execute_task.
        """
        if not self.capabilities.supports_resume:
            raise SpiError(
                f"resume not supported by {self.backend_id} "
                f"(capabilities.supports_resume=false, assumed-fail-closed)",
                kind="dispatch", backend=self.backend_id)
        if cwd is None:
            from agent_bridge.roots import dispatch_root as _root
            cwd = str(_root() / "dsh_dispatch" / run_id)
        try:
            self._manager.resume(run_id, platform_session_id, dsh_session_id,
                                 cwd=cwd)
        except SpiError:
            raise
        except Exception as exc:
            raise _normalize(exc, self.backend_id) from exc

    def shutdown(self) -> None:
        self._manager.shutdown()
