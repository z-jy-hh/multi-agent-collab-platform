"""R14-3: OFFLINE regression for the evolve auto flow (no key, no real LLM).

This file exists because the R13 authorized real-LLM rerun exposed a P0 that
four offline-green rounds never covered: the auto branch writes the SAME
version's record TWICE (provisional before apply, final after the golden
compare) and the R10-1 append-only gate refused the second write — with the
exception landing AFTER apply_candidate and BEFORE the rollback block, so the
live prompt stayed in candidate state (see reports/R13_addendum.md §4 and
reports/round13_evolve_auto_real.log).

Isolation technique (all monkeypatched, zero disk/network outside tmp_path):
  * ``core.paths.PROJECT_ROOT`` + ``evolver.guard.PROJECT_ROOT`` -> tmp project
    (cmd_evolve imports PROJECT_ROOT at CALL time; guard binds it at import);
  * ``evolver.versions.prompt_versions_dir`` -> tmp version store (real
    prompts/.versions is never touched);
  * ``main._load_evolve_cfg`` -> fixed auto config (K=1, threshold=0.01);
  * ``evolver.golden.run_golden`` -> scripted Metrics (1st call = baseline,
    2nd = candidate / raise) — the REAL golden path is covered by the
    authorized real-LLM acceptance run of this round, not here;
  * ``evolver.analyzer.analyze`` / ``evolver.optimizer.propose_changes`` ->
    one synthetic Candidate against a prompts/ whitelisted file.
"""

import argparse
import json

import pytest

import core.paths
import evolver.analyzer
import evolver.golden
import evolver.guard
import evolver.optimizer
import evolver.versions
import main as main_module
from core.errors import GuardViolation
from evolver.optimizer import Candidate
from evolver.versions import Metrics, VersionStore

