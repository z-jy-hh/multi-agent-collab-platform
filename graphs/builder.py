"""Graph builder: assembles a LangGraph state machine per topology config.

Every node wrapper applies the runtime guards (C18):
1. validates its structured output against the topology YAML's output_schema;
2. emits events onto the EventBus (agent cards, matcher reasons with scores,
   blackboard changes, arbitration — SSE criterion 10);
3. publishes blackboard ops with deterministic op_ids (C6) through the
   agent's memory guard (C10).

Node kinds (generic, in this file):
- plan / match / arbitrate / summarize / review — supervisor pipeline stages
- single_agent / fixed_agent — one agent, one call
Topology modules may add virtual nodes via ``node::<id>`` entries, override
kinds via ``kind::<kind>`` factories, and provide routers via ``route::<rule>``.

LLM nodes call ctx.llm directly; agent nodes call ctx.runtime — CrewAI is
only ever reached through that protocol (C4).
"""

from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import json_repair
import yaml
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from core import ids
from core.crdt import clock_increment
from core.errors import ConfigError, CancelledRun, NoCapabilityMatch
from core.events import EventBus
from core.llm import LLMClient
from core.matcher import (Assignment, GateThresholds, apply_assignments,
                          assign, eligible_agents, score_agent)
from core.memory import MemoryStore
from core.registry import Registry
from core.runtime import AgentRuntime
from core.schemas import SubTask, validate_against_schema
from core.state import GraphState, conflict_keys, resolve_blackboard
from graphs.guardrails import prefix_for as guardrails_prefix


# ---------------------------------------------------------------------------
def _runtime_kind(runtime) -> str:
    """B1: report the substrate that actually executed, not a guess.

    Offline runs use ScriptedRuntime; only real key-backed runs use CrewAIRuntime.
    """
    name = type(runtime).__name__.lower()
    if "scripted" in name:
        return "scripted"
    if "crewai" in name:
        return "crewai"
    return name or "unknown"


def _executor_of(ctx: "RunContext", agent_id: str) -> str:
    """Per-agent substrate (W4-1). Falls back to the old run-level heuristic when
    the runtime does not expose ``executor_for`` (old objects / test doubles)."""
    fn = getattr(ctx.runtime, "executor_for", None)
    if callable(fn):
        try:
            return str(fn(agent_id))
        except Exception:
            pass
    return _runtime_kind(ctx.runtime)


def _output_fields(ctx: "RunContext", result, agent_id: str,
                   *, always: bool = False) -> dict:
    """Evidence fields for ``agent_output`` — only what the runtime really knows.

    ``always=True`` keeps the historical worker key-set (executor +
    backend_session_id) even for old runtimes; summarize/generic only get the
    keys when the runtime actually supplies them (off-state stays byte-equal).
    """
    meta = dict(getattr(result, "meta", {}) or {})
    fields: dict = {}
    if always or "executor" in meta:
        fields["executor"] = str(meta.get("executor") or _executor_of(ctx, agent_id))
    if always or "backend_session_id" in meta:
        fields["backend_session_id"] = meta.get("backend_session_id")
    if "stop_reason" in meta:
        fields["stop_reason"] = meta.get("stop_reason")
    if "session_scope" in meta:
        fields["session_scope"] = meta.get("session_scope")
    if "fallback" in meta:
        fields["fallback"] = bool(meta.get("fallback"))
    resolved = getattr(ctx, "profiles", {}).get(agent_id)
    if resolved is not None:
        fields["profile_selection_id"] = resolved.selection_id
    return fields


