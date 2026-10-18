"""P1 工单 1 · allocation=self_nominate integration tests.

Actual coverage (F2 erratum, do NOT claim more): 6 test functions cover
B1 + B3 (nomination events + invalid reason), B2' (shrunk-pool common
candidate score equality vs lead_pick), B4 (empty-nomination fallback),
B5 (default byte-identical), B6 (top-scorer-excluded discriminator),
B7 (partial-nomination pool-shrink), B7b (two nominees for one subtask).
B1..B7 numbering follows the P1 task-book; there is no separate B-test
for machine-check tags beyond what B1/B3 assert.

Lives in tests/integration: exercises graphs/builder.make_match_node via
run_topology (layercheck core scope bans graphs imports in tests/unit).

Discriminators:
- B6/B7: the top scorer (researcher) NOT nominating => synth must win s1;
  removing the pool filter makes researcher win => this test RED.

print/assert messages ASCII-only where ASCII (GBK console rule); Chinese only
in fixture strings copied from repo configs.
"""

import json

from core.events import EventBus
from graphs.runner import default_llm_script, run_topology

MODE = "supervisor"
PARAMS_OFF = {}                       # lead_pick (default)
PARAMS_ON = {"allocation": "self_nominate"}

VALID_S1 = [{"subtask_id": "s1", "tags": ["research"]}]
VALID_S2 = [{"subtask_id": "s2", "tags": ["analysis"]}]


def make_script(noms, review_pass=True):
    """FakeLLM script: self-nomination branch + PASS review + default rest."""

    def script(prompt, json_mode=False):
        if "自荐 agent" in prompt:
            aid = prompt.split("自荐 agent ", 1)[1].split("（", 1)[0].strip()
            return json.dumps({"nominations": noms.get(aid, [])},
                              ensure_ascii=False)
        if "审稿人" in prompt:
            return json.dumps({"verdict": "PASS" if review_pass else "REWORK",
                               "comments": "ok"}, ensure_ascii=False)
        return default_llm_script(prompt, json_mode)

    return script


def run_mode(noms, params, bus=None):
    from core.llm import FakeLLM
    from core.runtime import ScriptedRuntime
    from graphs.runner import DEFAULT_SCRIPTS

    bus = bus or EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(e))
    runtime = ScriptedRuntime({**DEFAULT_SCRIPTS,
                               "synth": ["综合:多Agent适合可分解任务。"]})
    final = run_topology(MODE, "多Agent协同调研任务", bus=bus,
                         llm=FakeLLM(script=make_script(noms)), runtime=runtime,
                         params=params)
    return final, seen


def ev(seen, type_):
    return [e for e in seen if e.type == type_]


def payload(e):
    return e.payload


def assignment_by_subtask(seen):
    return {payload(e)["subtask_id"]: payload(e) for e in ev(seen, "assignment")}


def valid_nominators(seen, sid):
    """Pool membership derived ONLY from nomination events: agent_ids with a
    VALID nomination for sid (machine-checkable pool, no shadow state)."""
    pool = set()
    for e in ev(seen, "nomination"):
        p = payload(e)
        for entry in p["nominations"]:
            if entry["subtask_id"] == sid and entry["valid"]:
                pool.add(p["agent_id"])
    return pool


# B1 + B3 + 策略留痕 -----------------------------------------------------------

def test_nomination_events_and_invalid_reason():
    # supervisor cites "research": not on its own card (domains=[planning,
    # review]) => invalid + reason recorded (B3); researcher/synth/analyst
    # nominate validly (B1: one nomination event per roster agent).
    noms = {
        "supervisor": VALID_S1,     # invalid: research not in domains
        "researcher": VALID_S1,
        "synth": VALID_S1,
        "analyst": VALID_S2,
    }
    _, seen = run_mode(noms, PARAMS_ON)
    strats = ev(seen, "allocation_strategy")
    assert len(strats) == 1, "exactly one allocation_strategy event"
    assert payload(strats[0])["strategy"] == "self_nominate"
    nom_events = ev(seen, "nomination")
    assert len(nom_events) >= 4, "nomination events must cover the roster"
    # supervisor's nomination is invalid with a reason (B3)
    sup = [payload(e) for e in nom_events if payload(e)["agent_id"] == "supervisor"]
    assert sup and sup[0]["nominations"], "supervisor nomination entry recorded"
    entry = sup[0]["nominations"][0]
    assert entry["valid"] is False
    assert "not on card" in entry["reason"]
    # researcher/synth valid
    for aid in ("researcher", "synth"):
        mine = [payload(e) for e in nom_events if payload(e)["agent_id"] == aid]
        assert mine[0]["nominations"][0]["valid"] is True
    # dispatch still happened
    assert assignment_by_subtask(seen), "assignments produced"


# B2' · 收紧口径：池缩小时共同候选者与 lead_pick 逐值相等 ----------------------

