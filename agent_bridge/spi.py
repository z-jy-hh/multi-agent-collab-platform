"""R17-W1: signature-level SPI for agent_bridge (design v3 §2; NO behaviour).

Types and Protocol signatures only — this module is the contract the
implementation round (W5 dsh backend / W6 wb backend) will implement. It
performs no I/O, spawns nothing, and imports nothing from dsh_bridge (the
dependency direction is agent_bridge -> core, never core -> agent_bridge,
layercheck red line). Capability flags mirror design §2.4: all 17 are
required, the registry lints against them (§3.2), and every flag carries an
``evidence`` tag (``measured@…`` / ``static@…`` / ``assumed-fail-closed``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol, runtime_checkable

# closed vocabularies (design §2.4 / §3.2 — lint rejects values outside these)
USAGE_SHAPES = ("none", "context_used_size", "token_family", "acp_usage_updates")
SANDBOX_SEMANTICS = ("acl-single-root", "builtin-vendor", "none")
EVIDENCE_PREFIXES = ("measured@", "static@", "assumed-fail-closed")


@dataclass(frozen=True)
class CapabilityFlags:
    """The 17 flags of design §2.4 — ALL required; absence is a load error.

    ``evidence`` maps flag name -> provenance string; an upgrade from
    ``assumed-fail-closed`` to ``measured`` requires a real-machine probe
    (W12), never an edit of belief."""

    protocol_version: int
    transport: str
    permission_has_path: bool
    permission_options: frozenset[str]
    usage_shape: str
    cancel_is_notification: bool
    server_times_out: bool
    supports_resume: bool
    supports_background: bool
    paid: bool
    single_prompt_per_session: bool
    single_root_cwd: bool
    single_handler_slot: bool
    audit_hash_chain: bool
    sandbox_semantics: str
    supports_elicitation: bool
    egress: bool
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BudgetSpec:
    """Per-dispatch budget (design §2.5/§5.2).

    ``tool_calls`` / ``wall_s`` / ``turns`` are platform-side counters,
    backend-agnostic and always enforced; the other three depend on
    ``capabilities.usage_shape`` — a budget set against the wrong shape is a
    registry lint error (T5 in §6.3), never a silent no-op (F7/F10)."""

    context_used: int | None = None
    tokens: int | None = None
    money: float | None = None
    tool_calls: int | None = None
    wall_s: float | None = None
    turns: int | None = None


@dataclass(frozen=True)
class RunAuthorization:
    """Per-run authorization (design §6.2).

    A backend with ``paid=true`` (or, once wired, any egressing backend per
    the N3 data policy) must be refused by the dispatch gate BEFORE any spawn
    when the corresponding consent/opt-in is absent."""

    consent_token: str | None = None
    data_opt_in: bool = False
    permission_mode: str | None = None
    owner_note: str | None = None


@dataclass(frozen=True)
class SessionSpec:
    """One dispatch = one session with an immutable single-root cwd (F-6)."""

    run_id: str
    platform_session_id: str
    cwd: str
    permission_mode: str = "workspace-write"


@dataclass(frozen=True)
class TaskSpec:
    """One-shot unit for :meth:`AgentBackend.execute_task` (design §2.2)."""

    run_id: str
    platform_session_id: str
    task_text: str
    cwd: str
    permission_mode: str = "workspace-write"
    result_schema: dict | None = None
    result_filename: str = "result.json"


@dataclass
class AgentEvent:
    """Normalised backend event (design §2.3): the five measured kinds plus
    ``unknown`` (archived verbatim and counted — never dropped, never
    guessed). ``raw`` ALWAYS travels so a backend payload change cannot lose
    information."""

    type: str
    payload: dict
    raw: dict
    ts: float


@dataclass(frozen=True)
class PermissionRequest:
    """What the backend asked. dsh real frames carry ONLY
    ``sessionId / toolCall.toolCallId / options`` (F-2) — no path evidence —
    so the policy layer must never branch on paths for such backends."""

    tool_call_id: str | None
    options: tuple[str, ...]
    raw: dict


@dataclass(frozen=True)
class PermissionDecision:
    """The only decision shape allowed out of the policy layer (design §5.1).

    ``option_id`` must be one the SERVER offered in the same frame; inventing
    optionIds is a defect. ``basis`` is one of ``default-deny`` /
    ``human-hook`` / ``run-tier``."""

    option_id: str
    reason: str
    basis: str
    latency_ms: float = 0.0
    timed_out: bool = False


@dataclass
class TaskResult:
    """Result of :meth:`AgentBackend.execute_task` (design §2.2/§2.5).

    ``audit_refs`` carries the op-log op_ids plus backend audit file paths so
    the dual-source reconcile job (W7) can run without re-deriving them."""

    stop_reason: str | None = None
    final_text: str = ""
    usage: dict = field(default_factory=dict)
    events: list[AgentEvent] = field(default_factory=list)
    result_data: dict | None = None
    validation_errors: list = field(default_factory=list)
    error: str | None = None
    audit_refs: dict = field(default_factory=dict)


@runtime_checkable
class AgentSessionHandle(Protocol):
    """Session-level SPI (design §2.3): 5 methods here + the ``open_session``
    factory on :class:`AgentBackend` = 6 session-level methods; with the
    task-level ``execute_task`` the required SPI surface is 7.

    Concurrency contract: ONE prompt in flight per session, loud rejection
    otherwise (F-6/F-7); ``cancel`` is idempotent and takes the shape
    dictated by ``capabilities.cancel_is_notification``."""

    def prompt(self, text: str) -> None: ...

    def events(self) -> Iterator[AgentEvent]: ...

    def answer_permission(
        self, request: PermissionRequest, decision: PermissionDecision
    ) -> None: ...

    def cancel(self, *, reason: str) -> None: ...

    def close(self, *, reason: str) -> None: ...


@runtime_checkable
class AgentBackend(Protocol):
    """Task-level SPI (design §2.2): ``execute_task`` is the ONE required
    method; ``initialize``/``shutdown`` are optional no-ops in implementations.

    ``backend_id`` and ``capabilities`` come from the registry and are
    lint-checked — never guessed at import time."""

    backend_id: str
    capabilities: CapabilityFlags

    def open_session(self, spec: SessionSpec) -> AgentSessionHandle: ...

    def execute_task(
        self, task: TaskSpec, *, budget: BudgetSpec, authz: RunAuthorization
    ) -> TaskResult: ...
