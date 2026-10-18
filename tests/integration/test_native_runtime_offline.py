"""W4-1 offline acceptance: ACPRuntime against the fake ACP server.

Covers C1/C1b (evidence + reuse), C3 (fallback), C5/C10a-e (serialisation,
latches, queue, cancel, close bound), C6 (off-state baseline), C11 (permission
attribution) and the C2b mixed-run shape (native researcher + inner writer).

No real model, no real dsh binary: the fake server speaks the measured ACP
shapes over stdio.
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from core.errors import CancelledRun
from core.events import EventBus
from core.llm import FakeLLM
from core.schemas import AgentResult
from core.paths import run_dir
from core.storage import SQLiteOpStore

from agent_bridge.errors import SpiError
from agent_bridge.runtime import ACPRuntime, NativeConfigError
from agent_bridge.spi import PermissionDecision

from graphs.runner import default_llm_script, run_topology

REPO = Path(__file__).resolve().parents[2]
FAKE = REPO / "tests" / "fixtures" / "fake_acp_server.py"
BASELINE = REPO / "reports" / "w4_off_baseline.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class SpyScriptedRuntime:
    """Inner runtime double: records calls, prints a sentinel per agent."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, agent_id: str, prompt: str, context: dict) -> AgentResult:
        self.calls.append(agent_id)
        return AgentResult(agent_id=agent_id, output=f"spy:{agent_id}",
                           meta={"runtime": "scripted", "prompt_chars": len(prompt)})


def _native_env(monkeypatch, tmp_path, scenario: str, *, fallback: str = "off",
                extra=()) -> Path:
    receipt = tmp_path / "receipt.jsonl"
    args = [scenario, "--random-session", "--receipt", str(receipt), *extra]
    overrides = {
        "node_bin": sys.executable,
        "dsh_bin_js": str(FAKE),
        "client_extra_args": args,
        "dispatch_root": str(tmp_path / "dispatch"),
    }
    monkeypatch.setenv("AGENT_REGISTRY", "1")
    monkeypatch.setenv("NATIVE_RUNTIME", "dsh")
    monkeypatch.setenv("NATIVE_AGENTS", "researcher")
    monkeypatch.setenv("NATIVE_FALLBACK", fallback)
    monkeypatch.setenv("DISPATCH_HETERO", "0")
    monkeypatch.setenv("DSH_EXPECTED_VERSION", "0.0.1")
    monkeypatch.setenv("DISPATCH_BACKEND_OVERRIDES", json.dumps(overrides))
    return receipt


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _count(rows: list[dict], method: str) -> int:
    return sum(1 for r in rows if r["event"] == "recv" and r["method"] == method)


def _new_session_id(rows: list[dict]) -> str | None:
    for r in rows:
        if r["event"] == "send" and r["method"] == "session/new.reply":
            return (r.get("result") or {}).get("sessionId")
    return None


def _events(bus: EventBus) -> list[dict]:
    return [e.model_dump() for e in bus.replay(0)]


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


@pytest.fixture
def runtime_factory(monkeypatch, tmp_path):
    made: list[ACPRuntime] = []

    def _make(*, inner=None, fallback: str = "off", queue_max: int = 8,
              max_turns: int = 8, wall_s: float = 120.0, cancel_event=None,
              bus=None, agents=frozenset({"researcher"}), preflight: bool = True):
        bus = bus or EventBus()
        rt = ACPRuntime(
            run_id="w4-run", platform_session_id="w4-sess", agents=agents,
            inner=inner, fallback=fallback, bus=bus,
            store=SQLiteOpStore(tmp_path / "ops.sqlite3"),
            queue_max=queue_max, max_turns=max_turns, wall_s=wall_s,
            cancel_event=cancel_event)
        if preflight:
            rt.preflight()
        made.append(rt)
        return rt, bus

    yield _make
    for rt in made:
        try:
            rt.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# C1 / C1b — evidence + session reuse
# ---------------------------------------------------------------------------
def test_minimal_native_turn_records_real_session(monkeypatch, tmp_path, runtime_factory):
    receipt = _native_env(monkeypatch, tmp_path, "text_then_end")
    rt, bus = runtime_factory()
    result = rt.execute("researcher", "hello", {})

    rows = _rows(receipt)
    sid = _new_session_id(rows)
    assert sid and result.meta["backend_session_id"] == sid
    assert result.meta["executor"] == "acp"
    assert result.meta["backend"] == "dsh"
    assert result.meta["stop_reason"] == "end_turn"
    assert result.output == "ok:5"
    assert _count(rows, "session/new") == 1
    assert _count(rows, "session/prompt") == 1
    # every prompt frame carries the session id issued by session/new
    prompt_rows = [r for r in rows if r["event"] == "recv" and r["method"] == "session/prompt"]
    assert all(r["params"]["sessionId"] == sid for r in prompt_rows)

    events = _events(bus)
    assert any(e["type"] == "dsh_session" and e["payload"].get("action") == "new"
               for e in events)
    turns = [e for e in events if e["type"] == "dsh_turn_event"]
    assert turns and all(e["payload"].get("agent_id") == "researcher" for e in turns)
    assert [t["payload"].get("turn_seq") for t in turns] == sorted(
        t["payload"].get("turn_seq") for t in turns)


