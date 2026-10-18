"""P1-HOSTPICK acceptance tests (workorder sha256_16 cc2599dbf0a6ed77).

Q1  default byte-identical (canonical normalization strips ts/seq)
Q2  host pick requested ONLY for subtasks with >=2 gate-passing candidates
Q3  endpoint 409/422 matrix (Q7 shares this matrix; canvas-paths-all-GET
    remains covered by test_logic_canvas C6 and is NOT re-asserted here)
Q4  bounded wait -> allocation_fallback{reason: host_timeout}, run completes
Q5  any conflict -> allocation_conflict event + loud ConfigError
Q6  allocation events anchor the subtask (subtask_id/agent_id/author/ts) and
    assignments carry chosen_by in {auto, host, pin} (omitted only in
    fully-default runs — documented deviation, preserves Q1 byte-identity)
Q8  liveness: host_pick_wait alive within the event-carried deadline (B2),
    dead after it; answered closes the request; unreadable trace -> not
    pending (B7); permission pairing regression intact

Fixtures reuse the P1 nominate harness (FakeLLM + ScriptedRuntime). Host
answers are delivered by a bus subscriber writing allocation_decision.json
into the run dir — the same file _wait_host_answer polls (the real endpoint
writes it atomically; subscriber ordering puts us BEFORE the trace writer,
so the endpoint cannot be used in-process during a run).

print/assert messages ASCII-only (GBK console rule).
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import pytest

from core.events import EventBus
from graphs.runner import default_llm_script, run_topology

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _runs_cleanup import cleanup_run  # noqa: E402  (P3/B-1 shared fail-loud helper)


MODE = "supervisor"


# --- harness ------------------------------------------------------------------

def _new_run_id(tag: str) -> str:
    return f"hp-{tag}-{uuid.uuid4().hex[:8]}"


def _cleanup_run(rid: str) -> None:
    # P3 (B-1): delegate to the shared fail-loud helper. The old
    # shutil.rmtree(..., ignore_errors=True) silently swallowed failures and
    # leaked statusless hp-* dirs (T1: 11 residue dirs in one full run).
    cleanup_run(rid)


def make_script(plan=None):
    """FakeLLM script: optional custom plan JSON + PASS review + default
    rest (no nomination branch — host_pick strategies that need nominations
    use per-subtask self_nominate via allocation_map, not tested through
    this harness)."""
    def script(prompt, json_mode=False):
        if "拆解" in prompt and plan is not None:
            return json.dumps({"subtasks": plan}, ensure_ascii=False)
        if "审稿人" in prompt:
            return json.dumps({"verdict": "PASS", "comments": "ok"},
                              ensure_ascii=False)
        return default_llm_script(prompt, json_mode)
    return script


def answer_writer(chooser):
    """Bus subscriber: on allocation_choice_requested, write the host
    decision file directly (simulates the host answering via the API).
    ``chooser(subtask_id, payload) -> agent_id or None`` (None = stay
    silent -> timeout path)."""
    def hook(event):
        if event.type != "allocation_choice_requested":
            return
        sid = event.payload.get("subtask_id")
        aid = chooser(sid, event.payload)
        if aid is None:
            return
        from core.paths import run_dir
        path = run_dir(event.run_id) / "allocation_decision.json"
        items: list = []
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    items = loaded
            except ValueError:
                pass
        items.append({"subtask_id": sid, "agent_id": aid,
                      "chosen_by": "host", "author": "test-host"})
        path.write_text(json.dumps(items, ensure_ascii=False),
                        encoding="utf-8")
    return hook


def run_hp(params, chooser=None, tag="x", collect=None, plan=None):
    """One supervisor run in host-pick mode with a unique run_id; cleans up
    its run dir afterwards. Returns (final, seen)."""
    rid = _new_run_id(tag)
    from core.llm import FakeLLM
    from core.runtime import ScriptedRuntime
    from graphs.runner import DEFAULT_SCRIPTS

    bus = EventBus()
    seen: list = []
    bus.subscribe(seen.append)
    if chooser is not None:
        bus.subscribe(answer_writer(chooser))
    full = {"allocation_host_timeout_s": 5.0, **params}
    try:
        final = run_topology(
            MODE, "多Agent协同调研任务", run_id=rid, bus=bus,
            llm=FakeLLM(script=make_script(plan)),
            runtime=ScriptedRuntime({**DEFAULT_SCRIPTS,
                                     "synth": ["综合:多Agent适合可分解任务。"]}),
            params=full)
    finally:
        if collect is not None:
            collect.append(rid)
        _cleanup_run(rid)
    return final, seen


def ev(seen, type_):
    return [e for e in seen if e.type == type_]


def assignments(seen):
    return {e.payload["subtask_id"]: e.payload
            for e in ev(seen, "assignment")}


# --- Q1: default byte-identical ------------------------------------------------

def test_q1_default_byte_identical():
    """No allocation params => identical assignment segment (agent_id,
    score, reason) as explicit lead_pick; NO host-pick machinery events and
    no chosen_by key on assignments (documented deviation)."""
    _, d = run_hp({}, tag="q1d")
    _, x = run_hp({"allocation": "lead_pick"}, tag="q1x")
    a1, a2 = assignments(d), assignments(x)
    assert {k: (v["agent_id"], v["score"], v["reason"])
            for k, v in a1.items()} == \
           {k: (v["agent_id"], v["score"], v["reason"])
            for k, v in a2.items()}
    for t in ("allocation_strategy", "allocation_choice_requested",
              "allocation_answer", "allocation_fallback",
              "allocation_conflict"):
        assert not ev(d, t), f"default run must not emit {t}"
    assert all("chosen_by" not in p for p in a1.values())


# --- Q2: only >=2-candidate subtasks ask ---------------------------------------

def test_q2_only_multi_candidate_subtasks_ask():
    _, seen = run_hp({"allocation": "self_nominate_host_pick"},
                     chooser=lambda sid, pl: pl["candidates"][0]["agent_id"],
                     tag="q2")
    reqs = ev(seen, "allocation_choice_requested")
    assert reqs, "at least one host request in host_pick mode"
    for e in reqs:
        p = e.payload
        assert len(p["candidates"]) >= 2, \
            f"pool check broken: {p['subtask_id']} asked with " \
            f"{len(p['candidates'])} candidate(s)"
        assert p["runner_up"], "runner_up recorded"
        assert p["timeout_s"] > 0 and p["deadline_ts"] > time.time() - 1
        assert isinstance(p["taken"], list)
    asked = {e.payload["subtask_id"] for e in reqs}
    assert asked <= set(assignments(seen)), "asked subtasks all dispatched"


def test_q2b_single_candidate_subtask_never_asks():
    """Q2 discriminator load: a plan with s2 requiring 'data' (ONLY analyst
    has it on card) gives s2 a 1-candidate pool — s2 must be auto-assigned
    with NO host request. Mutation removing the >=2 check makes s2 ask with
    1 candidate => this test RED."""
    plan = [
        {"subtask_id": "s1", "description": "调研多Agent协同的工程现状",
         "required_capabilities": ["research"]},
        {"subtask_id": "s2", "description": "统计各拓扑的运行数据",
         "required_capabilities": ["data"]},
    ]
    _, seen = run_hp({"allocation": "self_nominate_host_pick"},
                     chooser=lambda sid, pl: pl["candidates"][0]["agent_id"],
                     tag="q2b", plan=plan)
    asked = {e.payload["subtask_id"]: e.payload
             for e in ev(seen, "allocation_choice_requested")}
    assert "s2" not in asked, \
        f"single-candidate subtask must not ask the host: {asked.get('s2')}"
    for p in asked.values():
        assert len(p["candidates"]) >= 2, p
    a = assignments(seen)
    assert a.get("s2", {}).get("agent_id") == "analyst", \
        f"s2 (data-only pool) must auto-assign to analyst: {a.get('s2')}"


# --- Q3/Q7: endpoint 409/422 matrix ---------------------------------------------

@pytest.fixture()
def alloc_env(tmp_path, monkeypatch):
    """A fake run dir whose trace holds one pending host request (s1:
    researcher/synth, taken=[]) plus a second request (s2, taken=[analyst])."""
    rid = "hp-alloc-env"
    rd = tmp_path / rid
    rd.mkdir()
    monkeypatch.setattr("api.routes.run_dir", lambda r: tmp_path / r)
    now = time.time()
    events = [
        {"seq": 1, "ts": now, "type": "run_started", "payload": {}},
        {"seq": 2, "ts": now, "type": "allocation_choice_requested",
         "payload": {"subtask_id": "s1",
                     "candidates": [{"agent_id": "researcher"},
                                    {"agent_id": "synth"}],
                     "runner_up": "synth", "timeout_s": 600,
                     "deadline_ts": now + 600, "taken": []}},
        {"seq": 3, "ts": now, "type": "allocation_choice_requested",
         "payload": {"subtask_id": "s2",
                     "candidates": [{"agent_id": "analyst"},
                                    {"agent_id": "synth"}],
                     "runner_up": "synth", "timeout_s": 600,
                     "deadline_ts": now + 600, "taken": ["analyst"]}},
    ]
    (rd / "trace.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return {"run_id": rid, "dir": rd}


def _call_alloc(rid, body):
    from api import routes
    return routes.add_allocation_decision(rid, body)


def test_q3_unknown_run_404(alloc_env):
    with pytest.raises(routes_http_exc()) as ei:
        _call_alloc("hp-nope", {"subtask_id": "s1", "agent_id": "synth"})
    assert ei.value.status_code == 404


def routes_http_exc():
    from api.routes import HTTPException
    return HTTPException


def test_q3_missing_fields_422(alloc_env):
    for body in ({"agent_id": "synth"}, {"subtask_id": "s1"}, {}):
        with pytest.raises(routes_http_exc()) as ei:
            _call_alloc(alloc_env["run_id"], body)
        assert ei.value.status_code == 422


def test_q3_agent_outside_pool_422(alloc_env):
    with pytest.raises(routes_http_exc()) as ei:
        _call_alloc(alloc_env["run_id"],
                    {"subtask_id": "s1", "agent_id": "analyst"})
    assert ei.value.status_code == 422
    assert "not in the candidate pool" in ei.value.detail


def test_q3_agent_taken_422(alloc_env):
    with pytest.raises(routes_http_exc()) as ei:
        _call_alloc(alloc_env["run_id"],
                    {"subtask_id": "s2", "agent_id": "analyst"})
    assert ei.value.status_code == 422
    assert "taken" in ei.value.detail


def test_q3_valid_answer_appends_then_repeat_409(alloc_env):
    out = _call_alloc(alloc_env["run_id"],
                      {"subtask_id": "s1", "agent_id": "synth",
                       "author": "host-alice"})
    assert out["appended"]["agent_id"] == "synth"
    assert out["appended"]["chosen_by"] == "host"
    assert out["appended"]["author"] == "host-alice"
    assert "ts" in out["appended"], "helper owns the ts"
    saved = json.loads((alloc_env["dir"] / "allocation_decision.json")
                       .read_text(encoding="utf-8"))
    assert len(saved) == 1 and saved[0]["subtask_id"] == "s1"
    # same subtask answered twice -> no longer pending
    with pytest.raises(routes_http_exc()) as ei:
        _call_alloc(alloc_env["run_id"],
                    {"subtask_id": "s1", "agent_id": "researcher"})
    assert ei.value.status_code == 409


def test_q3_unrequested_subtask_409(alloc_env):
    with pytest.raises(routes_http_exc()) as ei:
        _call_alloc(alloc_env["run_id"],
                    {"subtask_id": "s9", "agent_id": "synth"})
    assert ei.value.status_code == 409


def test_q3_corrupt_decision_file_409(alloc_env):
    (alloc_env["dir"] / "allocation_decision.json").write_text(
        "{not json", encoding="utf-8")
    with pytest.raises(routes_http_exc()) as ei:
        _call_alloc(alloc_env["run_id"],
                    {"subtask_id": "s1", "agent_id": "synth"})
    assert ei.value.status_code == 409
    assert "corrupt" in ei.value.detail
    # the torn file was NOT silently reset
    assert (alloc_env["dir"] / "allocation_decision.json").read_text(
        encoding="utf-8") == "{not json"


def test_q3_terminal_after_last_request_409(alloc_env):
    with open(alloc_env["dir"] / "trace.jsonl", "a",
              encoding="utf-8") as fh:
        fh.write(json.dumps({"seq": 4, "ts": time.time(),
                             "type": "run_finished", "payload": {}}) + "\n")
    with pytest.raises(routes_http_exc()) as ei:
        _call_alloc(alloc_env["run_id"],
                    {"subtask_id": "s1", "agent_id": "synth"})
    assert ei.value.status_code == 409


# --- Q4: bounded wait -> host_timeout fallback ----------------------------------

def test_q4_timeout_falls_back_auto():
    t0 = time.time()
    final, seen = run_hp({"allocation": "self_nominate_host_pick",
                          "allocation_host_timeout_s": 1.0},
                         chooser=None, tag="q4")
    elapsed = time.time() - t0
    fb = ev(seen, "allocation_fallback")
    assert fb, "allocation_fallback must be emitted on host timeout"
    # a self_nominate_host_pick run may also emit the nomination-pool
    # fallback ("no_valid_nomination...") — require the host_timeout one
    assert any(p.get("reason") == "host_timeout"
               for p in (e.payload for e in fb)), \
        [e.payload for e in fb]
    a = assignments(seen)
    assert a, "timeout still auto-assigns"
    assert final["status"] == "done"
    assert elapsed < 30, f"wait not bounded: {elapsed:.1f}s"


# --- Q5: conflicts are loud ------------------------------------------------------

def _run_hp_capture(params, chooser, tag, seen):
    """run_hp variant for EXPECTED-FAILURE runs: appends events into the
    caller-owned ``seen`` list (the run raises, so a return value would be
    lost); re-raises whatever run_topology raises."""
    from core.llm import FakeLLM
    from core.runtime import ScriptedRuntime
    from graphs.runner import DEFAULT_SCRIPTS

    rid = _new_run_id(tag)
    bus = EventBus()
    bus.subscribe(seen.append)
    if chooser is not None:
        bus.subscribe(answer_writer(chooser))
    full = {"allocation_host_timeout_s": 5.0, **params}
    try:
        return run_topology(MODE, "多Agent协同调研任务", run_id=rid, bus=bus,
                            llm=FakeLLM(script=make_script()),
                            runtime=ScriptedRuntime({**DEFAULT_SCRIPTS,
                                                     "synth": ["综合:ok。"]}),
                            params=full)
    finally:
        _cleanup_run(rid)


def test_q5_duplicate_pin_conflict_loud():
    from core.errors import ConfigError
    seen: list = []
    with pytest.raises(ConfigError):        # loud, never silent reassignment
        _run_hp_capture(
            {"allocation": "self_nominate_host_pick",
             "pins": {"s1": "researcher", "s2": "researcher"}},
            lambda sid, pl: pl["candidates"][0]["agent_id"], "q5dup", seen)
    cf = ev(seen, "allocation_conflict")
    assert cf, "allocation_conflict event must be emitted"
    reasons = {c.get("reason") for e in cf for c in e.payload["pairs"]}
    assert any("already pinned" in r for r in reasons), reasons


def test_q5_pin_fails_gates_conflict_loud():
    from core.errors import ConfigError
    # supervisor's domains are planning/review, not research -> gate fails
    seen: list = []
    with pytest.raises(ConfigError):
        _run_hp_capture(
            {"allocation": "self_nominate_host_pick",
             "pins": {"s1": "supervisor"}},
            lambda sid, pl: pl["candidates"][0]["agent_id"], "q5gate", seen)
    cf = ev(seen, "allocation_conflict")
    assert cf, "gate-failing pin must conflict loudly"
    reasons = {c.get("reason") for e in cf for c in e.payload["pairs"]}
    assert any(("gates" in r) or ("pool" in r) for r in reasons), reasons


def test_q5_host_answer_outside_pool_conflict_loud():
    from core.errors import ConfigError
    with pytest.raises(ConfigError):
        run_hp({"allocation": "self_nominate_host_pick"},
               chooser=lambda sid, pl: "supervisor",   # never a candidate
               tag="q5ans")
    # conflict event recorded on the bus inside run_hp -> re-run capturing:
    from core.llm import FakeLLM
    from core.runtime import ScriptedRuntime
    from graphs.runner import DEFAULT_SCRIPTS
    rid = _new_run_id("q5ans2")
    bus = EventBus()
    seen: list = []
    bus.subscribe(seen.append)
    bus.subscribe(answer_writer(lambda sid, pl: "supervisor"))
    with pytest.raises(ConfigError):
        run_topology(MODE, "多Agent协同调研任务", run_id=rid, bus=bus,
                     llm=FakeLLM(script=make_script()),
                     runtime=ScriptedRuntime({**DEFAULT_SCRIPTS,
                                              "synth": ["综合:ok。"]}),
                     params={"allocation": "self_nominate_host_pick",
                             "allocation_host_timeout_s": 5.0})
    _cleanup_run(rid)
    cf = ev(seen, "allocation_conflict")
    assert cf, "out-of-pool host answer must emit allocation_conflict"
    reasons = {c.get("reason") for e in cf for c in e.payload["pairs"]}
    assert any("out of pool" in r for r in reasons), reasons


# --- Q6: anchors + chosen_by -----------------------------------------------------

def test_q6_host_answer_chosen_by_and_anchors():
    _, seen = run_hp({"allocation": "self_nominate_host_pick"},
                     chooser=lambda sid, pl: pl["candidates"][0]["agent_id"],
                     tag="q6")
    ans = ev(seen, "allocation_answer")
    assert ans, "allocation_answer anchor event emitted"
    for e in ans:
        p = e.payload
        assert p["subtask_id"] and p["agent_id"], "anchored to a subtask"
        assert p["chosen_by"] == "host"
        assert p["author"], "author recorded"
    a = assignments(seen)
    assert a, "assignments produced"
    for sid, p in a.items():
        assert p.get("chosen_by") in ("auto", "host", "pin"), \
            f"{sid}: chosen_by missing/invalid in host-pick mode: {p}"
    answered = {e.payload["subtask_id"] for e in ans}
    for sid in answered:
        assert a[sid]["chosen_by"] == "host"
        assert "chosen by host" in a[sid]["reason"]


def test_q6_pin_chosen_by_pin():
    _, seen = run_hp({"allocation": "self_nominate_host_pick",
                      "pins": {"s1": "researcher"}},
                     chooser=lambda sid, pl: pl["candidates"][0]["agent_id"],
                     tag="q6pin")
    a = assignments(seen)
    assert a["s1"]["agent_id"] == "researcher"
    assert a["s1"]["chosen_by"] == "pin"
    assert a["s1"]["reason"] == "pinned by host"


# --- Q8: liveness -----------------------------------------------------------------

@pytest.fixture()
def live_env(tmp_path, monkeypatch):
    import agent_bridge.recovery as rec_mod
    rid = "hp-live"
    rd = tmp_path / rid
    rd.mkdir()
    monkeypatch.setattr(rec_mod, "run_dir", lambda r: tmp_path / r)
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh_home"))
    monkeypatch.setenv("WB_AUDIT_DIR", str(tmp_path / "wb_audit"))

    def _state(status):
        (rd / "state.json").write_text(json.dumps({"status": status}),
                                       encoding="utf-8")

    def _trace(events):
        (rd / "trace.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8")

    return {"run_id": rid, "dir": rd, "state": _state, "trace": _trace}


def _req_event(seq, deadline, ts=None):
    return {"seq": seq, "ts": ts if ts is not None else time.time(),
            "type": "allocation_choice_requested",
            "payload": {"subtask_id": "s1", "timeout_s": 600,
                        "deadline_ts": deadline,
                        "candidates": [{"agent_id": "researcher"},
                                       {"agent_id": "synth"}],
                        "taken": []}}


def test_q8_host_pick_wait_alive_within_window(live_env):
    from agent_bridge.recovery import derive_liveness
    live_env["state"]("running")
    live_env["trace"]([
        {"seq": 1, "ts": time.time(), "type": "run_started", "payload": {}},
        _req_event(2, time.time() + 600),
    ])
    liv = derive_liveness(live_env["run_id"])
    assert "host_pick_wait" in liv["live_paths"], liv
    assert liv["stalled"] is False
    assert liv["pending_allocation"] == ["s1"]


def test_q8_host_pick_wait_dead_after_deadline(live_env):
    from agent_bridge.recovery import derive_liveness
    old = time.time() - 3600
    live_env["state"]("running")
    live_env["trace"]([
        {"seq": 1, "ts": old, "type": "run_started", "payload": {}},
        _req_event(2, old + 600, ts=old),
    ])
    liv = derive_liveness(live_env["run_id"])
    assert "host_pick_wait" not in liv["live_paths"], liv
    assert liv["stalled"] is True, \
        "expired host wait must NOT keep the run alive (B2 bound)"


def test_q8_host_pick_wait_is_external_not_self_window(live_env):
    """R2 (DSH review): host_pick_wait must be registered as EXTERNAL artifact
    evidence in ``assess_and_recover``'s whitelist. If it is not, a host wait is
    treated as the machine's OWN open window (the run stays in-cycle/queued
    instead of being marked 'recovered'). This assertion is deliberately direct:
    removing "host_pick_wait" from the whitelist must turn THIS test red, so the
    protection sits in the same file as the feature it guards."""
    from agent_bridge.recovery import assess_and_recover, save_recovery
    from core.storage import InMemoryOpStore

    live_env["state"]("running")
    # The request must be OLDER than the silence window (600s) while still inside
    # the host deadline. Otherwise `active_run` would also be a live path and
    # `artifact_live` would be non-empty even without the whitelist entry — the
    # assertion below would then pass either way and discriminate nothing.
    req_ts = time.time() - 900
    live_env["trace"]([
        {"seq": 1, "ts": req_ts, "type": "run_started", "payload": {}},
        _req_event(2, time.time() + 600, ts=req_ts),
    ])
    # a machine-owned window is open (queued); the host wait must outrank it
    save_recovery(live_env["run_id"],
                  {"state": "queued", "queued_at": time.time()})
    rec = assess_and_recover(live_env["run_id"], store=InMemoryOpStore())
    # NOTE: live_paths also carries the machine's own queued_continuation window,
    # so assert MEMBERSHIP (not equality) here; the discriminating assertion is
    # the state below: artifact_live is ["host_pick_wait"] with the whitelist
    # entry and [] without it.
    assert "host_pick_wait" in rec["live_paths"], rec
    assert rec["state"] == "recovered", \
        "an un-expired host wait is EXTERNAL evidence and must mark the run recovered"


def test_q8_answered_request_closes(live_env):
    from agent_bridge.recovery import derive_liveness
    live_env["state"]("running")
    req = _req_event(2, time.time() + 600)
    ans = {"seq": 3, "ts": time.time(), "type": "allocation_answer",
           "payload": {"subtask_id": "s1", "agent_id": "synth",
                       "chosen_by": "host", "author": "host"}}
    live_env["trace"]([
        {"seq": 1, "ts": time.time(), "type": "run_started", "payload": {}},
        req, ans,
    ])
    liv = derive_liveness(live_env["run_id"])
    assert "host_pick_wait" not in liv["live_paths"], liv
    assert liv["pending_allocation"] == []


def test_q8_unreadable_trace_not_pending(live_env):
    from agent_bridge.recovery import derive_liveness
    live_env["state"]("running")
    (live_env["dir"] / "trace.jsonl").write_bytes(b"\xff\xfe\x00 broken")
    liv = derive_liveness(live_env["run_id"])
    assert liv["pending_allocation"] == []
    assert liv["pending_permission"] is False
    assert "host_pick_wait" not in liv["live_paths"]


def test_q8_permission_pairing_regression(live_env):
    from agent_bridge.recovery import derive_liveness
    live_env["state"]("running")
    live_env["trace"]([
        {"seq": 1, "ts": time.time(), "type": "run_started", "payload": {}},
        {"seq": 2, "ts": time.time(), "type": "dsh_permission_request",
         "payload": {"tool": "shell"}},
    ])
    liv = derive_liveness(live_env["run_id"])
    assert "explicit_wait" in liv["live_paths"], liv
    assert liv["pending_permission"] is True


def test_q8_real_stall_detection_not_weakened(live_env):
    from agent_bridge.recovery import derive_liveness
    old = time.time() - 3600
    live_env["state"]("running")
    live_env["trace"]([
        {"seq": 1, "ts": old, "type": "run_started", "payload": {}},
        {"seq": 2, "ts": old, "type": "node_end", "payload": {}},
    ])
    liv = derive_liveness(live_env["run_id"])
    assert liv["stalled"] is True, liv
    assert liv["live_paths"] == []
