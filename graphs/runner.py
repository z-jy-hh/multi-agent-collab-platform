"""Run orchestrator: wire a topology, execute it, persist the run trace.

Default wiring (no key → fully offline, task book §五):
- llm = llm_from_env()          → FakeLLM when OPENAI_API_KEY is unset
- runtime = ScriptedRuntime(...) with canned outputs when offline,
  CrewAIRuntime(crews.factory.make_crew) when a key is present
- memory = MemoryStore(SQLiteOpStore(runs/collab.sqlite3)) when persistent
  (C13), in-memory otherwise (tests)
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import yaml

from core import ids
from core.errors import StorageError
from core.events import EventBus, EventJSONLWriter
from core.llm import llm_from_env
from core.memory import MemoryStore
from core.paths import configs_dir, run_dir, storage_path
from core.registry import load_registry
from core.runtime import CrewAIRuntime, ScriptedRuntime
from core.storage import InMemoryOpStore, SQLiteOpStore

DEFAULT_SCRIPTS: dict[str, list[str]] = {
    "supervisor": ['{"subtasks": [{"subtask_id": "s1", "description": "调研多Agent协同的工程现状", "required_capabilities": ["research"]}]}'],
    "researcher": ["要点1:多Agent把复杂任务拆给专长各异的执行者并行完成;要点2:主流拓扑有主管/流水线/辩论;结论:按任务可分解性选拓扑。"],
    "analyst": ["对比:单Agent长上下文 vs 多Agent并行——上下文隔离、职责单一、可并行、易定位;判断:多Agent适合可分解任务。"],
    "writer": ["# 多Agent协同调研\n现状:主管模式为主流。\n机制:能力匹配派活+黑板共享。\n结论:按可分解性选型。"],
    "reviewer": ['{"verdict": "PASS", "comments": "ok"}'],
    "debater_pro": ["正方:多Agent并行带来的吞吐与职责隔离收益大于协调开销。"],
    "debater_con": ["反方:协调与仲裁成本在任务不可分解时会吃掉全部收益。"],
    "judge": ['{"verdict": "CONCLUDED", "conclusion": "多Agent适合可分解任务;不可分解任务应保留单Agent路径。"}'],
}


def default_llm_script(prompt: str, json_mode: bool) -> str:
    """Offline default LLM script for LLM nodes (plan/review/judge/arbitrate).
    Business-aware ON PURPOSE: it lives in graphs/, not core (C4).
    The reviewer sends the FIRST draft back once (REWORK) so the default demo
    exercises the rework loop and gives the evolver a real weakness to find."""
    import json as _json

    state = getattr(default_llm_script, "_review_state", None)
    if state is None:
        state = default_llm_script._review_state = {"n": 0}

    if "拆解" in prompt:
        return _json.dumps({"subtasks": [
            {"subtask_id": "s1", "description": "调研多Agent协同的工程现状与典型拓扑",
             "required_capabilities": ["research"]},
            {"subtask_id": "s2", "description": "对比单Agent与多Agent的收益与成本",
             "required_capabilities": ["analysis"]},
        ]}, ensure_ascii=False)
    if "审稿人" in prompt:
        state["n"] += 1
        if state["n"] == 1:
            return _json.dumps({"verdict": "REWORK", "comments": "第一稿结构不完整,请按 现状/机制/结论 三段重写"},
                               ensure_ascii=False)
        return _json.dumps({"verdict": "PASS", "comments": "ok"}, ensure_ascii=False)
    if "辩论裁判" in prompt:
        return _json.dumps({"verdict": "CONCLUDED",
                            "conclusion": "多Agent协同适合可分解任务;不可分解任务保留单Agent路径。"},
                           ensure_ascii=False)
    if "并发写" in prompt:
        return _json.dumps({"action": "pick", "value": "仲裁后的统一结论",
                            "why": "两版本意图不冲突,取并集后统一表述"}, ensure_ascii=False)
    return "ok"


def _load_workflow_params() -> dict:
    path = configs_dir() / "workflow.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def run_topology(
    mode: str,
    task: str,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    runtime=None,
    llm=None,
    registry=None,
    memory: MemoryStore | None = None,
    bus: EventBus | None = None,
    params: dict | None = None,
    persistent: bool = False,
    cancel_event=None,
) -> dict:
    from graphs.builder import RunContext, build_graph

    run_id = run_id or ids.new_run_id()
    session_id = session_id or f"session-{run_id[:8]}"

    registry = registry or load_registry(configs_dir())
    config = registry.get_topology(mode)
    merged_params: dict = {}
    merged_params.update(_load_workflow_params())
    merged_params.update(config.get("defaults", {}))
    merged_params.update(params or {})

    llm = llm if llm is not None else llm_from_env(script=default_llm_script)
    worker_model: str | None = None
    inner = None
    if runtime is None:
        from core.llm import FakeLLM

        if isinstance(llm, FakeLLM):
            inner = ScriptedRuntime(DEFAULT_SCRIPTS)
        else:
            from crews.factory import make_crew, worker_model_name

            inner = CrewAIRuntime(make_crew)
            # W0-1: the model the crew worker really uses (null offline — nothing
            # to claim; the platform judge LLM is a different object).
            worker_model = worker_model_name()
        runtime = inner
    if memory is None:
        store = SQLiteOpStore(storage_path()) if persistent else InMemoryOpStore()
        memory = MemoryStore(store)
    bus = bus if bus is not None else EventBus()

    # trace.jsonl per run (C13) — also the SSE replay source of truth
    trace_path = Path(run_dir(run_id)) / "trace.jsonl"
    trace_writer = EventJSONLWriter(trace_path)
    bus.subscribe(trace_writer)
    events: list = []
    bus.subscribe(events.append)
    native_runtime = None
    try:

        # W4-1: role-level native execution. Constructed AFTER the bus/trace
        # writer on purpose (preflight failures must be visible in the trace),
        # and only when the caller did not inject its own runtime.
        native_mode = (os.environ.get("NATIVE_RUNTIME") or "off").strip().lower()
        if native_mode == "dsh" and runtime is inner:
            from agent_bridge.errors import SpiError as _SpiError
            from agent_bridge.runtime import NativeConfigError, runtime_from_env

            native_runtime = runtime_from_env(
                run_id, session_id, inner=inner, bus=bus, cancel_event=cancel_event)
            runtime = native_runtime
            try:
                native_runtime.preflight()
            except NativeConfigError as exc:
                bus.emit("native_role_error", run_id, {
                    "agent_id": None, "backend": native_runtime.backend_id,
                    "kind": "config", "code": exc.code, "error": str(exc)})
                raise
            except Exception as exc:
                err = (exc if isinstance(exc, _SpiError)
                       else _SpiError(str(exc), kind="environment", backend="dsh",
                                      code="native.preflight_failed"))
                if native_runtime.fallback_mode == "crewai" and inner is not None:
                    native_runtime.degrade(reason="preflight", code=err.code,
                                           detail=str(exc))
                else:
                    bus.emit("native_role_error", run_id, {
                        "agent_id": None, "backend": native_runtime.backend_id,
                        "kind": err.kind, "code": err.code, "error": str(exc)})
                    raise

        # R17-P4FIX-3 (dsh): heterogeneous LEAF dispatch. Earlier versions only
        # resolved bindings and emitted an event, and swallowed registry errors
        # with a bare except. Now every bound+enabled backend actually receives the
        # task through the SPI (execute_task) and reports an outcome event, so an
        # offline run leaves auditable evidence of a real mixed execution (platform
        # agents + hetero leaf). The existing topology still runs after this
        # pre-pass; nothing about the CrewAI roster changes.
        hetero_results: dict = {}
        hetero_bindings: dict[str, str] = {}
        from agent_bridge.dispatch import dispatch_hetero_enabled
        if dispatch_hetero_enabled():
            from agent_bridge.hetero_dispatch import dispatch_bound_leaves
            from agent_bridge.registry import load_registry as _load_spi_registry
            spi_data: dict = {}
            try:
                spi_data = _load_spi_registry() or {}
            except Exception as exc:                      # loud, never silent
                bus.emit("hetero_dispatch_error", run_id,
                         {"stage": "registry", "error": f"{type(exc).__name__}: {exc}"})
            if spi_data:
                # W4-1: native-owned agents are executed by the graph, never by
                # the blanket pre-pass (exactly-once). The set is frozen by
                # preflight and stays frozen even after a degrade.
                skip_native = set(native_runtime.skip_in_prepass) if native_runtime else set()
                if skip_native:
                    for agent_id in sorted(skip_native):
                        bus.emit("hetero_dispatch_skipped", run_id,
                                 {"agent_id": agent_id, "backend_id": "dsh",
                                  "reason": "native-role-runtime"})
                    spi_data = copy.deepcopy(spi_data)
                    for agent_id in skip_native:
                        (spi_data.get("bindings") or {}).pop(agent_id, None)
                        ((spi_data.get("routing") or {}).get("map") or {}).pop(agent_id, None)
                hetero_bindings = {
                    agent_id: target.rstrip(":")
                    for agent_id, target in (spi_data.get("bindings") or {}).items()
                    if target.rstrip(":") in (spi_data.get("backends") or {})
                }
                # F14: routing.map is the current binding truth; omitting it made
                # the trace say "resolved: {}" while researcher->dsh dispatched.
                for agent_id, backend_id in ((spi_data.get("routing") or {}).get("map") or {}).items():
                    target = str(backend_id).rstrip(":")
                    if target in (spi_data.get("backends") or {}):
                        hetero_bindings[agent_id] = target
                bus.emit("hetero_dispatch_resolved", run_id,
                         {"bindings": hetero_bindings})
                hetero_results = dispatch_bound_leaves(
                    run_id=run_id, session_id=session_id, task_text=task,
                    bus=bus, registry_data=spi_data)

        # P2: default is ON, but a run only binds profiles when params.profiles
        # is present; `PROFILE_IDENTITY=0` is the rollback switch.
        resolved_profiles: dict = {}
        if (os.environ.get("PROFILE_IDENTITY") or "1").strip() == "1":
            plan = merged_params.get("profiles") or {}
            if plan:
                from graphs import identity as _identity
                from graphs import profiles as _pc

                presets = _pc.load_presets()
                rules_list, _ = _pc.parse_user_profile()
                rules_by_id = {r.id: r for r in rules_list}
                checks = _pc.load_rule_checks()
                installed = {s["id"] for s in _pc.skills_catalog()
                             if s.get("installed")}
                slots = {agent_id: agent_id for agent_id in plan}
                resolved_profiles = _identity.resolve(
                    plan, slots=slots, presets=presets, rules=rules_by_id,
                    checks=checks, available_skills=installed,
                    allowed_tools=_pc.PLATFORM_TOOL_ALLOWLIST)
                bus.emit("profile_bound", run_id, {
                    "selection_id": _identity.run_selection_id(resolved_profiles),
                    "agents": {aid: r.as_dict()
                               for aid, r in resolved_profiles.items()},
                    "identity": "profiles",
                })

        ctx = RunContext(
            run_id=run_id, session_id=session_id, task=task, mode=mode,
            runtime=runtime, llm=llm, registry=registry, memory=memory,
            bus=bus, config=config, params=merged_params,
            counters={"cancel_event": cancel_event} if cancel_event is not None else {},
            worker_model=worker_model, profiles=resolved_profiles,
        )
        graph = build_graph(ctx)
        initial = {
            "run_id": run_id, "session_id": session_id, "task": task, "mode": mode,
            "status": "running", "subtasks": [], "blackboard": {}, "worker_notes": [],
            "review_round": 0, "debate_round": 0,
        }
        try:
            final = graph.invoke(
                initial, config={"recursion_limit": int(merged_params.get("recursion_limit", 48))},
            )
        except Exception as exc:
            from core.errors import CancelledRun

            if isinstance(exc, CancelledRun):
                bus.emit("run_cancelled", run_id, {"reason": str(exc)})
                final = {"status": "cancelled", "run_id": run_id,
                         "session_id": session_id, "task": task, "mode": mode}
            else:
                # V2-M1: the error path exports too. V2.1 (must-fix 2): exc is
                # passed in so the snapshot holds error{type, detail} and the
                # digest gets a synthesized termination item. export_snapshot
                # never raises, so the original exception propagates unchanged.
                if native_runtime is not None:
                    # W4-1: the session close event must land BEFORE the error
                    # snapshot, otherwise the exported digest misses it.
                    native_runtime.close(reason="run failed")
                from core.archive_export import export_snapshot
                export_snapshot(run_id=run_id, session_id=session_id, mode=mode,
                                task=task, events=events, final=None, health=None,
                                bus=bus, status="error", config=None, exc=exc)
                raise
        final = dict(final)
        if final.get("final_report") and final.get("status") in (None, "running", "summarized"):
            final["status"] = "done"  # clean completion; failures keep their own status
        final["run_id"] = run_id
        final["session_id"] = session_id

        # W4-1: close the native session BEFORE health/run_finished so the
        # dsh_session(close) event stays before the last trace line (T5).
        if native_runtime is not None:
            native_runtime.close(reason="run finished")

        # post-run inspection → health.json (D8, G4)
        from inspector.report import write_health
        from inspector.runner import build_check_context, run_checks

        check_ctx = build_check_context(run_id, session_id, mode, events, final,
                                        memory, merged_params, llm=llm, registry=registry)
        health = run_checks(check_ctx)
        write_health(health)
        bus.emit("health", run_id, {"score": health.score, "summary": health.summary})

        # run record (C13: runs survive restarts via the op store + state file)
        # N2: the raw task/user text flows into the final dict -- the persisted
        # state.json copy goes through the same pre-persistence redaction as the
        # other artifacts (the in-memory `final` is untouched for the caller).
        from core.redact import record_run_redaction, redact_strings_deep, redaction_enabled
        state_path = Path(run_dir(run_id)) / "state.json"
        state_payload = final
        if redaction_enabled():
            state_payload, counts = redact_strings_deep(final)
            if counts:
                record_run_redaction(Path(run_dir(run_id)), counts)
        state_path.write_text(json.dumps(state_payload, ensure_ascii=False, indent=2, default=str),
                              encoding="utf-8")
        if persistent:
            _record_run(run_id, mode, session_id, task, final["status"])
        finished_payload = {"status": final.get("status", "done")}
        if native_runtime is not None:
            # additively honest: only a native run reports the degraded flag
            finished_payload["degraded"] = native_runtime.degraded
        bus.emit("run_finished", run_id, finished_payload)

        # V2-M1 (archive-export H1): done AND cancelled runs share this tail, so
        # ONE call covers both statuses. It runs AFTER run_finished so the digest
        # can include that event. export_snapshot never raises (V2 section 3.1).
        from core.archive_export import export_snapshot
        export_snapshot(run_id=run_id, session_id=session_id, mode=mode, task=task,
                        events=events, final=final, health=health, bus=bus,
                        status=final.get("status", "done"), config=config)
        return final
    finally:
        # W4-1: idempotent close must run BEFORE the writer is unsubscribed, so
        # a close-timeout/failed event still lands in the trace; the explicit
        # close above normally makes this a no-op.
        if native_runtime is not None:
            try:
                native_runtime.close(reason="run finished")
            except Exception:
                pass
        # P3 (T2/B-2): unsubscribe FIRST, then close. A late emit on a
        # caller-supplied bus after run_topology returns must never hit a
        # closed file handle (ValueError), and the handle must be released
        # on the normal path, the cancel path, and the exception path.
        bus.unsubscribe(trace_writer)
        trace_writer.close()


def _record_run(run_id: str, mode: str, session_id: str, task: str, status: str) -> None:
    from core.storage import SQLiteOpStore  # noqa: F401  (WAL store already owns the DB file)

    import sqlite3

    from core.paths import run_dir, storage_path
    from core.redact import (RULES_VERSION, record_run_redaction, redact,
                             redaction_enabled)

    conn = sqlite3.connect(str(storage_path()))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        # R15-M5: measure the journal mode into its OWN variable — reusing the
        # `mode` parameter here is what wrote "wal" into runs.mode for every
        # API-created run. The F1 measurement itself stays (K2 测量而非声称).
        journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if journal != "wal":
            conn.close()
            raise StorageError(f"journal_mode switch failed on {storage_path()}: "
                               f"expected 'wal', measured '{journal}'")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, mode TEXT,"
            " session_id TEXT, task TEXT, status TEXT, created_at REAL DEFAULT"
            " (strftime('%s','now')))"
        )
        # N2-FIX1 (review F1): 只增 schema evolution -- rows written under a
        # redaction rules version are MARKED; pre-existing rows keep NULL
        cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
        if "redaction_rules" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN redaction_rules TEXT")
        # N2-FIX1 (review F1): the task lands in the registry verbatim unless
        # masked here -- mask before persistence (same rules; n2-v1 -> n2-v2)
        stored_task = task
        db_counts: dict = {}
        if redaction_enabled():
            stored_task, db_counts = redact(task)
            if db_counts:
                record_run_redaction(Path(run_dir(run_id)), db_counts)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY,"
            " created_at REAL DEFAULT (strftime('%s','now')))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, mode, session_id, task, status,"
            " redaction_rules) VALUES (?,?,?,?,?,?)",
            (run_id, mode, session_id, stored_task, status,
             RULES_VERSION if redaction_enabled() else None),
        )
        conn.execute("INSERT OR IGNORE INTO sessions (session_id) VALUES (?)", (session_id,))
        conn.commit()
    finally:
        conn.close()
