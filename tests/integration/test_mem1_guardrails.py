"""R17 MEM1 guardrail criteria (workorder 93fef01e3a1761a1, G1-G5).

Rule 7 placement: lives in tests/integration because it imports `graphs`
at module level (tests/unit is core scope, no upper-layer imports).

G1  off-state (params.guardrails_enabled absent) trace byte-identity for
    BOTH topologies prompt_for serves: supervisor AND debate (B-2-1).
    Digests frozen from the pre-implementation baseline df6b9a3, captured
    twice deterministically (evidence: mem1_evidence/g1_baseline_*.json).
    A prompt-catcher passthrough wrapper is installed during these runs —
    unchanged digests prove the wrapper itself does not alter behavior
    (B-2-2).
G2  on-state: configured agents receive their entries; unconfigured agents
    receive nothing; by_agent override is PER ID (global others still apply,
    B-1-2); block shape is identical for every agent (B-1-3); entry order ==
    yaml declaration order (B-1-4).
G3  data-driven: no guard text inlined in Python source (grep-equivalent);
    mutating the yaml changes the injection.
G4  prompt-only: assignment/review event payloads identical on/off (volatile
    keys stripped); trace event-type set unchanged.
G5  library-side drop piece: "already landed" two-state semantics (MEM1FIX,
    workorder 4dac215981d2cdfa). The piece is a TRANSPORT artifact
    (inbox -> landed/<date>/ -> library 30-tasks/); asserting presence in the
    transient inbox made this test fail forever after lawful landing (ACCT2
    gate red, root cause evidenced). Now: bounded three-root search
    (roots must exist, else hard fail listing them), >=1 hit required
    (none => red), ALL hits must be sha256-identical (mismatch => red with
    every path+hash listed — catches backfill pollution), content asserted
    on the FIRST hit via assert_content(): four sections + redact() residual
    = 0. Stronger than the old single-point existence check.

print/assert messages ASCII-only where feasible (GBK console rule).
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest
import yaml

from core.events import EventBus
from core.llm import FakeLLM
from core.paths import run_dir
from graphs import guardrails
from graphs.builder import RunContext
from graphs.runner import run_topology

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _runs_cleanup import cleanup_run  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SEED_YAML = REPO / "configs" / "guardrails" / "failure_modes.yaml"

# MEM1FIX (workorder 4dac215981d2cdfa, Q1/A-1): two-state "landed" search face.
# Roots are EXPLICIT (root, depth) tuples — no unbounded `**` globs; terminate
# by exact filename match + depth cap. A new archive layout => add one root
# by workorder, termination rule unchanged.
DROP_PIECE_NAME = "2026-10-12-rec-agent错误事件层提议.md"
MAX_DEPTH = 2  # landed/<date>/<file> needs depth 2; others need 1
SEARCH_ROOTS = (
    (Path(r"D:\ai-memory-drop\workbuddy\inbox"), 1),
    (Path(r"D:\ai-memory-drop\landed"), 2),
    (Path(r"D:\ai-memory-skill", "30-tasks"), 1),
)

# Frozen digests. Originally captured at pre-implementation baseline df6b9a3.
# RE-BASELINED twice for M1/W0-1 (rev1: additive fields; rev2: B1/B2 honesty fix —
# executor now reports the real substrate and the judge model moved to
# platform_model). Provenance: docs/baseline_changes.md — the only accepted reason
# to edit these constants.
G1_SUPERVISOR_DIGEST = "a758511df7d9e466bf75c7011ad81333ed4daf82bdbc3b7fa53db5ba8b8698d1"
G1_DEBATE_DIGEST = "61a06c184e6c3a714b91a641bf5e94144e7ec4f450a4fe3aad18a6314d6e4e8a"

STRIP_KEYS = {"ts", "seq", "run_id", "session_id", "op_id"}


# --------------------------------------------------------------- helpers --
def strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items() if k not in STRIP_KEYS}
    if isinstance(obj, list):
        return [strip_volatile(x) for x in obj]
    return obj


def _supervisor_llm():
    def script(prompt, json_mode):
        if "拆解" in prompt:
            return json.dumps({"subtasks": [
                {"subtask_id": "s1", "description": "调研多Agent协同的工程现状",
                 "required_capabilities": ["research"]},
                {"subtask_id": "s2", "description": "对比单Agent与多Agent的收益",
                 "required_capabilities": ["analysis"]},
            ]}, ensure_ascii=False)
        if "审稿人" in prompt:
            return json.dumps({"verdict": "PASS", "comments": "ok"})
        if "并发写" in prompt:
            return json.dumps({"action": "pick", "value": "仲裁后的统一结论",
                               "why": "两版本意图不冲突"})
        return "ok"
    return FakeLLM(script=script)


def _debate_llm():
    def script(prompt, json_mode):
        if "辩论裁判" in prompt:
            return json.dumps({"verdict": "CONCLUDED",
                               "conclusion": "多Agent协同适合可分解任务。"},
                              ensure_ascii=False)
        return "ok"
    return FakeLLM(script=script)


class PromptCatcher:
    """B-2-2 passthrough hook on RunContext.prompt_for: records
    (agent_id, prompt) pairs and returns the ORIGINAL object unmodified
    (identity, not a copy)."""

    def __init__(self, monkeypatch):
        self.records: list[tuple[str, str]] = []
        self._orig = RunContext.prompt_for

        def catcher(ctx, agent_id, **fills):
            text = self._orig(ctx, agent_id, **fills)
            self.records.append((agent_id, text))
            assert self.records[-1][1] is text  # identity: zero alteration
            return text

        monkeypatch.setattr(RunContext, "prompt_for", catcher)

    def prompts_for(self, agent_id):
        return [t for a, t in self.records if a == agent_id]

    def agents_called(self):
        return sorted({a for a, _ in self.records})


def _run(mode, llm, params=None, monkeypatch=None, prefix="mem1"):
    rid = f"run-{prefix}-{uuid.uuid4().hex[:8]}"
    captured = []
    bus = EventBus()
    bus.subscribe(lambda e: captured.append(e))
    catcher = PromptCatcher(monkeypatch) if monkeypatch is not None else None
    final = run_topology(mode, "多Agent协同的工程现状调研",
                         run_id=rid, session_id=f"sess-{prefix}-{uuid.uuid4().hex[:6]}",
                         llm=llm, bus=bus, params=params)
    assert final["final_report"], f"{mode} must produce a final report"
    trace = run_dir(rid) / "trace.jsonl"
    lines = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l]
    cleanup_run(rid)
    return captured, lines, catcher


def _trace_digest(lines):
    norm = strip_volatile(lines)
    blob = json.dumps(norm, ensure_ascii=False, sort_keys=False).encode("utf-8")
    import hashlib
    return hashlib.sha256(blob).hexdigest()


def _canonicalise_events(events):
    """P2FIX2 canonical caliber (workorder-sanctioned): the supervisor
    fixture has a genuinely concurrent fan-out (R5-family write-order race;
    1/6 minority draw observed in evidence), so its digest is computed over
    the canonicalised event order. _p2fix_r5_child reads sys.argv at module
    level — guard/restore around first import. Debate is serial-stable
    (6/6 identical, evidence race_check_debate) and keeps bare-byte order.
    """
    saved = list(sys.argv)
    sys.argv = [saved[0] if saved else "pytest", str(REPO)]
    try:
        from _p2fix_r5_child import canonicalise  # noqa: E402
    finally:
        sys.argv = saved
    return canonicalise(events, checks=True)


# ------------------------------------------------------------------- G1 --
def test_g1_off_state_byte_identical_supervisor_and_debate(monkeypatch):
    assert guardrails.prefix_for("researcher", {}) == ""  # default off
    for mode, llm, frozen in (("supervisor", _supervisor_llm(), G1_SUPERVISOR_DIGEST),
                              ("debate", _debate_llm(), G1_DEBATE_DIGEST)):
        _captured, lines, _c = _run(mode, llm, params=None, monkeypatch=monkeypatch,
                                    prefix=f"g1-{mode}")
        assert lines[-1]["type"] == "run_finished"
        if mode == "supervisor":       # concurrent fixture: P2FIX2 canonical
            norm = strip_volatile(_canonicalise_events(lines))
        else:                          # serial fixture: bare-byte order
            norm = strip_volatile(lines)
        blob = json.dumps(norm, ensure_ascii=False, sort_keys=False).encode("utf-8")
        import hashlib
        assert hashlib.sha256(blob).hexdigest() == frozen, \
            f"off-state digest drift for {mode}"


# ------------------------------------------------------------------- G2 --
def test_g2_on_state_injects_to_every_agent_with_identical_block(monkeypatch):
    params = {"guardrails_enabled": True}
    seed_ids = _seed_ids_in_order()
    _captured, _lines, catcher = _run("supervisor", _supervisor_llm(), params=params,
                                      monkeypatch=monkeypatch, prefix="g2-on")
    assert catcher.agents_called(), "wrapper must have captured prompts"
    blocks = set()
    for agent in catcher.agents_called():
        prompts = catcher.prompts_for(agent)
        assert prompts, f"agent {agent} called but no prompt captured"
        expected_block = guardrails.prefix_for(agent, params)
        assert expected_block, "seed is global-only: every agent must get a block"
        for p in prompts:
            assert p.startswith(expected_block), f"block shape mismatch for {agent}"
            for fid in seed_ids:
                assert fid in expected_block
        blocks.add(expected_block)
    # B-1-3: with the all-global seed the block is IDENTICAL for all agents
    assert len(blocks) == 1, "same configuration must render the same block"


def _seed_ids_in_order():
    data = yaml.safe_load(SEED_YAML.read_text(encoding="utf-8"))
    return [str(e["id"]) for e in data["global"]]


def test_g2_by_agent_override_is_per_id_and_global_others_apply(monkeypatch, tmp_path):
    # fixture yaml: global A(+orig text) + B; by_agent.researcher overrides A
    # with DIFFERENT text and adds C (by_agent-only)
    fix = tmp_path / "failure_modes.yaml"
    fix.write_text(
        "global:\n"
        "  - id: FM-GTEST-A\n"
        "    symptom: 全局A原始症状标记ORIGA\n"
        "    guard: 全局A原始护栏ORIGA\n"
        "    judge_hint: 全局A原始提示ORIGA\n"
        "  - id: FM-GTEST-B\n"
        "    symptom: 全局B症状MARKB\n"
        "    guard: 全局B护栏MARKB\n"
        "    judge_hint: 全局B提示MARKB\n"
        "by_agent:\n"
        "  researcher:\n"
        "    - id: FM-GTEST-A\n"
        "      symptom: 研究员覆盖A症状OVERA\n"
        "      guard: 研究员覆盖A护栏OVERA\n"
        "      judge_hint: 研究员覆盖A提示OVERA\n"
        "    - id: FM-GTEST-C\n"
        "      symptom: 研究员独有C症状ONLYC\n"
        "      guard: 研究员独有C护栏ONLYC\n"
        "      judge_hint: 研究员独有C提示ONLYC\n",
        encoding="utf-8")
    params = {"guardrails_enabled": True, "guardrails_path": str(fix)}
    _captured, _lines, catcher = _run("supervisor", _supervisor_llm(), params=params,
                                      monkeypatch=monkeypatch, prefix="g2-ovr")
    # per-id override: researcher sees override text + B (global other) + C
    for p in catcher.prompts_for("researcher"):
        assert "OVERA" in p and "ORIGA" not in p, "same id must take by_agent version"
        assert "MARKB" in p, "B-1-2: other global ids must still apply"
        assert "ONLYC" in p, "by_agent-only id must be appended"
    # analyst not in by_agent: original global A text + B, no override, no C
    for p in catcher.prompts_for("analyst"):
        assert "ORIGA" in p and "OVERA" not in p
        assert "MARKB" in p and "ONLYC" not in p


def test_g2_unconfigured_agents_receive_nothing(monkeypatch, tmp_path):
    fix = tmp_path / "failure_modes.yaml"
    fix.write_text(
        "global: []\n"
        "by_agent:\n"
        "  researcher:\n"
        "    - id: FM-GTEST-ONLY-R\n"
        "      symptom: 仅研究员可见SYMPTOMR\n"
        "      guard: 仅研究员可见GUARDR\n"
        "      judge_hint: 仅研究员可见HINTR\n",
        encoding="utf-8")
    params = {"guardrails_enabled": True, "guardrails_path": str(fix)}
    _captured, _lines, catcher = _run("supervisor", _supervisor_llm(), params=params,
                                      monkeypatch=monkeypatch, prefix="g2-neg")
    assert "researcher" in catcher.agents_called()
    for p in catcher.prompts_for("researcher"):
        assert "FM-GTEST-ONLY-R" in p
    for agent in catcher.agents_called():
        if agent == "researcher":
            continue
        for p in catcher.prompts_for(agent):
            assert "FM-" not in p, f"unconfigured agent {agent} must get no block"


# ------------------------------------------------------------------- G3 --
def test_g3_no_guard_text_inlined_in_python_source():
    data = yaml.safe_load(SEED_YAML.read_text(encoding="utf-8"))
    texts = []
    for e in data["global"]:
        for f in ("symptom", "guard", "judge_hint", "source"):
            texts.append(str(e.get(f, "")))
    sources = [(REPO / "graphs" / "guardrails.py").read_text(encoding="utf-8"),
               (REPO / "graphs" / "builder.py").read_text(encoding="utf-8")]
    for t in texts:
        if not t:
            continue
        for src_name, src in (("guardrails.py", sources[0]),
                              ("builder.py", sources[1])):
            assert t not in src, f"guard text inlined in {src_name}: {t[:30]}"


def test_g3_yaml_mutation_changes_injection(monkeypatch, tmp_path):
    data = yaml.safe_load(SEED_YAML.read_text(encoding="utf-8"))
    old_guard = str(data["global"][0]["guard"])
    data["global"][0]["guard"] = "变异后的新护栏文本MUTATED-GUARD"
    fix = tmp_path / "failure_modes.yaml"
    fix.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    params = {"guardrails_enabled": True, "guardrails_path": str(fix)}
    _captured, _lines, catcher = _run("supervisor", _supervisor_llm(), params=params,
                                      monkeypatch=monkeypatch, prefix="g3-mut")
    for agent in ("researcher", "analyst"):
        prompts = catcher.prompts_for(agent)
        assert prompts
        assert any("MUTATED-GUARD" in p for p in prompts), "mutated text must flow"
        assert all(old_guard not in p for p in prompts), "old text must be gone"


# ------------------------------------------------------------------- G4 --
def test_g4_assignment_review_payloads_identical_on_off(monkeypatch):
    def payloads(mode, params, tag):
        captured, lines, _c = _run(mode, _supervisor_llm(), params=params,
                                   monkeypatch=monkeypatch, prefix=f"g4-{tag}")
        kinds = {e["type"] for e in lines}
        sel = [strip_volatile(e) for e in lines
               if e["type"] in ("assignment", "review")]
        return kinds, sel

    off_kinds, off_sel = payloads("supervisor", None, "off")
    on_kinds, on_sel = payloads("supervisor", {"guardrails_enabled": True}, "on")
    assert off_kinds == on_kinds, "no new event types may appear"
    assert off_sel == on_sel, "assignment/review payloads must be identical"


# ------------------------------------------------------------------- G5 --
def _sha16_file(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _find_drop_piece():
    """Q1/A-1 two-state search: inbox -> landed/** (date-desc, explicit) ->
    library 30-tasks/. Returns (hits, missing_roots).
    Termination: explicit root list + per-root depth cap (MAX_DEPTH) + exact
    filename match; NO unbounded `**` globs."""
    hits, missing_roots = [], []
    for root, depth in SEARCH_ROOTS:
        assert depth <= MAX_DEPTH
        if not root.exists():
            missing_roots.append(str(root))
            continue
        if root.name == "landed":
            date_dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                               key=lambda p: p.name, reverse=True)
            for d in date_dirs:
                f = d / DROP_PIECE_NAME
                if f.is_file():
                    hits.append(f)
        else:
            f = root / DROP_PIECE_NAME
            if f.is_file():
                hits.append(f)
    return hits, missing_roots


def assert_content(path):
    """Q2/A-2 content assertions, kept in ONE helper so the discriminator can
    remove exactly this call. Four sections + redact() residual = 0."""
    from core.redact import redact

    text = path.read_text(encoding="utf-8")
    for section in ("事件 schema", "归因枚举", "晋升门槛", "治理红线"):
        assert section in text, f"missing section: {section}"
    masked, counts = redact(text)
    assert counts == {}, f"redact residual must be 0, got {counts}"
    assert masked == text  # nothing to mask == file carries no raw PII/secret


def test_g5_drop_piece_compliant_and_redact_residual_zero():
    hits, missing_roots = _find_drop_piece()
    # A-1: any missing search root => hard fail LISTING the roots (the red
    # must have an accurate cause; a vanished root must not masquerade as
    # "no hit").
    assert not missing_roots, f"search roots missing: {missing_roots}"
    # Q1 two-state: the piece must exist in at least one landing state;
    # absent everywhere => red (a real loss is still caught).
    assert hits, ("drop piece not found in any landing state "
                  f"(inbox / landed/** / 30-tasks): {DROP_PIECE_NAME}")
    # A-2: ALL hits must be byte-identical (sha256); a mismatch is backfill
    # pollution => red, message lists every path + its sha256.
    shas = {str(p): _sha16_file(p) for p in hits}
    assert len(set(shas.values())) == 1, (
        f"multi-hit sha256 mismatch (backfill pollution?): {shas}")
    # Q2 content NOT downgraded: full original content assertions run on the
    # FIRST hit (stable: roots fixed, landed sorted date-desc).
    assert_content(hits[0])