@dataclass
class RunContext:
    """Everything a node needs, injected — no globals."""

    run_id: str
    session_id: str
    task: str
    mode: str
    runtime: AgentRuntime
    llm: LLMClient
    registry: Registry
    memory: MemoryStore
    bus: EventBus
    config: dict                                    # topology YAML
    params: dict = field(default_factory=dict)      # workflow.yaml ⊕ defaults
    counters: dict = field(default_factory=dict)    # cooperative bounds (C18)
    worker_model: str | None = None                  # W0-1: crew LLM model (None offline)
    profiles: dict = field(default_factory=dict)     # P1: agent_id -> ResolvedProfile

    def emit(self, type: str, payload: dict | None = None, agent_id: str | None = None):
        return self.bus.emit(type, self.run_id, payload or {}, agent_id)

    def guard(self, agent_id: str):
        return self.memory.guard(self.session_id, agent_id)

    def op_id(self, node: str, key: str, version_index: int) -> str:
        return ids.derive_op_id(self.run_id, node, key, version_index)

    def agent_card(self, agent_id: str) -> dict:
        spec = self.registry.get_agent(agent_id)
        # M1/W0-1 (B1/B2): executor is the substrate that really runs this agent;
        # `model` stays null because the worker's model lives behind the runtime
        # (crewai.LLM), while `platform_model` is the judge/planner LLM — different
        # objects. Never claim one as the other.
        executor = _executor_of(self, agent_id)
        card = {"agent_id": agent_id, "role": spec.role, "domains": spec.domains,
                "strength": dict(spec.strength), "cost": spec.cost,
                "executor": executor,
                # W0-1: the worker's real model when a worker runtime is in use;
                # null under ScriptedRuntime (nothing to claim) AND under the
                # native backend (the model is backend-managed, not observable).
                "model": None if executor == "acp" else self.worker_model,
                "platform_model": getattr(self.llm, "model", None)}
        # W4-1: backend is evidence, additive; never a session id (F4 red line).
        describe = getattr(self.runtime, "describe", None)
        if callable(describe):
            try:
                card.update(dict(describe(agent_id) or {}))
            except Exception:
                pass
        return card

    def prompt_for(self, agent_id: str, **fills: Any) -> str:
        spec = self.registry.get_agent(agent_id)
        path = ids_path_from_ref(spec.prompt_ref)
        template = path.read_text(encoding="utf-8") if path.exists() else f"{spec.role}: {{task}}"
        resolved = self.profiles.get(agent_id)
        if resolved is not None:
            # P1: the profile preset becomes the prompt source; the brief is the
            # binding overlay. Off (no resolved profile) is byte-identical.
            template = str(resolved.preset.behavior.get("prompt") or template)
        text = template
        for key, value in {"task": self.task, **fills}.items():
            text = text.replace("{" + key + "}", str(value))
        if resolved is not None and resolved.brief:
            # O4: full brief on the first call, a one-line-per-hard-rule reminder
            # afterwards (same agent across nodes). Never repeat 8K every turn.
            calls = self.params.setdefault("_profile_calls", {})
            first = not calls.get(agent_id)
            calls[agent_id] = calls.get(agent_id, 0) + 1
            if first:
                brief = resolved.brief
            else:
                from graphs.identity import short_brief
                brief = short_brief(resolved)
            if brief:
                text = brief + "\n\n" + text
        # MEM1 (93fef01e3a1761a1): guardrails prepend, default OFF — off-state
        # prefix is "" so the returned text is byte-identical to pre-MEM1.
        prefix = guardrails_prefix(agent_id, self.params)
        return prefix + text if prefix else text

    def execute(self, agent_id: str, prompt: str, context: dict):
        """P1 seam: all agent execution goes through the profile guard."""
        resolved = self.profiles.get(agent_id)
        if resolved is None:
            return self.runtime.execute(agent_id, prompt, context)
        from graphs.profile_guard import execute_with_checks
        return execute_with_checks(self.runtime, agent_id, prompt, context,
                                   resolved, bus=self.bus, run_id=self.run_id)

    def llm_json(self, prompt: str, system: str = "") -> dict:
        raw = self.llm.complete(prompt, system=system, json_mode=True)
        data = json_repair.loads(raw)
        return data if isinstance(data, dict) else {"reply": str(data)}

    def blackboard_summary(self) -> str:
        ops = self.memory.blackboard_ops(self.session_id)
        return json.dumps(
            {f"{o.agent_id}::{o.key}": str(o.value)[:200] for o in ops},
            ensure_ascii=False, indent=1,
        )

    def publish_entry(self, agent_id: str, node: str, key: str, value: Any,
                      version_index: int = 0) -> dict | None:
        """Publish one blackboard op via the agent's guard; returns the
        serialized MVEntry (with 'key') for the reducer, None when dup."""
        guard = self.guard(agent_id)
        op = guard.publish(key, value, self.op_id(node, key, version_index))
        if op is None:
            return None
        self.emit("blackboard_write",
                  {"key": key, "op_id": op.op_id, "clock": op.clock}, agent_id=agent_id)
        return {"key": key, "value": value, "clock": dict(op.clock),
                "hlc": list(op.hlc), "op_id": op.op_id, "agent_id": agent_id}


def ids_path_from_ref(ref: str):
    from core.paths import PROJECT_ROOT

    return PROJECT_ROOT / ref


# -- node wrapper: runtime guards ----------------------------------------------


