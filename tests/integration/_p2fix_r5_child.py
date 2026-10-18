"""P2-FIX2: subprocess child for R5's hermetic supervisor fixture run.

Isolation (P2-FIX, Appendix D, kept): the PARENT (test_p2_adversarial.py)
invokes this file in a FRESH interpreter with an env WHITELIST so the
digest cannot drift with host behavior switches (DISPATCH_HETERO=1 etc.
was empirically shown to add hetero_dispatch_* events; the whitelist also
keeps the ~60s hetero dispatch pre-pass OUT of the child - F2: that cost
only exists in env-carrying in-process runs).

R5 CRITERION LINE (workorder sha256_16 0f37e591032154c9, Appendix F1 C1):
the digest asserts MOD-FANOUT-SCHEDULING EQUIVALENCE (模扇出调度等价),
**NOT byte-identity**. LangGraph runs the supervisor's Send fan-out
(researcher/analyst) on separate threads, so the ORDER of events inside
the fan-out window is a thread-scheduling draw, not semantics; the
baseline trace froze ONE such draw. The canonicalisation absorbs ONLY the
ordering of events inside worker fan-out windows; everything outside -
plan, assignments, arbitration, review/rework, health - keeps trace order
byte-for-byte (C2 assert below, runs every execution).

Canonicalisation (slot-stable-sort, fixed-point on the frozen constant):
  window members = blackboard_write | agent_output | agent_card
                   | node_enter(payload.node == "worker")
  slot keys: node_enter #i -> 2(i-1); agent_card (rank j, agent_id DESC)
             -> 2j+1; blackboard_write (rank j) -> 4+2j;
             agent_output (rank j) -> 5+2j.
  The baseline window [enter, card_r, enter, card_a, write_r, out_r,
  write_a, out_a] maps to itself (identity/fixed point), and every one of
  the 8!/(2!) window arrangements maps back to the baseline digest
  (measured 0/40320 escapes; all 13 captured evidence traces absorbed -
  probe_upgrade_out.txt / f0_absorption_table.txt).
Loud failure (C3): unknown agent / card-enter count mismatch / multi-agent
sequential member run / a non-member event between two members of a window
span -> AssertionError (never silent reorder or drop).
Invariants (C4, asserted every run): count conservation, multiset
bijection, idempotence.
Slot-table maintenance point (C7): a new event type inside the fan-out
window, or a third agent, requires updating the slot table here.

History: the P2-FIX "gate" (poll <=60s keyed on prompt text) was DEAD CODE
(workers run through ctx.runtime.execute, never the LLM; probe evidence)
and was removed. A narrow write/output-run sort (b1a7731) absorbed only
the write-interleave family and was upgraded per Appendix F1.

Protocol: `python _p2fix_r5_child.py <repo_root> [expected_digest]` -> prints
ONE JSON line:
  {"status": ..., "digest": <sha256 hex>, "reviews": [[round, verdict,
   comments], ...], "arb": [payload, ...], "adv": <adversarial llm calls>}
If expected_digest is given and the computed digest differs, the trace and
result JSON are QUARANTINED (D:\WorkBuddy\p2fix2_evidence\quarantine\<ts>\)
before the run dir is removed - a digest mismatch is evidence and must not
be destroyed by the cleanup.
ASCII-only prints (GBK console rule).
"""
import hashlib
import json
import shutil
import sys

REPO = sys.argv[1]
sys.path.insert(0, REPO)
EXPECTED = sys.argv[2] if len(sys.argv) > 2 else None

from core.events import EventBus                      # noqa: E402
from core.llm import FakeLLM                          # noqa: E402
from core.paths import run_dir                        # noqa: E402
from core.runtime import ScriptedRuntime              # noqa: E402
from graphs.runner import (DEFAULT_SCRIPTS,           # noqa: E402
                           default_llm_script, run_topology)

RUN_ID = "fixture-p2-fixed"
STRIP_KEYS = {"ts", "seq", "run_id", "session_id", "created_at", "finished_at"}
MEMBER_TYPES = {"blackboard_write", "agent_output", "agent_card"}


def strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items()
                if k not in STRIP_KEYS}
    if isinstance(obj, list):
        return [strip_volatile(x) for x in obj]
    return obj


def _is_window_member(e):
    t = e.get("type")
    if t in MEMBER_TYPES:
        return True
    if t == "node_enter":
        return (e.get("payload") or {}).get("node") == "worker"
    return False


def _rank(e, rank):
    a = e.get("agent_id")
    if a not in rank:                     # C3: unknown agent -> loud
        raise AssertionError(
            "fanout window: event from agent %r not in card set %r"
            % (a, sorted(rank)))
    return rank[a]


def _window_key(e, rank, enter_slot):
    t = e.get("type")
    if t == "node_enter":
        k = 2 * enter_slot[0]
        enter_slot[0] += 1
        return k
    if t == "agent_card":
        return 2 * _rank(e, rank) + 1
    if t == "blackboard_write":
        return 4 + 2 * _rank(e, rank)
    return 5 + 2 * _rank(e, rank)