def test_second_turn_reuses_the_session(monkeypatch, tmp_path, runtime_factory):
    receipt = _native_env(monkeypatch, tmp_path, "text_then_end")
    rt, _ = runtime_factory()
    first = rt.execute("researcher", "a", {})
    second = rt.execute("researcher", "bb", {})

    rows = _rows(receipt)
    assert _count(rows, "session/new") == 1
    assert _count(rows, "session/prompt") == 2
    assert first.meta["backend_session_id"] == second.meta["backend_session_id"]
    assert first.output == "ok:1" and second.output == "ok:2"


# ---------------------------------------------------------------------------
# C11 — permission attribution
# ---------------------------------------------------------------------------
def test_permission_request_carries_the_agent_id(monkeypatch, tmp_path, runtime_factory):
    _native_env(monkeypatch, tmp_path, "permission_roundtrip")
    rt, bus = runtime_factory()
    box: dict = {}

    def _run():
        try:
            box["result"] = rt.execute("researcher", "do it", {})
        except Exception as exc:              # pragma: no cover - surfaced below
            box["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    request_event = None
    while time.monotonic() < deadline and request_event is None:
        for e in _events(bus):
            if e["type"] == "spi_permission_request":
                request_event = e
                break
        time.sleep(0.05)

    assert request_event is not None, "no permission request reached the bus"
    assert request_event["payload"].get("agent_id") == "researcher"
    assert request_event["payload"].get("backend_id") == "dsh"

    pending = rt._handle.wait_permission(5)
    assert pending is not None
    rt._handle.answer_permission(
        pending, PermissionDecision(option_id="allow-once", reason="test",
                                    basis="human-hook"))
    thread.join(20)
    assert not thread.is_alive()
    assert "error" not in box
    assert box["result"].meta["executor"] == "acp"


# ---------------------------------------------------------------------------
# C3 — fallback / loud failure
# ---------------------------------------------------------------------------
def test_fallback_on_process_death_and_fast_fail_after(monkeypatch, tmp_path, runtime_factory):
    receipt = _native_env(monkeypatch, tmp_path, "die_mid_turn", fallback="crewai")
    inner = SpyScriptedRuntime()
    rt, bus = runtime_factory(inner=inner, fallback="crewai")

    result = rt.execute("researcher", "x", {})
    assert result.meta["executor"] == "scripted"
    assert result.meta["fallback"] is True
    assert result.meta["backend_session_id"] is None
    assert inner.calls == ["researcher"]

    events = _events(bus)
    types = _types(events)
    assert types.count("native_role_error") == 1
    assert types.count("native_fallback") == 1
    assert _count(_rows(receipt), "session/prompt") == 1

    started = time.monotonic()
    with pytest.raises(SpiError) as exc:
        rt.execute("researcher", "again", {})
    assert time.monotonic() - started < 2.0
    assert exc.value.code == "native.no_session"
    # no second attempt, no second fallback
    assert _types(_events(bus)).count("native_fallback") == 1
    assert _types(_events(bus)).count("native_role_error") == 1


def test_loud_failure_without_fallback(monkeypatch, tmp_path, runtime_factory):
    _native_env(monkeypatch, tmp_path, "die_mid_turn", fallback="off")
    rt, bus = runtime_factory(fallback="off")
    with pytest.raises(SpiError) as exc:
        rt.execute("researcher", "x", {})
    assert exc.value.kind == "process-died"
    assert "native_fallback" not in _types(_events(bus))


# ---------------------------------------------------------------------------
# C5 / C10 — concurrency, queue, close
# ---------------------------------------------------------------------------
def test_two_threads_create_exactly_one_session(monkeypatch, tmp_path, runtime_factory):
    receipt = _native_env(monkeypatch, tmp_path, "text_then_end")
    rt, _ = runtime_factory()
    barrier = threading.Barrier(2)
    results: list = []

    def _run(prompt):
        barrier.wait(5)
        results.append(rt.execute("researcher", prompt, {}))

    threads = [threading.Thread(target=_run, args=(p,)) for p in ("a", "bb")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert len(results) == 2
    rows = _rows(receipt)
    assert _count(rows, "session/new") == 1
    assert _count(rows, "session/prompt") == 2
    assert len({r.meta["backend_session_id"] for r in results}) == 1


def test_queue_overflow_is_loud(monkeypatch, tmp_path, runtime_factory):
    receipt = _native_env(monkeypatch, tmp_path, "hold_prompt")
    rt, _ = runtime_factory(queue_max=1)
    first: dict = {}

    def _run():
        first["result"] = rt.execute("researcher", "hold", {})

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _count(_rows(receipt), "session/prompt") < 1:
        time.sleep(0.05)
    assert _count(_rows(receipt), "session/prompt") == 1

    with pytest.raises(SpiError) as exc:
        rt.execute("researcher", "second", {})
    assert exc.value.code == "native.queue_full"

    rt._handle._session.client.notify("test/release", {})
    thread.join(20)
    assert not thread.is_alive()
    assert "result" in first


def test_close_is_bounded_when_server_never_answers_close(monkeypatch, tmp_path, runtime_factory):
    receipt = _native_env(monkeypatch, tmp_path, "close_hangs")
    rt, _ = runtime_factory()
    rt.execute("researcher", "x", {})
    started = time.monotonic()
    rt.close()
    elapsed = time.monotonic() - started
    # 60 s (client default) + grace; the contract accepts <= 80 s.
    assert elapsed <= 80.0, f"close took {elapsed:.1f}s"
    assert _count(_rows(receipt), "session/close") == 1


def test_close_reaps_the_acp_child(monkeypatch, tmp_path, runtime_factory):
    """C7 (automated): close() must actually end the ACP child process.

    Negative control: the fake server is alive before close. Then close() and
    require the OS process to be gone within 5 s (stdin EOF -> graceful exit).
    This covers the direct child only; grandchildren need a Job Object (R5).
    """
    _native_env(monkeypatch, tmp_path, "text_then_end")
    rt, _ = runtime_factory()
    rt.execute("researcher", "x", {})
    proc = rt._backend._manager._client._proc
    assert proc.poll() is None, "fake ACP server must be alive before close"

    rt.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)
    assert proc.poll() is not None, "ACP child survived close()"


# ---------------------------------------------------------------------------
# C2b — mixed graph run offline (native researcher + inner writer)
# ---------------------------------------------------------------------------
def test_graph_runs_native_and_inner_agents_side_by_side(monkeypatch, tmp_path):
    receipt = _native_env(monkeypatch, tmp_path, "text_then_end", fallback="off")
    monkeypatch.setenv("DISPATCH_HETERO", "1")
    spies: list = []

    class SpyScripted(SpyScriptedRuntime):
        def __init__(self, *args, **kwargs):
            super().__init__()
            spies.append(self)

    import graphs.runner as runner_mod
    monkeypatch.setattr(runner_mod, "ScriptedRuntime", SpyScripted)

    bus = EventBus()
    final = run_topology("supervisor", "写一段多 Agent 协同的结论。",
                         run_id=f"w4-mixed-{uuid.uuid4().hex[:8]}", session_id="w4-mixed-s",
                         llm=FakeLLM(script=default_llm_script), bus=bus)
    assert final["status"] == "done"
    assert spies, "the runner did not construct the inner runtime"

    events = _events(bus)
    worker = [e for e in events if e["type"] == "agent_output"
              and e["payload"].get("subtask_id")]
    assert worker, "no worker output"
    assert any(e["payload"].get("executor") == "acp"
               and e["payload"].get("backend_session_id")
               and e["payload"].get("agent_id") == "researcher" for e in worker)
    summarize = [e for e in events if e["type"] == "agent_output"
                 and e["payload"].get("stage") == "summarize"]
    assert summarize and summarize[0]["payload"].get("executor") == "scripted"
    assert summarize[0]["payload"].get("backend_session_id") is None

    assert "hetero_dispatch_started" not in _types(events)
    assert any(e["type"] == "hetero_dispatch_skipped"
               and e["payload"].get("reason") == "native-role-runtime"
               for e in events)
    # the native agent really used the fake session
    assert _count(_rows(receipt), "session/new") == 1
    assert _count(_rows(receipt), "session/prompt") >= 1


# ---------------------------------------------------------------------------
# C6 — off-state baseline (W4-1-0)
# ---------------------------------------------------------------------------
def _normalize(events) -> list[dict]:
    out = []
    for ev in events:
        d = ev.model_dump() if hasattr(ev, "model_dump") else dict(ev)
        d.pop("ts", None)
        d.pop("seq", None)
        d.pop("run_id", None)
        out.append(d)
    return out


def test_off_state_matches_the_pinned_baseline(monkeypatch):
    pytest.importorskip("graphs.runner", reason="graph layer absent")
    assert BASELINE.exists(), "W4-1-0 baseline file is missing"
    pinned = json.loads(BASELINE.read_text(encoding="utf-8"))
    monkeypatch.delenv("NATIVE_RUNTIME", raising=False)
    monkeypatch.delenv("NATIVE_AGENTS", raising=False)
    monkeypatch.delenv("NATIVE_FALLBACK", raising=False)
    monkeypatch.setenv("DISPATCH_HETERO", "0")

    cases = [("single", "W4 基线任务：1+1 等于几？只回答数字。", "w4off-single"),
             ("supervisor", "W4 基线任务：用一句话说明多 Agent 协同的代价。", "w4off-supervisor")]
    for mode, task, run_id in cases:
        default_llm_script._review_state = None
        # fixed run_id is required by the baseline (op_ids derive from it), but
        # health.json generation rotation is not idempotent across reruns.
        shutil.rmtree(run_dir(run_id), ignore_errors=True)
        bus = EventBus()
        run_topology(mode, task, run_id=run_id, session_id="w4off-session",
                     llm=FakeLLM(script=default_llm_script), bus=bus,
                     persistent=False, params={"recursion_limit": 48})
        got = _normalize(bus.replay(0))
        if mode == "single":
            assert got == pinned["single"]["ordered"], "single off-state drifted"
        else:
            got.sort(key=lambda e: json.dumps(e, sort_keys=True, ensure_ascii=False))
            assert got == pinned["supervisor"]["multiset"], "supervisor off-state drifted"


# ---------------------------------------------------------------------------
# preflight/config (config class: loud, never fallback)
# ---------------------------------------------------------------------------
def test_preflight_requires_the_registry_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_REGISTRY", "0")
    rt = ACPRuntime(run_id="r", platform_session_id="s",
                    agents=frozenset({"researcher"}),
                    store=SQLiteOpStore(tmp_path / "ops.sqlite3"))
    with pytest.raises(NativeConfigError) as exc:
        rt.preflight()
    assert exc.value.code == "native.registry_gate"


def test_preflight_rejects_an_unbound_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_REGISTRY", "1")
    rt = ACPRuntime(run_id="r", platform_session_id="s",
                    agents=frozenset({"writer"}),
                    store=SQLiteOpStore(tmp_path / "ops.sqlite3"))
    with pytest.raises(NativeConfigError) as exc:
        rt.preflight()
    assert exc.value.code == "native.agents_not_bound"


def test_preflight_environment_degrade_keeps_native_set(monkeypatch, tmp_path):
    """A failed preflight must still freeze the native set (exactly-once) and
    the degraded runtime must mark its agents as fallback in agent_output."""
    overrides = {
        "node_bin": sys.executable,
        "dsh_bin_js": "",                 # empty => backend.command_unresolved
        "dispatch_root": str(tmp_path / "dispatch"),
    }
    monkeypatch.setenv("AGENT_REGISTRY", "1")
    monkeypatch.setenv("NATIVE_RUNTIME", "dsh")
    monkeypatch.setenv("NATIVE_AGENTS", "researcher")
    monkeypatch.setenv("NATIVE_FALLBACK", "crewai")
    monkeypatch.setenv("DISPATCH_BACKEND_OVERRIDES", json.dumps(overrides))
    inner = SpyScriptedRuntime()
    rt = ACPRuntime(run_id="w4-degrade", platform_session_id="s",
                    agents=frozenset({"researcher"}), inner=inner,
                    fallback="crewai", store=SQLiteOpStore(tmp_path / "ops.sqlite3"))
    with pytest.raises(SpiError) as exc:
        rt.preflight()
    assert exc.value.code == "backend.command_unresolved"
    assert rt.skip_in_prepass == frozenset({"researcher"})
    rt.degrade(reason="preflight", code=exc.value.code, detail=str(exc.value))
    res = rt.execute("researcher", "x", {})
    assert res.meta["executor"] == "scripted"
    assert res.meta["fallback"] is True
    assert inner.calls == ["researcher"]


def test_empty_agent_set_is_a_valid_off_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_REGISTRY", "0")
    rt = ACPRuntime(run_id="r", platform_session_id="s", agents=frozenset(),
                    store=SQLiteOpStore(tmp_path / "ops.sqlite3"))
    assert rt.preflight()["disabled"] is True
    assert rt.native_agents == frozenset()