def guard_node(ctx: RunContext, node_cfg: dict, fn: Callable[[dict], dict]):
    schema = node_cfg.get("output_schema") or {}

    def wrapped(state: dict) -> dict:
        node_id = node_cfg["id"]
        cancel = ctx.counters.get("cancel_event")  # cooperative cancel (C12/C18)
        if cancel is not None and cancel.is_set():
            from core.errors import CancelledRun

            raise CancelledRun(f"run cancelled before node {node_id}")
        ctx.emit("node_enter", {"node": node_id}, agent_id=node_cfg.get("agent"))
        ctx.counters["iterations"] = ctx.counters.get("iterations", 0) + 1
        try:
            out = fn(state)
        except Exception as exc:
            ctx.emit("node_error",
                     {"node": node_id, "error": f"{type(exc).__name__}: {exc}"},
                     agent_id=node_cfg.get("agent"))
            raise
        problems = validate_against_schema(out, schema)
        if problems:
            ctx.emit("schema_violation",
                     {"node": node_id, "problems": problems},
                     agent_id=node_cfg.get("agent"))
            out = {**out, "schema_problems": problems}
        return out

    return wrapped


# -- generic node kinds ---------------------------------------------------------


def make_plan_node(ctx: RunContext, node_cfg: dict):
    def plan(state: dict) -> dict:
        prompt = ctx.prompt_for(
            node_cfg["agent"], max_subtasks=ctx.params.get("max_subtasks", 6),
            blackboard_summary=ctx.blackboard_summary(),
        )
        data = ctx.llm_json(prompt, system="你是任务拆解主管,只输出 JSON。")
        subtasks = [
            SubTask(
                subtask_id=s.get("subtask_id", f"s{i + 1}"),
                description=str(s.get("description", "")),
                required_capabilities=[str(c) for c in s.get("required_capabilities", [])],
            ).model_dump()
            for i, s in enumerate(data.get("subtasks", []))
        ]
        ctx.emit("plan", {"subtasks": subtasks}, agent_id=node_cfg["agent"])
        return {"subtasks": subtasks}

    return plan


def _nomination_pool(ctx: RunContext, subs: list[SubTask],
                     profiles: list, th) -> tuple[dict, list[str]]:
    """Self-nomination (P1 allocation=self_nominate).

    Each agent is asked ONCE (deterministic order: agent_id) to nominate for
    subtasks it claims capability for. Machine check (V1.0 §3.3-2): every
    cited tag must exist on the agent's OWN card AND strength >= min_strength,
    otherwise that nomination entry is invalid with the reason recorded.

    Returns (per-subtask valid pools, fallback subtask ids). matcher.py is
    untouched: the pool filter happens BEFORE assign(); scores/gates are the
    same functions on a possibly smaller roster (V1.1 B2 "共同候选者").
    """
    valid_pool: dict[str, list] = {st.subtask_id: [] for st in subs}
    subtask_brief = json.dumps(
        [{"subtask_id": st.subtask_id, "description": st.description,
          "required_capabilities": st.required_capabilities} for st in subs],
        ensure_ascii=False)
    for p in sorted(profiles, key=lambda x: x.agent_id):
        prompt = (
            f"你是自荐 agent {p.agent_id}（能力 domains={p.domains}, "
            f"strength={dict(p.strength)}）。待领子任务: {subtask_brief}。"
            "只输出 JSON: {\"nominations\": [{\"subtask_id\": \"...\", \"tags\": [\"...\"]}]}；"
            "只自荐你确有能力的任务并引用卡上的能力标签；无合适任务输出 {\"nominations\": []}。"
        )
        data = ctx.llm_json(prompt, system="你是自荐 agent,只输出 JSON。")
        entries = data.get("nominations", []) if isinstance(data, dict) else []
        checked: list[dict] = []
        for e in entries if isinstance(entries, list) else []:
            sid = str(e.get("subtask_id", "")) if isinstance(e, dict) else ""
            tags = [str(t) for t in e.get("tags", [])] if isinstance(e, dict) else []
            reason = ""
            if sid not in valid_pool:
                reason = f"unknown subtask {sid!r}"
            else:
                bad = [t for t in tags
                       if t not in p.domains
                       or float(p.strength.get(t, 0.0)) < th.min_strength]
                if bad:
                    reason = f"tags not on card or below min_strength: {bad}"
            valid = (sid in valid_pool) and not reason
            checked.append({"subtask_id": sid, "tags": tags,
                            "valid": valid, "reason": reason})
            if valid:
                valid_pool[sid].append(p)
        ctx.emit("nomination", {"agent_id": p.agent_id, "nominations": checked},
                 agent_id=p.agent_id)
    fallback = [sid for sid, pool in valid_pool.items() if not pool]
    return valid_pool, fallback