def _check_window(run):
    """C3: unknown inputs inside the window must fail LOUDLY (F1 C3).

    Legal window shapes in this fixture:
      fan-out window (has node_enter(worker)): #enters == #agent_cards and
        every write/output/card agent is in the card set;
      sequential member run (no worker enter, e.g. the summarize node's
        [blackboard_write, agent_output]): all member agents must be ONE
        agent (a concurrent write surface without worker enters would be a
        topology change -> loud, not silently reordered).
    """
    cards = {e.get("agent_id") for e in run if e.get("type") == "agent_card"}
    enters = sum(1 for e in run if e.get("type") == "node_enter")
    if enters == 0 and not cards:
        agents = {e.get("agent_id") for e in run
                  if e.get("type") in ("blackboard_write", "agent_output")}
        if len(agents) > 1:
            raise AssertionError(
                "sequential member run with multiple agents %r (topology "
                "change?)" % sorted(agents))
        return
    if enters != len(cards):
        raise AssertionError("fanout window: node_enter(worker) count %d "
                             "!= agent_card count %d" % (enters, len(cards)))
    for e in run:
        if e.get("type") in ("blackboard_write", "agent_output", "agent_card"):
            if e.get("agent_id") not in cards:
                raise AssertionError(
                    "fanout window: unknown agent %r (cards %r)"
                    % (e.get("agent_id"), sorted(cards)))


def canonicalise(events, checks=True):
    """MOD-FANOUT-SCHEDULING EQUIVALENCE - see module docstring (C1)."""
    out = []
    i, n = 0, len(events)
    while i < n:
        if _is_window_member(events[i]):
            j = i + 1
            while j < n and _is_window_member(events[j]):
                j += 1
            run = events[i:j]
            if checks:
                _check_window(run)
            cards = {e.get("agent_id") for e in run
                     if e.get("type") == "agent_card"}
            if cards:
                rank = {a: k for k, a in enumerate(sorted(cards, reverse=True))}
            else:                      # sequential single-agent member run
                agents = sorted({e.get("agent_id") for e in run
                                 if e.get("agent_id") is not None})
                rank = {a: k for k, a in enumerate(agents)}
            enter_slot = [0]
            out.extend(sorted(run, key=lambda e: _window_key(e, rank, enter_slot)))
            i = j
        else:
            out.append(events[i])
            i += 1
    if not checks:
        return out
    # C4: bijection / count conservation
    assert len(out) == len(events), "canonicalisation changed event count"
    before = sorted(json.dumps(strip_volatile(e), sort_keys=True,
                               ensure_ascii=False) for e in events)
    after = sorted(json.dumps(strip_volatile(e), sort_keys=True,
                              ensure_ascii=False) for e in out)
    assert before == after, "canonicalisation is not a bijection on events"
    # C4: idempotence
    twice = canonicalise(out, checks=False)
    assert [json.dumps(strip_volatile(e), sort_keys=True, ensure_ascii=False)
            for e in out] == \
           [json.dumps(strip_volatile(e), sort_keys=True, ensure_ascii=False)
            for e in twice], "canonicalisation is not idempotent"
    # C2: everything OUTSIDE window spans is byte-identical (raw JSON)
    outs_b = [json.dumps(e, sort_keys=True, ensure_ascii=False)
              for e in events if not _is_window_member(e)]
    outs_a = [json.dumps(e, sort_keys=True, ensure_ascii=False)
              for e in out if not _is_window_member(e)]
    assert outs_b == outs_a, "canonicalisation touched outside-window events"
    return out


def canonical_digest(path):
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                events.append(json.loads(line))
    lines = [json.dumps(strip_volatile(e), sort_keys=True, ensure_ascii=False)
             for e in canonicalise(events)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def main():
    shutil.rmtree(run_dir(RUN_ID), ignore_errors=True)
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
        "supervisor", "多Agent协同调研任务", run_id=RUN_ID, bus=bus,
        llm=FakeLLM(script=script),
        runtime=ScriptedRuntime({**DEFAULT_SCRIPTS,
                                 "synth": ["综合:多Agent适合可分解任务。"]}),
        params={})
    digest = canonical_digest(run_dir(RUN_ID) / "trace.jsonl")
    # P2FIX2 debug: keep the last child trace for offline diffing
    try:
        dbg = r"D:\WorkBuddy\p2fix2_evidence\last_child_trace.jsonl"
        shutil.copyfile(run_dir(RUN_ID) / "trace.jsonl", dbg)
    except Exception:
        pass
    if EXPECTED and digest != EXPECTED:
        # digest mismatch = EVIDENCE: quarantine the trace + result JSON
        # BEFORE the run dir is removed (mismatch traces used to be lost
        # when a later child run overwrote the debug copy).
        try:
            import time as _t
            qdir = (r"D:\WorkBuddy\p2fix2_evidence\quarantine"
                    + "\\" + _t.strftime("%Y%m%dT%H%M%S"))
            import os as _os
            _os.makedirs(qdir, exist_ok=True)
            shutil.copyfile(run_dir(RUN_ID) / "trace.jsonl",
                            _os.path.join(qdir, "trace.jsonl"))
            with open(_os.path.join(qdir, "child.json"), "w",
                      encoding="utf-8") as _fh:
                _fh.write(json.dumps({"digest": digest, "expected": EXPECTED},
                                     ensure_ascii=False, indent=1))
        except Exception:
            pass
    shutil.rmtree(run_dir(RUN_ID), ignore_errors=True)
    out = {
        "status": final.get("status"),
        "digest": digest,
        "reviews": [[e.payload.get("round"), e.payload.get("verdict"),
                     e.payload.get("comments", "")]
                    for e in seen if e.type == "review"],
        "arb": [e.payload for e in seen if e.type == "arbitration"],
        "adv": calls["adv"],
    }
    print("CHILD-JSON:" + json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
