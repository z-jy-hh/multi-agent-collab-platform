"""G5 gate / 判据 12a: the evolution mechanism works end-to-end offline.

- golden set runs produce Metrics;
- analyzer + optimizer produce a candidate with a diff — and in propose mode
  NOTHING is applied (live file hash unchanged);
- guard.py refuses code files and invalid YAML (code-level whitelist, C8);
- version snapshot → restore restores byte-identical content (hash proof).
"""

import hashlib
import json

import pytest

from core.errors import GuardViolation
from core.paths import PROJECT_ROOT, prompts_dir
from evolver.analyzer import Weakness, analyze
from evolver.golden import run_golden
from evolver.guard import apply_candidate, validate_candidate
from evolver.optimizer import propose_changes
from evolver.versions import Metrics, VersionStore, decide


def test_golden_runs_produce_metrics():
    metrics = run_golden(K=1, run_prefix="g5-golden")
    assert isinstance(metrics, Metrics)
    assert 0.0 <= metrics.quality <= 100.0


def test_propose_mode_never_touches_live_files():
    writer = PROJECT_ROOT / "prompts" / "worker" / "writer.v1.md"
    before = hashlib.sha256(writer.read_bytes()).hexdigest()

    weaknesses = analyze([], events_by_run={
        "r1": [_FakeEvent({"verdict": "REWORK", "comments": "太啰嗦"})],
    })
    assert any(w.kind == "rework" for w in weaknesses)
    candidates = propose_changes(weaknesses, PROJECT_ROOT)
    assert candidates and candidates[0].file == "prompts/worker/writer.v1.md"
    assert "@@" in candidates[0].diff  # a real unified diff

    # propose mode: validate + snapshot the PROPOSAL, do not write live file
    for cand in candidates:
        validate_candidate(cand.file, cand.new_content)  # would pass when applied
    after = hashlib.sha256(writer.read_bytes()).hexdigest()
    assert before == after, "propose mode must not modify live prompts"


def test_guard_refuses_code_and_bad_yaml(tmp_path):
    with pytest.raises(GuardViolation):
        validate_candidate("core/matcher.py", "print('pwned')")  # code file
    with pytest.raises(GuardViolation):
        validate_candidate("tests/unit/test_x.py", "x")  # tests are code too
    with pytest.raises(GuardViolation):
        validate_candidate("configs/agents.yaml",
                           "agents:\n  - agent_id: x\n    strength: {research: 99}\n"
                           "    cost: low\n")  # strength out of range
    with pytest.raises(GuardViolation):
        validate_candidate("configs/agents.yaml",
                           "agents:\n  - agent_id: x\n    strength: {research: 5}\n"
                           "    cost: turbo\n")  # cost not in enum
    with pytest.raises(GuardViolation):
        validate_candidate("../../outside.md", "x")  # escapes project root


def test_snapshot_restore_roundtrip_is_byte_identical(tmp_path):
    store = VersionStore(tmp_path / "versions")
    src = tmp_path / "live" / "prompts" / "worker" / "writer.v1.md"
    src.parent.mkdir(parents=True)
    src.write_text("v1 content\n", encoding="utf-8")
    hashes = store.snapshot(1, {"prompts/worker/writer.v1.md": src})

    src.write_text("v2 mutated\n", encoding="utf-8")  # a bad candidate got applied
    restored = store.restore(1, tmp_path / "live")
    assert src.read_text(encoding="utf-8") == "v1 content\n"
    assert restored == hashes  # byte-level restore proven by hash (U5)


def test_save_record_persists_nonempty_diff(tmp_path):
    """Rectification C1: the diff must land in record.json (never empty)."""
    import json

    store = VersionStore(tmp_path / "versions")
    src = tmp_path / "live" / "prompts" / "worker" / "writer.v1.md"
    src.parent.mkdir(parents=True)
    src.write_text("v1 content\n", encoding="utf-8")
    hashes = store.snapshot(1, {"prompts/worker/writer.v1.md": src})
    diff = "".join(__import__("difflib").unified_diff(
        ["v1 content\n"], ["v1 content\nappended\n"],
        fromfile="a/prompts/worker/writer.v1.md", tofile="b/prompts/worker/writer.v1.md"))
    store.save_record(1, "refine prompts/worker/writer.v1.md due to: rework",
                      "KEEP", 0.01, hashes, diff=diff)
    rec = json.loads((store.base / "v001" / "record.json").read_text(encoding="utf-8"))
    assert rec["diff"] and "@@" in rec["diff"]


def test_decide_boundary_rollback():
    base = Metrics(quality=80, cost=500, latency_s=1.0)
    same = Metrics(quality=80, cost=500, latency_s=1.0)  # deterministic FakeLLM → delta 0
    assert decide(base, same, threshold=0.01) == "ROLLBACK"


class _FakeEvent:
    def __init__(self, payload):
        self.type = "review"
        self.payload = payload