def _wait_host_answer(ctx: RunContext, subtask_id: str, deadline: float) -> dict | None:
    """P1-HOSTPICK: poll runs/<run_id>/allocation_decision.json for a host
    answer for ONE subtask until ``deadline``. Returns the decision entry or
    None on timeout. Bounded by construction (Q4); cooperative-cancel aware;
    a torn decision file is NOT treated as an answer (the endpoint writes
    atomically, so torn == someone else's problem -> keep polling)."""
    from core.paths import run_dir

    path = run_dir(ctx.run_id) / "allocation_decision.json"
    cancel = ctx.counters.get("cancel_event")
    while time.time() < deadline:
        if cancel is not None and cancel.is_set():
            raise CancelledRun("cancelled while waiting for host allocation")
        if path.exists():
            try:
                items = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                items = None
            if isinstance(items, list):
                for e in items:
                    if isinstance(e, dict) and e.get("subtask_id") == subtask_id:
                        return e
        time.sleep(0.25)
    return None


def _auto_assign_groups(subs: list[SubTask], pools: dict[str, list],
                        th: GateThresholds, llm):
    """assign() with the taken-spreading semantics preserved: group subtasks
    by pool signature, one assign() call per distinct pool."""
    from core.matcher import MatchReport

    groups: dict[tuple, tuple[list[SubTask], list]] = {}
    for st in subs:
        pool = pools[st.subtask_id]
        sig = tuple(sorted(p.agent_id for p in pool))
        g = groups.setdefault(sig, ([], pool))
        g[0].append(st)
    merged = MatchReport()
    for group, pool in groups.values():
        merged.assignments.extend(assign(group, pool, th, llm=llm).assignments)
    order = {st.subtask_id: i for i, st in enumerate(subs)}
    merged.assignments.sort(key=lambda a: order.get(a.subtask_id, 1 << 30))
    return merged


def make_match_node(ctx: RunContext, node_cfg: dict):
    def match(state: dict) -> dict:
        subs = [SubTask(**s) for s in state.get("subtasks", [])]
        profiles = list(ctx.registry.agents().values())
        th = GateThresholds(
            min_strength=float(ctx.params.get("min_strength", 5.0)),
            min_score=float(ctx.params.get("min_score", 0.9)),
            gap_threshold=float(ctx.params.get("gap_threshold", 0.1)),
        )
        allocation = str(ctx.params.get("allocation", "lead_pick"))
        allocation_map = dict(ctx.params.get("allocation_map") or {})
        pins = dict(ctx.params.get("pins") or {})
        host_involved = bool(pins) or bool(allocation_map) or \
            allocation == "self_nominate_host_pick"
        if not host_involved and allocation == "self_nominate":
            ctx.emit("allocation_strategy",
                     {"strategy": "self_nominate",
                      "agents": sorted(p.agent_id for p in profiles)})
            valid_pool, fallback_ids = _nomination_pool(ctx, subs, profiles, th)
            if fallback_ids:
                ctx.emit("allocation_fallback",
                         {"subtask_ids": fallback_ids,
                          "reason": "no_valid_nomination; fell back to full roster"})
            # group subtasks by pool signature; one assign() call per distinct
            # pool keeps the taken-spreading semantics inside each group
            groups: dict[tuple, tuple[list[SubTask], list]] = {}
            for st in subs:
                pool = valid_pool[st.subtask_id] or profiles
                sig = tuple(sorted(p.agent_id for p in pool))
                g = groups.setdefault(sig, ([], pool))
                g[0].append(st)
            from core.matcher import MatchReport
            merged = MatchReport()
            for group, pool in groups.values():
                merged.assignments.extend(
                    assign(group, pool, th, llm=ctx.llm).assignments)
            order = {st.subtask_id: i for i, st in enumerate(subs)}
            merged.assignments.sort(key=lambda a: order.get(a.subtask_id, 1 << 30))
            report = merged
        elif not host_involved:
            try:
                report = assign(subs, profiles, th, llm=ctx.llm)
            except NoCapabilityMatch as exc:
                ctx.emit("no_capability_match",
                         {"missing": exc.missing, "detail": str(exc)})
                return {"status": "no_capability_match",
                        "worker_notes": [f"缺能力,停止派活: {exc}"],
                        "subtasks": [s.model_dump() for s in subs]}
        else:
            # P1-HOSTPICK generalized path: per-subtask strategies, host pick
            # inside the valid pool, host pins. matcher.py untouched — the
            # gates (coverage + min_score) apply to host choices via
            # eligible_agents(), and pins/answers never bypass them.
            report = _host_pick_match(ctx, subs, profiles, th, allocation,
                                      allocation_map, pins)
        filled = apply_assignments(subs, report)
        for a in report.assignments:
            # criterion 10: who got what, and why (with the score). chosen_by
            # is carried on the EVENT payload only (matcher model untouched);
            # in fully-default runs it is omitted so the default trace stays
            # byte-identical (Q1), absence == "auto".
            payload = {"subtask_id": a.subtask_id, "agent_id": a.agent_id,
                       "score": a.score, "reason": a.reason,
                       "runner_up": a.runner_up}
            chosen_map = getattr(report, "chosen_by", None) or {}
            chosen_by = chosen_map.get(a.subtask_id)
            if chosen_by:
                payload["chosen_by"] = chosen_by
            ctx.emit("assignment", payload, agent_id=a.agent_id)
        return {"subtasks": [s.model_dump() for s in filled], "status": "dispatched"}

    return match


