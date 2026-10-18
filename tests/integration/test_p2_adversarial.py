"""P2-2 acceptance tests (workorder sha256_16 d24b14312defa30f, Appendix C;
P2-FIX workorder 43a062783c03b561, Appendix D).

R5  (K2) canonical byte-identity with the switch OFF: run supervisor once
    with run_id="fixture-p2-fixed", digest the trace with the canonical
    strip list {ts, seq, run_id, session_id, created_at, finished_at}
    (stripped at ANY level), json.dumps(sort_keys=True, ensure_ascii=False),
    UTF-8 sha256 — must equal the PRE-CHANGE baseline
    53fb3cacb62581e1f700b11a57bf6db8f46943dfa6e3e6f0069db033afbbe0f7
    (captured from commit b3dd6f1, evidence: D:/WorkBuddy/p2_evidence/
    r5_baseline_digest.txt).
K3  OFF-state arbitration events carry NO "source" key.
R6  ON-state semantics: the rework round triggers exactly ONE extra
    adversarial LLM call; the convergence is emitted on the EXISTING
    arbitration event type with source="enhanced"; the critique is merged
    into the rework round's review_comments; the round gate
    (max_review_rounds) still bounds it (gate-exhausted round => 0 calls).
R7  field-shape comparison: the enhanced arbitration event adds EXACTLY
    {source} on top of the canonical pick shape {key, action, value, why}.

P2-FIX isolation (Appendix D, approved): the fixture run executes in a
FRESH SUBPROCESS (tests/integration/_p2fix_r5_child.py) with a scrubbed
environment — ROOT CAUSE was host behavior-switch env leakage
(DISPATCH_HETERO=1 adds hetero_dispatch_* events and shifts the digest);
"order dependence" was withdrawn by DSH (D1). The in-process variant
`_run_supervisor_inprocess` is kept ONLY as the F2 discriminator target:
removing the isolation and running with DISPATCH_HETERO=1 must turn R5
red again.

R5 CRITERION (workorder sha256_16 0f37e591032154c9, Appendix F1 C1):
`test_r5` asserts MOD-FANOUT-SCHEDULING EQUIVALENCE (模扇出调度等价),
**NOT byte-identity**: the digest is taken after the slot-stable-sort
canonicalisation implemented in the child (_p2fix_r5_child.py), which
absorbs ONLY the ordering of events inside worker fan-out windows (the
thread-scheduling draw LangGraph makes for the Send fan-out). Everything
outside those windows keeps trace order byte-for-byte (C2 assert runs on
every execution). The baseline constant 53fb3cac...（rev2，见 docs/baseline_changes.md） is FROZEN and the
canonicalisation is fixed-point on it (identity on the faithful baseline
trace); all captured drift families (f464f3bf x9, bd2f223b, ff43a6d0,
b01b4e1c) are absorbed back to it, and 0/40320 window arrangements escape
(probe_upgrade_out.txt, f0_absorption_table.txt). Loud failure on unknown
agents/event types inside a window (C3); idempotence + bijection asserted
per run (C4).

P2-FIX2 (workorder sha256_16 2a4a0240efe31f05, Appendix E1): the previous
interleaving gate was proven DEAD CODE (workers go through
ctx.runtime.execute, never the LLM — probe_prompts_out.json) and is
removed. R5's digest now uses the narrow MOD-FANOUT-INTERLEAVE
EQUIVALENCE (模扇出交错等价) canonicalisation implemented in the child
(_p2fix_r5_child.py:_sort_fanout_runs): only maximal contiguous runs of
{blackboard_write, agent_output} are stable-sorted by agent_id DESCENDING;
all other events keep trace order. It absorbs ONLY the concurrent
fan-out write/output ordering (the observed drift), never content:
fixed point on the frozen baseline measured (probe_narrowing_out.txt),
drifted variant absorbed (b01b4e1c... -> 53fb3cac...（rev2，见 docs/baseline_changes.md）), and S4 guards
(1-payload-byte change / payload swap => digest MUST change).

Stripped/pinned env (D3 list — keep in sync with the P2FIX report):
  stripped: PYTHONPATH, PYTHONIOENCODING, PYTHONUTF8,
            DISPATCH_HETERO, AGENT_REGISTRY, WB_ENABLED, R16_TRACE,
            DSH_HOME, WB_AUDIT_DIR, REDACTION_ENABLED,
            DISPATCH_BACKEND_OVERRIDES, DISPATCH_CONSENT_TOKEN,
            DISPATCH_DATA_OPT_IN, DISPATCH_COUNTERS_ALLOW_CORRUPT
  pinned:   PYTHONHASHSEED=0

print/assert messages ASCII-only (GBK console rule).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from core.events import EventBus
from core.llm import FakeLLM
from core.paths import PROJECT_ROOT, run_dir
from core.runtime import ScriptedRuntime
from graphs.runner import DEFAULT_SCRIPTS, default_llm_script, run_topology

RUN_ID = "fixture-p2-fixed"
RUN_ID_INPROC = "fixture-p2-inproc"    # in-process F2/R6/R7 runs use their own
                                       # dir; since P3 the writer is closed in
                                       # run_topology's finally, so cleanup no
                                       # longer races an open handle (B-1)
# RE-BASELINED twice for M1/W0-1 (rev2: B1/B2 honesty fix); see docs/baseline_changes.md
BASELINE_DIGEST = "53fb3cacb62581e1f700b11a57bf6db8f46943dfa6e3e6f0069db033afbbe0f7"
STRIP_KEYS = {"ts", "seq", "run_id", "session_id", "created_at", "finished_at"}

sys.path.insert(0, str(PROJECT_ROOT / "tests" / "integration"))
from _runs_cleanup import cleanup_run  # noqa: E402  (P3/B-1 shared fail-loud helper)

CHILD = PROJECT_ROOT / "tests" / "integration" / "_p2fix_r5_child.py"

# D3: the child runs under an env WHITELIST (strongest form of stripping —
# unknown host behavior switches cannot leak). The switch names below are
# the documented threat list (kept for the record / report D3 table):
#   DISPATCH_HETERO, AGENT_REGISTRY, WB_ENABLED, R16_TRACE, DSH_HOME,
#   WB_AUDIT_DIR, REDACTION_ENABLED, DISPATCH_BACKEND_OVERRIDES,
#   DISPATCH_CONSENT_TOKEN, DISPATCH_DATA_OPT_IN,
#   DISPATCH_COUNTERS_ALLOW_CORRUPT, PYTHONPATH, PYTHONIOENCODING, PYTHONUTF8
# pinned: PYTHONHASHSEED=0
_CHILD_ENV_ALLOW = ("PATH", "SystemRoot", "SystemDrive", "TEMP", "TMP",
                    "USERPROFILE", "APPDATA", "LOCALAPPDATA")


def _child_env() -> dict:
    env = {}
    for k, v in os.environ.items():
        if k.upper() in {a.upper() for a in _CHILD_ENV_ALLOW}:
            env[k] = v
    env["PYTHONHASHSEED"] = "0"
    return env


def _run_supervisor(params: dict | None = None):
    """Hermetic fixture run (P2-FIX approved isolation): fresh interpreter,
    scrubbed env, PYTHONHASHSEED=0. Returns the child's result dict:
    {status, digest, reviews: [[round, verdict, comments]...],
     arb: [payload...], adv: <count>}.
    Non-default params (R6/R7 on-state semantics — they do not depend on
    env byte-identity) run through the in-process harness."""
    if params:
        return _run_supervisor_inprocess(params)
    proc = subprocess.run(
        [sys.executable, str(CHILD), str(PROJECT_ROOT), BASELINE_DIGEST],
        env=_child_env(), cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace", timeout=300)
    lines = [ln for ln in proc.stdout.splitlines()
             if ln.startswith("CHILD-JSON:")]
    assert lines, (f"child produced no JSON; rc={proc.returncode}\n"
                   f"stdout tail: {proc.stdout[-1500:]}\n"
                   f"stderr tail: {proc.stderr[-1500:]}")
    return json.loads(lines[-1][len("CHILD-JSON:"):])


def _run_supervisor_inprocess(params: dict):
    """F2 discriminator target (NOT used by R5 after the P2-FIX fix):
    the original in-process harness, exposed to whatever the parent
    process environment carries."""
    cleanup_run(RUN_ID_INPROC)  # P3 (B-1): fail-loud, no ignore_errors
    default_llm_script._review_state = {"n": 0}
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    calls = {"adv": 0}

    def script(prompt, json_mode=False):
        if "对抗评审者" in prompt:
            calls["adv"] += 1
            return json.dumps({"critique": "结论过强,缺反例",
                               "converge": "按反例补强后签发"},
                              ensure_ascii=False)
        return default_llm_script(prompt, json_mode)

    final = run_topology(
        "supervisor", "多Agent协同调研任务", run_id=RUN_ID_INPROC, bus=bus,
        llm=FakeLLM(script=script),
        runtime=ScriptedRuntime({**DEFAULT_SCRIPTS,
                                 "synth": ["综合:多Agent适合可分解任务。"]}),
        params=params)
    sys.path.insert(0, str(PROJECT_ROOT / "tests" / "integration"))
    try:
        import _p2fix_r5_child as child_mod
        dig = child_mod.canonical_digest(run_dir(RUN_ID_INPROC) / "trace.jsonl")
    finally:
        cleanup_run(RUN_ID_INPROC)  # P3 (B-1): fail-loud, no ignore_errors
    return {
        "status": final.get("status"),
        "digest": dig,
        "reviews": [[e.payload.get("round"), e.payload.get("verdict"),
                     e.payload.get("comments", "")]
                    for e in seen if e.type == "review"],
        "arb": [e.payload for e in seen if e.type == "arbitration"],
        "adv": calls["adv"],
    }


# --- R5 + K3: off-state byte identity (hermetic subprocess) -------------------

def test_r5_off_state_digest_equals_prechange_baseline():
    out = _run_supervisor()
    assert out["status"] == "done", out["status"]
    assert out["adv"] == 0, "OFF state must make ZERO adversarial LLM calls"
    assert out["digest"] == BASELINE_DIGEST


def test_k3_off_state_arbitration_has_no_source_key():
    out = _run_supervisor()
    arb = out["arb"]
    assert arb, "expected at least the default arbitration event"
    for p in arb:
        assert "source" not in p, f"OFF arbitration must have no source: {p}"
        # observed default shape in this fixture: {key, action, why}
        # (action "none" carries no value; the concurrent-write pick form
        # is {key, action, value, why} — see test_r7)
        assert set(p.keys()) <= {"key", "action", "value", "why"}, p


# --- R6: on-state semantics ---------------------------------------------------

def test_r6_on_state_single_extra_call_and_enhanced_event():
    out = _run_supervisor({"review_adversarial": True})
    assert out["status"] == "done"
    reviews = [[r[0], r[1]] for r in out["reviews"]]
    assert reviews == [[1, "REWORK"], [2, "PASS"]], reviews
    # exactly ONE extra adversarial LLM call for the ONE rework round
    assert out["adv"] == 1, f"expected 1 adversarial call, got {out['adv']}"
    arb = out["arb"]
    enhanced = [p for p in arb if p.get("source") == "enhanced"]
    assert len(enhanced) == 1
    assert enhanced[0]["action"] == "pick"
    assert "反例" in enhanced[0]["why"] and "补强" in enhanced[0]["value"]
    # the critique is merged into the rework round's review_comments
    # (round-1 review event carries the merged text; round 2's comments are
    # the reviewer's own fresh output and overwrite the state afterwards)
    r1 = [r for r in out["reviews"] if r[0] == 1][0]
    assert "对抗收敛" in r1[2]


def test_r6_round_gate_bounds_adversarial_calls():
    # max_review_rounds=1: the round-1 REWORK hits an exhausted gate ->
    # the adversarial pass must NOT run (double-bounded, K3 report item)
    out = _run_supervisor({"review_adversarial": True, "max_review_rounds": 1})
    assert out["adv"] == 0, f"gate exhausted => 0 adversarial calls, got {out['adv']}"


# --- R7: arbitration field shape ----------------------------------------------

def test_r7_arbitration_shape_default_vs_enhanced():
    off = _run_supervisor()
    off_keys = {frozenset(p.keys()) for p in off["arb"]}
    assert off_keys == {frozenset({"key", "action", "why"})}, off_keys

    on = _run_supervisor({"review_adversarial": True})
    on_arb = on["arb"]
    enhanced = [p for p in on_arb if p.get("source") == "enhanced"][0]
    plain = [p for p in on_arb if p.get("source") != "enhanced"]
    assert plain, "default-shape arbitration must be untouched in ON state"
    assert set(plain[0].keys()) == {"key", "action", "why"}
    # canonical default arbitration (concurrent-write pick) is
    # {key, action, value, why}; the enhanced event = that shape + source,
    # i.e. the ONLY added field is "source" (workorder Appendix C)
    assert set(enhanced.keys()) == {"key", "action", "value", "why", "source"}
    assert set(enhanced.keys()) - {"key", "action", "value", "why"} == {"source"}
