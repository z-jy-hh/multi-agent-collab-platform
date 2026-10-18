"""N2 integration: the REAL pipeline (run_topology) with redaction on.

R1 positive: a task seeded with SYNTHETIC secrets must land on disk only in
mask form (trace/health/state), with redaction.json counts and the
redaction_rules audit field. R3: rules version in all three markers
(n2-v1 -> n2-v2 since N1-FIX1). R5 (OFF):
REDACTION_ENABLED=0 must land plaintext and the scanner must flag it --
executed in a REDIRECTED tmp runs root so the real runs/ stay clean.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.redact import RULES_VERSION, _RULES, _mask_spans
from core.paths import run_dir as real_run_dir

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _runs_cleanup import cleanup_run  # noqa: E402  (P3/B-1 shared fail-loud helper)

TASK = ("联系 13812345678，邮箱 zhangsan@example.com，"
        "key sk-abcdef1234567890ghij，身份证 110101199001011234")
SECRETS = ["13812345678", "zhangsan@example.com",
           "sk-abcdef1234567890ghij", "110101199001011234"]


def _unmasked_hits(text: str) -> dict:
    spans = _mask_spans(text)
    out = {}
    for kind, rx in _RULES:
        n = 0
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < pe and ps < e for ps, pe in spans):
                continue
            n += 1
        if n:
            out[kind] = n
    return out


def _run(real: bool, tmp_path, monkeypatch, run_id):
    from core.events import EventBus
    from core.llm import FakeLLM
    from graphs.runner import default_llm_script, run_topology
    if not real:
        # redirect ALL artifact writers into tmp (plaintext OFF-state run):
        # runner AND inspector each hold their own module-level run_dir
        monkeypatch.setattr("graphs.runner.run_dir",
                            lambda rid: tmp_path / "runs" / rid)
        monkeypatch.setattr("inspector.report.run_dir",
                            lambda rid: tmp_path / "runs" / rid)
    run_topology("single", TASK, run_id=run_id,
                 session_id=f"n2-{run_id[:8]}", bus=EventBus(),
                 persistent=real, llm=FakeLLM(script=default_llm_script))
    if real:
        return real_run_dir(run_id)
    return tmp_path / "runs" / run_id


def test_r1_positive_real_pipeline_masks_everything():
    import uuid
    rid = f"n2-r1-{uuid.uuid4().hex[:8]}"  # FLAKY-1 lesson: never reuse ids
    rd = _run(real=True, tmp_path=None, monkeypatch=None, run_id=rid)
    unmasked_total = {}
    for fname in ("trace.jsonl", "health.json", "state.json"):
        text = (rd / fname).read_text(encoding="utf-8")
        hits = _unmasked_hits(text)
        for k, v in hits.items():
            unmasked_total[k] = unmasked_total.get(k, 0) + v
        for secret in SECRETS:
            assert secret not in text, f"{fname} leaked a planted secret"
    assert unmasked_total == {}, f"unmasked forms survived: {unmasked_total}"
    # the mask form with the exact reconcilable fingerprint is present
    from core.redact import _fingerprint
    expected_phone_mask = f"⟨REDACTED:phone_cn:{_fingerprint('13812345678')}⟩"
    assert expected_phone_mask in (rd / "state.json").read_text(encoding="utf-8")
    marker = json.loads((rd / "redaction.json").read_text(encoding="utf-8"))
    assert marker["rules_version"] == RULES_VERSION
    for kind in ("phone_cn", "openai_sk", "idcard_cn", "email"):
        assert marker["counts_by_kind"].get(kind, 0) >= 1, kind
    # R3: rules version inside health.json
    health = json.loads((rd / "health.json").read_text(encoding="utf-8"))
    assert health.get("redaction_rules") == RULES_VERSION
    # N2-FIX1 (review F1): the runs REGISTRY row must carry the masked task
    # only + the rules marker (persistent=True path, _record_run)
    import sqlite3
    from core.paths import storage_path
    conn = sqlite3.connect(str(storage_path()))
    try:
        row = conn.execute(
            "SELECT task, redaction_rules FROM runs WHERE run_id = ?",
            (rid,)).fetchone()
    finally:
        conn.close()
    assert row is not None, "run must be registered (persistent=True)"
    stored_task, marker = row
    assert _unmasked_hits(stored_task) == {}, stored_task
    for secret in SECRETS:
        assert secret not in stored_task
    assert marker == RULES_VERSION


def test_r3_run_meta_carries_rules_version(tmp_path):
    from api.routes import _write_run_meta
    rd = real_run_dir("n2-r3-meta")
    cleanup_run("n2-r3-meta")  # P3 (B-1): pre-clean stale residue (loud)
    rd.mkdir(parents=True, exist_ok=True)
    try:
        _write_run_meta(rd, {"run_id": "n2-r3-meta", "source_trust": "human",
                             "independent_review": None})
        meta = json.loads((rd / "run_meta.json").read_text(encoding="utf-8"))
        assert meta["redaction_rules"] == RULES_VERSION
        assert "derived_from" in meta  # trust channel field (decision card §2)
        assert meta["source_trust"] == "human"
    finally:
        cleanup_run("n2-r3-meta")  # P3 (B-1): fail-loud, no silent leak


def test_r5_off_lands_plaintext_and_scanner_flags_it(tmp_path, monkeypatch):
    import uuid
    monkeypatch.setenv("REDACTION_ENABLED", "0")
    rd = _run(real=False, tmp_path=tmp_path, monkeypatch=monkeypatch,
              run_id=f"n2-r5-{uuid.uuid4().hex[:8]}")
    state_text = (rd / "state.json").read_text(encoding="utf-8")
    leaked = [s for s in SECRETS if s in state_text]
    assert leaked, "OFF state must land plaintext (this is the expected red)"
    # the scanner must flag the plaintext run dir (fixture root, real runs/
    # are untouched)
    import importlib.util
    import sys
    from pathlib import Path as _P
    tool = (_P(__file__).parents[2] / "tests" / "tools"
            / "audit_invariants.py")
    d = str(tool.parent)
    if d not in sys.path:
        sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location("audit_n2_r5", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    problems, _ = mod.check_redaction(tmp_path)
    rel = rd.relative_to(tmp_path).as_posix()
    assert any(rel in p for p in problems)
    assert all(s not in prob for prob in problems for s in SECRETS), \
        "scanner never prints content"