_VALID_STRATEGIES = {"lead_pick", "self_nominate", "self_nominate_host_pick"}


def _host_pick_match(ctx: RunContext, subs: list[SubTask], profiles: list,
                     th: GateThresholds, allocation: str,
                     allocation_map: dict, pins: dict):
    """P1-HOSTPICK generalized dispatch (档1+档2).

    Order (deterministic, plan order; B5): pins validate+reserve first, then
    per-subtask strategy; a host choice is only requested when the VALID
    pool has >= 2 gate-passing candidates (Q2); the answer must be inside
    the request's candidate list and NOT in `taken` (Q3/B5, enforced again
    here in addition to the endpoint); timeout => auto + allocation_fallback
    {reason: host_timeout} (Q4); any conflict => allocation_conflict event
    + loud ConfigError, never a silent reassignment (Q5, necessary-condition
    checks only — full feasibility is out of scope, B5).
    """
    strat: dict[str, str] = {}
    for st in subs:
        s = str(allocation_map.get(st.subtask_id, allocation))
        if s not in _VALID_STRATEGIES:
            raise ConfigError(f"unknown allocation strategy {s!r} for "
                              f"{st.subtask_id!r} (allowed: {sorted(_VALID_STRATEGIES)})")
        strat[st.subtask_id] = s
    ctx.emit("allocation_strategy",
             {"strategy": allocation, "allocation_map": dict(allocation_map),
              "pins": dict(pins), "agents": sorted(p.agent_id for p in profiles)})

    if any(s != "lead_pick" for s in strat.values()):
        valid_pool, fallback_ids = _nomination_pool(ctx, subs, profiles, th)
        if fallback_ids:
            ctx.emit("allocation_fallback",
                     {"subtask_ids": fallback_ids,
                      "reason": "no_valid_nomination; fell back to full roster"})
    else:
        valid_pool = {st.subtask_id: [] for st in subs}

    def base_pool(st: SubTask) -> list:
        if strat[st.subtask_id] == "lead_pick":
            return list(profiles)
        return list(valid_pool[st.subtask_id]) or list(profiles)

    # --- pins first: validate against pool + gates, then reserve ----------
    pinned: dict[str, str] = {}                      # sid -> agent_id
    conflicts: list[dict] = []
    by_id = {p.agent_id: p for p in profiles}
    for st in subs:                                   # plan order (B5)
        sid = st.subtask_id
        if sid not in pins:
            continue
        target = str(pins[sid])
        prof = by_id.get(target)
        in_pool = prof is not None and any(p.agent_id == target
                                           for p in base_pool(st))
        if target in pinned.values():
            conflicts.append({"subtask_id": sid, "agent_id": target,
                              "reason": "agent already pinned to another subtask"})
        elif not in_pool:
            conflicts.append({"subtask_id": sid, "agent_id": target,
                              "reason": "pin target not in the valid pool"})
        elif not eligible_agents(st, [prof], th):
            conflicts.append({"subtask_id": sid, "agent_id": target,
                              "reason": "pin target fails coverage/min_score gates"})
        else:
            pinned[sid] = target
    # necessary-condition feasibility: every unpinned subtask must still have
    # >= 1 gate-passing candidate after reservations (NOT a completeness
    # claim — full feasibility is out of scope, B5)
    for st in subs:
        if st.subtask_id in pinned:
            continue
        pool = [p for p in base_pool(st) if p.agent_id not in pinned.values()]
        if not eligible_agents(st, pool, th):
            conflicts.append({"subtask_id": st.subtask_id, "agent_id": None,
                              "reason": "no gate-passing candidate left after pins"})
    if conflicts:
        ctx.emit("allocation_conflict", {"pairs": conflicts})
        raise ConfigError(f"allocation_conflict: {conflicts}")

    # --- auto-assign the pinned ones --------------------------------------
    assignments: list[Assignment] = []
    chosen: dict[str, str] = {}                       # sid -> chosen_by
    for st in subs:
        sid = st.subtask_id
        if sid not in pinned:
            continue
        target = pinned[sid]                  # agent_id string (NOT the profile)
        prof = by_id[target]
        s = score_agent(prof, st.required_capabilities)
        pool = [p for p in base_pool(st) if p.agent_id != target]
        cands = eligible_agents(st, pool, th)
        runner_up = cands[0][0].agent_id if cands else None
        assignments.append(Assignment(
            subtask_id=sid, agent_id=target, score=round(s, 4),
            reason="pinned by host", runner_up=runner_up))
        chosen[sid] = "pin"

    # --- remaining subtasks in plan order ---------------------------------
    auto_subs: list[SubTask] = []
    auto_pools: dict[str, list] = {}
    for st in subs:
        sid = st.subtask_id
        if sid in pinned:
            continue
        pool = [p for p in base_pool(st) if p.agent_id not in pinned.values()]
        s = strat[sid]
        if s == "self_nominate_host_pick":
            cands = eligible_agents(st, pool, th)
            if len(cands) >= 2:                        # Q2: only >=2 pools ask
                now = time.time()
                timeout_s = float(ctx.params.get("allocation_host_timeout_s",
                                                 600.0))
                payload = {"subtask_id": sid,
                           "candidates": [{"agent_id": p.agent_id,
                                           "score": round(float(score), 4)}
                                          for p, score in cands],
                           "runner_up": cands[1][0].agent_id,
                           "timeout_s": timeout_s,
                           "deadline_ts": now + timeout_s,
                           "taken": sorted(pinned.values())}
                ctx.emit("allocation_choice_requested", payload)
                answer = _wait_host_answer(ctx, sid, now + timeout_s)
                if answer is None:
                    ctx.emit("allocation_fallback",
                             {"subtask_ids": [sid], "reason": "host_timeout"})
                else:
                    agent_id = str(answer.get("agent_id", ""))
                    ok = any(p.agent_id == agent_id for p, _s in cands) and \
                        agent_id not in payload["taken"]
                    if not ok:
                        pairs = [{"subtask_id": sid, "agent_id": agent_id,
                                  "reason": "host answer out of pool or taken"}]
                        ctx.emit("allocation_conflict", {"pairs": pairs})
                        raise ConfigError(f"allocation_conflict: {pairs}")
                    score = next(float(sc) for p, sc in cands
                                 if p.agent_id == agent_id)
                    runner_up = next((p.agent_id for p, _s in cands
                                      if p.agent_id != agent_id), None)
                    assignments.append(Assignment(
                        subtask_id=sid, agent_id=agent_id, score=score,
                        reason=f"chosen by host ({answer.get('author', 'host')})",
                        runner_up=runner_up))
                    chosen[sid] = "host"
                    ctx.emit("allocation_answer",
                             {"subtask_id": sid, "agent_id": agent_id,
                              "chosen_by": "host",
                              "author": answer.get("author", "host"),
                              "ts": answer.get("ts")})
                    continue
            # else: pool too small or no eligible candidate -> auto below
        auto_subs.append(st)
        auto_pools[sid] = pool

    if auto_subs:
        merged = _auto_assign_groups(auto_subs, auto_pools, th, ctx.llm)
        assignments.extend(merged.assignments)
    for a in assignments:
        chosen.setdefault(a.subtask_id, "auto")
    order = {st.subtask_id: i for i, st in enumerate(subs)}
    assignments.sort(key=lambda a: order.get(a.subtask_id, 1 << 30))

    from core.matcher import MatchReport

    report = MatchReport()
    report.assignments = assignments
    # chosen_by rides on the report for the event emitter (attribute added
    # dynamically; matcher.Assignment is a plain dataclass, matcher.py is
    # untouched)
    report.chosen_by = dict(chosen)  # type: ignore[attr-defined]
    return report