FILE_REL = "prompts/worker/writer.v1.md"
ORIG = "原始提示词 v6 内容\nline2\nline3\n"
CAND = "候选改写内容(优化后)\nnew line\n"
BASELINE = Metrics(quality=50.0, cost=100.0, latency_s=1.0)
BETTER = Metrics(quality=90.0, cost=90.0, latency_s=0.8)   # delta > threshold -> KEEP
WORSE = Metrics(quality=40.0, cost=120.0, latency_s=1.3)   # delta < 0 -> ROLLBACK


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    live = tmp_path / FILE_REL
    live.parent.mkdir(parents=True)
    # BINARY write (EOL fidelity, round-7 lesson): text mode would translate
    # \n -> \r\n on Windows and corrupt the byte-level baseline
    live.write_bytes(ORIG.encode("utf-8"))
    versions_dir = tmp_path / "prompts" / ".versions"
    monkeypatch.setattr(core.paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(evolver.guard, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(evolver.versions, "prompt_versions_dir",
                        lambda: versions_dir)
    monkeypatch.setattr(main_module, "_load_evolve_cfg",
                        lambda: {"mode": "auto", "K": 1, "threshold": 0.01})
    monkeypatch.setattr(evolver.analyzer, "analyze", lambda reports, events: [])
    monkeypatch.setattr(evolver.optimizer, "propose_changes",
                        lambda weaknesses, root: [Candidate(
                            file=FILE_REL, new_content=CAND,
                            reason="R14 离线测试候选",
                            diff="--- a/prompts/worker/writer.v1.md\n"
                                 "+++ b/prompts/worker/writer.v1.md\n")])
    return {"tmp": tmp_path, "live": live, "versions": versions_dir}


def _patch_golden(monkeypatch, candidate):
    """1st call -> baseline, later calls -> candidate (Metrics or Exception)."""
    calls = {"n": 0}
    prefixes = []

    def fake_run_golden(K=3, tasks=None, run_prefix="golden"):
        calls["n"] += 1
        prefixes.append(run_prefix)
        if calls["n"] == 1:
            return BASELINE
        if isinstance(candidate, BaseException):
            raise candidate
        return candidate

    monkeypatch.setattr(evolver.golden, "run_golden", fake_run_golden)
    return calls, prefixes


def _record(versions_dir, version=1):
    rec = versions_dir / f"v{version:03d}" / "record.json"
    return json.loads(rec.read_text(encoding="utf-8"))


def _auto():
    return main_module.cmd_evolve(argparse.Namespace(mode="auto"))


# ---- ① happy path -----------------------------------------------------------

def test_happy_path_keep_completes_with_final_decision(sandbox, monkeypatch):
    _patch_golden(monkeypatch, BETTER)
    rc = _auto()
    assert rc == 0
    rec = _record(sandbox["versions"])
    assert rec["decision"] == "KEEP"  # final, never left as PENDING
    assert rec["version"] == 1
    assert sandbox["live"].read_text(encoding="utf-8") == CAND  # candidate kept


def test_happy_path_rollback_restores_live_bytes(sandbox, monkeypatch, capsys):
    _patch_golden(monkeypatch, WORSE)
    rc = _auto()
    assert rc == 0
    rec = _record(sandbox["versions"])
    assert rec["decision"] == "ROLLBACK"
    assert sandbox["live"].read_bytes() == ORIG.encode("utf-8")  # byte-identical
    out = capsys.readouterr().out
    assert "[auto] 已回滚;字节级还原校验: PASS" in out
    assert "sha256=" in out  # per-file verification lines (K5)


# ---- ② exception injection -> byte restore + loud propagation ---------------

def test_exception_at_golden_restores_live_and_propagates(sandbox, monkeypatch, capsys):
    calls, _ = _patch_golden(monkeypatch, RuntimeError("golden 复跑爆炸"))
    before = sandbox["live"].read_bytes()
    with pytest.raises(RuntimeError, match="golden 复跑爆炸"):
        _auto()  # the ORIGINAL exception propagates loud (never swallowed)
    assert calls["n"] == 2  # failed on the candidate (2nd) golden run
    assert sandbox["live"].read_bytes() == before  # BYTE-LEVEL restore
    rec = _record(sandbox["versions"])
    assert rec["decision"] == "PENDING"  # tombstone: in-progress, never final
    out = capsys.readouterr().out
    assert "[auto] 异常回滚;字节级还原校验: PASS" in out
    assert "sha256=" in out


def test_exception_at_final_record_write_restores_live(sandbox, monkeypatch, capsys):
    # inject the failure at the exact spot of the R13 incident: the FINAL
    # save_record (main.py:151 in the pre-R14 code)
    real_save = evolver.versions.VersionStore.save_record
    state = {"n": 0}

    def flaky_save(self, version, reason, decision, score_delta, files, diff=""):
        state["n"] += 1
        if state["n"] >= 2:
            raise RuntimeError("final write boom")
        return real_save(self, version, reason, decision, score_delta,
                         files, diff=diff)

    monkeypatch.setattr(evolver.versions.VersionStore, "save_record", flaky_save)
    _patch_golden(monkeypatch, BETTER)
    before = sandbox["live"].read_bytes()
    with pytest.raises(RuntimeError, match="final write boom"):
        _auto()
    assert sandbox["live"].read_bytes() == before  # restored despite the failure
    rec = _record(sandbox["versions"])
    assert rec["decision"] == "PENDING"
    assert "[auto] 异常回滚;字节级还原校验: PASS" in capsys.readouterr().out


# ---- ③ gate invariants (R10-1 intent intact under R14-1) ---------------------

def test_gate_final_record_still_refuses_overwrite(sandbox):
    store = VersionStore()
    hashes = store.snapshot(1, {FILE_REL: sandbox["live"]})
    store.save_record(1, "r", "PENDING", 0.0, hashes)
    store.save_record(1, "r", "KEEP", 0.5, hashes)  # the ONE allowed upgrade
    with pytest.raises(GuardViolation, match="already carries record version 1"):
        store.save_record(1, "r", "ROLLBACK", 0.0, hashes)  # final -> refused
    with pytest.raises(GuardViolation, match="already carries record version 1"):
        store.save_record(1, "r", "PENDING", 0.0, hashes)   # final -> refused


def test_gate_pending_allows_one_update_then_refuses(sandbox):
    store = VersionStore()
    hashes = store.snapshot(1, {FILE_REL: sandbox["live"]})
    store.save_record(1, "r", "PENDING", 0.0, hashes)
    with pytest.raises(GuardViolation, match="PENDING record version 1"):
        store.save_record(1, "r", "PENDING", 0.0, hashes)  # 2nd PENDING: refused
    store.save_record(1, "r", "ROLLBACK", -0.1, hashes)    # the one upgrade: OK
    with pytest.raises(GuardViolation, match="already carries record version 1"):
        store.save_record(1, "r", "KEEP", 0.1, hashes)     # post-final: refused


# ---- ④ offline replica of the incident path ---------------------------------

def test_incident_replica_two_writes_same_version_completes(sandbox, monkeypatch):
    """THE hole: the auto flow writes the same version's record twice. Under
    the pre-R14 code the second write raised GuardViolation mid-flow (the
    R13 incident); offline FakeLLM runs never reached this branch, which is
    why four green rounds missed it. This test drives the FULL path offline
    and must complete with a final decision."""
    seen_calls: list[tuple[int, str]] = []
    real_save = evolver.versions.VersionStore.save_record

    def counting_save(self, version, reason, decision, score_delta, files, diff=""):
        seen_calls.append((version, decision))
        return real_save(self, version, reason, decision, score_delta,
                         files, diff=diff)

    monkeypatch.setattr(evolver.versions.VersionStore, "save_record", counting_save)
    _patch_golden(monkeypatch, BETTER)
    rc = _auto()
    assert rc == 0
    # exactly the incident shape: TWO records for the SAME version...
    assert seen_calls == [(1, "PENDING"), (1, "KEEP")]
    # ...and the flow COMPLETES with a final (non-PENDING) decision
    rec = _record(sandbox["versions"])
    assert rec["decision"] in ("KEEP", "ROLLBACK")
    assert rec["decision"] != "PENDING"


def test_incident_replica_rollback_branch_two_writes_complete(sandbox, monkeypatch):
    # the R13 incident was heading for ROLLBACK — drive that variant too
    _patch_golden(monkeypatch, WORSE)
    rc = _auto()
    assert rc == 0
    rec = _record(sandbox["versions"])
    assert rec["decision"] == "ROLLBACK"
    assert sandbox["live"].read_bytes() == ORIG.encode("utf-8")
