"""Rectification C2: backfill replay must be byte-exact.

- synthetic: snapshot + rule → candidate appends the refinement exactly once;
- real: v003 replay anchor sha256_16 == a5ad2f34a30508f8 (auditor-verified)
  and the replayed candidate equals the live KEEP file;
- backfill is idempotent and only ADDS the diff key (K4: existing fields
  pass through untouched; no empty-string diffs).
"""

import hashlib
import json

from core.paths import PROJECT_ROOT, prompt_versions_dir
from evolver.backfill_diffs import backfill, parse_kinds, replay_candidate, replay_candidate_sha16
from evolver.optimizer import RULES

V003_ANCHOR = "a5ad2f34a30508f8"


def test_parse_kinds_keeps_repeats_in_order():
    assert parse_kinds("refine configs/agents.yaml due to: match, match, match") == [
        "match", "match", "match"]
    assert parse_kinds("no marker here") == []


def test_replay_appends_rule_exactly_once():
    snap = "line1\n"
    candidate = replay_candidate(snap, ["rework", "rework"])
    assert candidate == snap + RULES["rework"][1]  # per-file merge: ONE append


def test_replay_unknown_kind_is_identity():
    assert replay_candidate("x\n", ["no-such-kind"]) == "x\n"


def test_real_v003_anchor_frozen_fixture():
    """Rectification re-review C2: the anchor assertion is now FROZEN — it
    reads tests/fixtures/v003_snapshot.md + v003_candidate.md and never the
    live prompts file, so a future legitimate KEEP cannot turn it red.
    Fixtures were frozen byte-exact from the real v003 record
    (snapshot sha16 65001572f6057832 → candidate sha16 a5ad2f34a30508f8)."""
    fixtures = PROJECT_ROOT / "tests" / "fixtures"
    snapshot = (fixtures / "v003_snapshot.md").read_text(encoding="utf-8")
    candidate = (fixtures / "v003_candidate.md").read_text(encoding="utf-8")

    replayed = replay_candidate(snapshot, ["rework"])
    assert replayed == candidate
    assert hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16] == V003_ANCHOR


def test_real_v003_reverse_discrimination():
    """Rectification re-review C2 reverse proof: the frozen assertion has
    discriminating power — a wrong rule, no rule, or a tampered candidate
    must NOT reproduce the frozen candidate."""
    fixtures = PROJECT_ROOT / "tests" / "fixtures"
    snapshot = (fixtures / "v003_snapshot.md").read_text(encoding="utf-8")
    candidate = (fixtures / "v003_candidate.md").read_text(encoding="utf-8")

    assert replay_candidate(snapshot, ["match"]) != candidate   # wrong rule
    assert replay_candidate(snapshot, ["schema"]) != candidate  # wrong rule
    assert replay_candidate(snapshot, []) != candidate          # rule dropped
    wrong_candidate = candidate.replace("三段", "两段")
    assert replay_candidate(snapshot, ["rework"]) != wrong_candidate  # tamper caught


def test_real_v003_record_diff_still_present():
    """Read-only regression: the HISTORICAL v003 record keeps its non-empty
    diff regardless of what happens to live files (record.json is immutable
    evidence, not live state)."""
    record = json.loads(
        (prompt_versions_dir() / "v003" / "record.json").read_text(encoding="utf-8"))
    assert record["diff"] and "@@" in record["diff"]


def test_backfill_idempotent_only_adds_diff(tmp_path):
    vdir = tmp_path / "v001"
    (vdir / "prompts" / "worker").mkdir(parents=True)
    (vdir / "prompts" / "worker" / "writer.v1.md").write_text("base\n", encoding="utf-8")
    (vdir / "record.json").write_text(json.dumps({
        "version": 1, "reason": "refine prompts/worker/writer.v1.md due to: rework",
        "decision": "ROLLBACK", "score_delta": 0.0, "created_at": 0.0,
        "files": {"prompts/worker/writer.v1.md": "anchormaybe"},
    }), encoding="utf-8")

    first = backfill(versions_dir=tmp_path, project_root=tmp_path)
    assert first["updated"] == ["v001"]
    rec = json.loads((vdir / "record.json").read_text(encoding="utf-8"))
    assert rec["diff"] and "@@" in rec["diff"]
    # pre-existing fields untouched
    assert rec["version"] == 1 and rec["decision"] == "ROLLBACK" and rec["score_delta"] == 0.0

    second = backfill(versions_dir=tmp_path, project_root=tmp_path)
    assert second["skipped"] == ["v001"] and second["updated"] == []  # idempotent


def test_backfill_unreplayable_gets_null_not_empty(tmp_path):
    vdir = tmp_path / "v001"
    (vdir / "prompts" / "worker").mkdir(parents=True)
    (vdir / "prompts" / "worker" / "writer.v1.md").write_text("base\n", encoding="utf-8")
    (vdir / "record.json").write_text(json.dumps({
        "version": 1, "reason": "no kind marker", "decision": "ROLLBACK",
        "score_delta": 0.0, "created_at": 0.0,
        "files": {"prompts/worker/writer.v1.md": "h"},
    }), encoding="utf-8")
    backfill(versions_dir=tmp_path, project_root=tmp_path)
    rec = json.loads((vdir / "record.json").read_text(encoding="utf-8"))
    assert rec["diff"] is None and "diff_unavailable_reason" in rec  # K4