def make_worker_node(ctx: RunContext, node_cfg: dict):
    def worker(payload: dict) -> dict:
        sub = payload["subtask"]
        agent_id = sub["assigned_agent"]
        ctx.emit("agent_card", ctx.agent_card(agent_id), agent_id=agent_id)
        prompt = ctx.prompt_for(
            agent_id, role_prompt="", task=sub["description"],
            context=ctx.blackboard_summary(),
        )
        result = ctx.execute(agent_id, prompt,
                             {"role_prompt": "",
                              "subtask_id": sub["subtask_id"]})
        entry = ctx.publish_entry(agent_id, "worker",
                                  f"result::{sub['subtask_id']}", result.output)
        ctx.emit("agent_output",
                 {"subtask_id": sub["subtask_id"], "agent_id": agent_id,
                  "output_chars": len(result.output),
                  # M1/W0-1: keeps "native vs api" answerable per event.
                  **_output_fields(ctx, result, agent_id, always=True)},
                 agent_id=agent_id)
        return {"worker_notes": [f"[{sub['subtask_id']}|{agent_id}] {result.output}"],
                "blackboard": [entry] if entry else []}

    return worker


def make_execute_router(ctx: RunContext):
    def route_execute(state: dict):
        if state.get("status") == "no_capability_match":
            return []
        return [Send("worker", {"subtask": s})
                for s in state.get("subtasks", []) if s.get("assigned_agent")]

    return route_execute