def test_common_candidates_scores_equal():
    # SHRUNK pool (F2): only synth->s1 and analyst->s2 nominate, so neither
    # pool equals the full roster. analyst is lead_pick's s2 winner AND stays
    # in the shrunk s2 pool => a "common candidate" wins in both modes and
    # its (agent_id, score) must be value-identical (V1.1 B2, non-trivial).
    noms = {"synth": VALID_S1, "analyst": VALID_S2}
    _, off = run_mode(noms, PARAMS_OFF)
    _, on = run_mode(noms, PARAMS_ON)
    a_off, a_on = assignment_by_subtask(off), assignment_by_subtask(on)
    assert set(a_off) == set(a_on), "same subtasks dispatched"
    for sid in ("s1", "s2"):
        pool = valid_nominators(on, sid)
        if a_off[sid]["agent_id"] in pool:
            # lead_pick winner survived the pool filter: common candidate
            assert a_off[sid]["agent_id"] == a_on[sid]["agent_id"], \
                f"{sid}: common candidate must still win"
            assert a_off[sid]["score"] == a_on[sid]["score"], \
                f"score drifted for {sid}: {a_off[sid]} vs {a_on[sid]}"


# B7 · 非全员自荐夹具：池必须真的缩小 ------------------------------------------

def test_partial_nomination_shrinks_pool():
    # only synth->s1 and analyst->s2 nominate. researcher (lead_pick's s1
    # winner, score 1.54) is OUT of the s1 pool => s1 must go to synth;
    # every winner must be inside the machine-derived valid pool.
    noms = {"synth": VALID_S1, "analyst": VALID_S2}
    _, off = run_mode(noms, PARAMS_OFF)
    _, seen = run_mode(noms, PARAMS_ON)
    a = assignment_by_subtask(seen)
    pools = {sid: valid_nominators(seen, sid) for sid in ("s1", "s2")}
    assert pools["s1"] == {"synth"}, pools["s1"]
    assert pools["s2"] == {"analyst"}, pools["s2"]
    assert "researcher" not in pools["s1"], "top scorer must be excluded"
    for sid in ("s1", "s2"):
        assert a[sid]["agent_id"] in pools[sid], \
            f"{sid}: winner outside the valid pool: {a[sid]}"
    assert a["s1"]["agent_id"] == "synth", \
        f"shrunk pool must force synth, got {a['s1']['agent_id']}"


# B7b · 同一子任务 >=2 人自荐 ---------------------------------------------------

def test_two_nominees_same_subtask_pool_respected():
    # researcher AND synth both nominate s1 (pool of 2) => researcher (top
    # scorer, now inside the pool) must win, and the winner stays pool-bound;
    # s2 has nobody => fallback still dispatches it (B4 path).
    noms = {"researcher": VALID_S1, "synth": VALID_S1}
    _, seen = run_mode(noms, PARAMS_ON)
    a = assignment_by_subtask(seen)
    pool = valid_nominators(seen, "s1")
    assert pool == {"researcher", "synth"}, pool
    assert a["s1"]["agent_id"] == "researcher", \
        f"in-pool top scorer must win, got {a['s1']['agent_id']}"
    assert a["s1"]["agent_id"] in pool
    fb = ev(seen, "allocation_fallback")
    assert fb and "s2" in set(payload(fb[0])["subtask_ids"]), \
        "empty s2 pool must fall back"


# B4 · 空自荐兜底 ---------------------------------------------------------------

def test_no_nomination_fallback():
    _, seen = run_mode({}, PARAMS_ON)   # nobody nominates
    fb = ev(seen, "allocation_fallback")
    assert fb, "allocation_fallback must be emitted"
    assert set(payload(fb[0])["subtask_ids"]) >= {"s1", "s2"}
    a = assignment_by_subtask(seen)
    assert a, "fallback still produces assignments"
    _, off = run_mode({}, PARAMS_OFF)
    a_off = assignment_by_subtask(off)
    for sid in ("s1", "s2"):
        assert a[sid]["agent_id"] == a_off[sid]["agent_id"], \
            "fallback must match lead_pick outcome"
        assert a[sid]["score"] == a_off[sid]["score"]


# B6 · 变异判别器: 池过滤被摘掉 ⇒ 本测试必红 -----------------------------------

def test_top_scorer_without_nomination_loses_s1():
    # researcher is s1's top scorer; if the pool filter works, its absence
    # forces synth. Mutation (filter removed) => researcher wins => RED.
    noms = {"synth": VALID_S1, "analyst": VALID_S2}   # researcher NOT nominating
    _, seen = run_mode(noms, PARAMS_ON)
    a = assignment_by_subtask(seen)
    assert a.get("s1", {}).get("agent_id") == "synth", \
        f"pool filter broken: s1 winner={a.get('s1', {}).get('agent_id')!r} (researcher expected to lose)"


# B5 · 缺省逐字节一致（归一化口径）---------------------------------------------

def test_default_byte_identical_to_baseline():
    # no allocation param => assignment segment (canonical) equals a run where
    # allocation=lead_pick explicitly; nomination/strategy events absent.
    _, seen_default = run_mode({}, {})
    _, seen_explicit = run_mode({}, {"allocation": "lead_pick"})
    assert not ev(seen_default, "nomination"), "default must not nominate"
    assert not ev(seen_default, "allocation_strategy"), "default must not emit strategy"
    a1 = assignment_by_subtask(seen_default)
    a2 = assignment_by_subtask(seen_explicit)
    assert {k: (v["agent_id"], v["score"], v["reason"]) for k, v in a1.items()} == \
           {k: (v["agent_id"], v["score"], v["reason"]) for k, v in a2.items()}