def make_arbitrate_node(ctx: RunContext, node_cfg: dict):
    def arbitrate(state: dict) -> dict:
        from core.crdt import MVEntry

        bb = state.get("blackboard") or {}
        conflicts = conflict_keys(bb)
        resolutions: dict[str, Any] = {}
        for key in conflicts:
            entries = bb.get(key, [])
            answer = ctx.llm_json(
                "同一分片有两个并发写版本:\n" + json.dumps(entries, ensure_ascii=False)
                + '\n判断意图是否冲突。输出 JSON:{"action":"rebase|pick",'
                  '"value":"最终值","why":"理由"}',
                system="你是冲突仲裁者。意图不冲突则 rebase,真冲突则 pick。",
            )
            if answer.get("action") == "rebase" and "value" in answer:
                resolutions[key] = {"action": "rebase", "value": answer["value"],
                                    "why": str(answer.get("why", ""))}
            else:  # deterministic fallback: LWW under stable HLC order (C11)
                mventries = [MVEntry.from_dict(d) for d in entries]
                winner = max(mventries, key=lambda e: e.hlc)
                resolutions[key] = {"action": "pick", "value": winner.value,
                                    "why": f"{answer.get('why', '')} (LWW fallback → {winner.op_id})"}
            ctx.emit("arbitration", {"key": key, **resolutions[key]})
        if not conflicts:
            ctx.emit("arbitration", {"key": None, "action": "none",
                                     "why": "no concurrent writes this round"})
        return {"conflicts": conflicts, "arbitration": resolutions}

    return arbitrate


def make_summarize_node(ctx: RunContext, node_cfg: dict):
    def summarize(state: dict) -> dict:
        agent_id = node_cfg.get("agent", "writer")
        resolved = resolve_blackboard(state.get("blackboard") or {})
        for key, res in (state.get("arbitration") or {}).items():
            resolved[key] = res.get("value")
        prompt = ctx.prompt_for(
            agent_id,
            context=json.dumps(resolved, ensure_ascii=False, indent=2),
            review_comments=str(state.get("review_comments") or ""),
        )
        result = ctx.execute(agent_id, prompt, {})
        entry = ctx.publish_entry(agent_id, "summarize", "final_report", result.output,
                                  version_index=int(state.get("review_round", 0)))
        ctx.emit("agent_output",
                 {"agent_id": agent_id, "stage": "summarize",
                  "output_chars": len(result.output),
                  **_output_fields(ctx, result, agent_id)}, agent_id=agent_id)
        return {"final_report": result.output, "status": "summarized",
                "blackboard": [entry] if entry else []}

    return summarize


def make_review_node(ctx: RunContext, node_cfg: dict):
    def review(state: dict) -> dict:
        prompt = ctx.prompt_for(node_cfg["agent"], task=ctx.task,
                                report=state.get("final_report", ""))
        data = ctx.llm_json(prompt, system="你是审稿人,只输出 JSON。")
        verdict = str(data.get("verdict", "REWORK"))
        rounds = int(state.get("review_round", 0)) + 1
        comments = str(data.get("comments", ""))
        # P2-2 (C′): optional adversarial convergence — ONLY when the switch
        # is on AND verdict == REWORK AND the round gate is not exhausted
        # (double-bounded). Off => zero extra calls, zero events, zero
        # payload change (R5 byte-identity). Emits arbitration with
        # source="enhanced" (R7: only-add field) and merges the critique
        # into the next round's review_comments.
        from graphs.review_adversarial import adversarial_pass
        verdict, comments = adversarial_pass(ctx, state, verdict,
                                             comments, rounds)
        ctx.emit("review", {"round": rounds, "verdict": verdict,
                            "comments": comments},
                 agent_id=node_cfg["agent"])
        return {"review_round": rounds, "review_verdict": verdict,
                "review_comments": comments}

    return review


def make_generic_agent_node(ctx: RunContext, node_cfg: dict):
    agent_id = node_cfg["agent"]

    def agent_node(state: dict) -> dict:
        ctx.emit("agent_card", ctx.agent_card(agent_id), agent_id=agent_id)
        fills: dict[str, Any] = {}
        if node_cfg["kind"] == "debater":
            fills["opponent_argument"] = str(state.get("review_comments") or "")
        prompt = ctx.prompt_for(agent_id, role_prompt="", task=ctx.task,
                                context=ctx.blackboard_summary(), **fills)
        result = ctx.execute(agent_id, prompt, {})
        entry = ctx.publish_entry(agent_id, node_cfg["id"],
                                  f"out::{node_cfg['id']}", result.output,
                                  version_index=int(state.get("debate_round", 0)))
        ctx.emit("agent_output", {"agent_id": agent_id, "node": node_cfg["id"],
                                  "output_chars": len(result.output),
                                  **_output_fields(ctx, result, agent_id)},
                 agent_id=agent_id)
        out: dict[str, Any] = {
            "worker_notes": [f"[{node_cfg['id']}|{agent_id}] {result.output}"],
            "final_report": result.output,
        }
        if entry:
            out["blackboard"] = [entry]
        if node_cfg["kind"] == "debater":
            out["review_comments"] = result.output  # the next debater rebuts this
        return out

    return agent_node


# -- assembly --------------------------------------------------------------------


def _instantiate(ctx: RunContext, node_cfg: dict, special: dict) -> Callable:
    kind = node_cfg["kind"]
    override = special.get(f"kind::{kind}")
    if override is not None:
        return override(node_cfg)
    makers = {
        "plan": make_plan_node,
        "match": make_match_node,
        "arbitrate": make_arbitrate_node,
        "summarize": make_summarize_node,
        "review": make_review_node,
    }
    if kind in makers:
        return makers[kind](ctx, node_cfg)
    if kind in ("single_agent", "fixed_agent", "debater"):
        return make_generic_agent_node(ctx, node_cfg)
    raise KeyError(f"unknown node kind: {kind}")


def build_graph(ctx: RunContext):
    module = importlib.import_module(f"graphs.topologies.{ctx.mode}")
    special = module.node_implementations(ctx)

    graph = StateGraph(GraphState)  # Annotated reducers handle parallel fan-in
    node_ids = [n["id"] for n in ctx.config["nodes"]]

    for node_cfg in ctx.config["nodes"]:
        graph.add_node(node_cfg["id"],
                       guard_node(ctx, node_cfg, _instantiate(ctx, node_cfg, special)))
    for key, value in special.items():
        if key.startswith("node::"):
            node_id = key[len("node::"):]
            cfg, fn = value
            graph.add_node(node_id, guard_node(ctx, cfg, fn))
            node_ids.append(node_id)

    # group conditional edges by source (a router decides among its targets)
    conditional_targets: dict[str, list] = {}
    for edge in ctx.config["edges"]:
        if edge.get("conditional"):
            conditional_targets.setdefault(edge["from"], []).append(edge["to"])

    for edge in ctx.config["edges"]:
        src, dst = edge["from"], edge["to"]
        if edge.get("conditional"):
            continue  # wired below, grouped
        if src == "START":
            graph.add_edge(START, dst)
        elif dst == "END":
            graph.add_edge(src, END)
        else:
            graph.add_edge(src, dst)

    for src, targets in conditional_targets.items():
        rule = next(e["rule"] for e in ctx.config["edges"]
                    if e["from"] == src and e.get("conditional"))
        router = special.get(f"route::{rule}")
        if router is None:
            raise KeyError(f"topology {ctx.mode} missing route::{rule}")
        # YAML writes END as a string; LangGraph needs the END sentinel
        normalized = [END if t == "END" else t for t in dict.fromkeys(targets)]
        graph.add_conditional_edges(src, router, normalized)

    return graph.compile()
