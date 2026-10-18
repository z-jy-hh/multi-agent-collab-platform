"""R17-BCFIX2 (B-1): --check liveness acceptance tests.

L1 three classes (alive / stalled / outside-window), the escalation-note
class, and the mutation canary: a BROKEN liveness derivation must blind the
checker — a green result is only meaningful if it depends on the derivation.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[3] / "tests" / "tools" / "audit_invariants.py"


def _load_tool():
    # audit_invariants.py is a SCRIPT (imports its sibling mutation_probe via
    # a plain import); mirror the subprocess sys.path here. Same loader as
    # test_audit_fingerprints.py.
    tool_dir = str(_TOOL.parent)
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)
    spec = importlib.util.spec_from_file_location("audit_invariants_liveness", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_run(root: Path, name: str, *, status="running", event_age_s=0.0,
              recovery=None):
    rd = root / "runs" / name
    rd.mkdir(parents=True)
    (rd / "state.json").write_text(json.dumps({"status": status}),
                                   encoding="utf-8")
    ev = {"seq": 1, "ts": time.time() - event_age_s, "type": "node_end"}
    (rd / "trace.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
    if recovery is not None:
        (rd / "recovery.json").write_text(json.dumps(recovery),
                                          encoding="utf-8")
    return rd


class TestL1CaseClasses:
    def test_alive_run_no_violation(self, tmp_path):
        mod = _load_tool()
        _make_run(tmp_path, "r_alive", event_age_s=0.0)
        problems, notes = mod.check_liveness(tmp_path, since_hours=24.0)
        assert problems == []
        assert any("assessed 1" in n for n in notes)

    def test_stalled_run_flagged_by_name(self, tmp_path):
        mod = _load_tool()
        _make_run(tmp_path, "r_stall", event_age_s=9999.0)
        problems, notes = mod.check_liveness(tmp_path, since_hours=24.0)
        assert any("r_stall" in p for p in problems)
        assert any("stalled" in p for p in problems)

    def test_pending_permission_holds_run_alive(self, tmp_path):
        mod = _load_tool()
        rd = _make_run(tmp_path, "r_perm", event_age_s=9999.0)
        (rd / "trace.jsonl").write_text(
            json.dumps({"seq": 1, "ts": time.time() - 9999,
                        "type": "dsh_permission_request"}) + "\n",
            encoding="utf-8")
        problems, _ = mod.check_liveness(tmp_path, since_hours=24.0)
        assert problems == []

    def test_terminal_run_not_flagged(self, tmp_path):
        mod = _load_tool()
        _make_run(tmp_path, "r_done", status="done", event_age_s=9999.0)
        problems, _ = mod.check_liveness(tmp_path, since_hours=24.0)
        assert problems == []

    def test_escalated_is_visible_note_not_violation(self, tmp_path):
        mod = _load_tool()
        _make_run(tmp_path, "r_esc", event_age_s=9999.0,
                  recovery={"state": "escalated", "attempt_count": 3})
        problems, notes = mod.check_liveness(tmp_path, since_hours=24.0)
        assert problems == []
        assert any("r_esc" in n and "escalated" in n for n in notes)


class TestL7WindowRule:
    def test_aged_out_run_skipped_visibly(self, tmp_path):
        mod = _load_tool()
        rd = _make_run(tmp_path, "r_old", event_age_s=9999.0)
        old = time.time() - 48 * 3600
        for f in ("state.json", "trace.jsonl"):
            os.utime(rd / f, (old, old))
        problems, notes = mod.check_liveness(tmp_path, since_hours=24.0)
        # the run IS stalled but outside the window: no red, visible count
        assert problems == []
        assert any("1 outside_window" in n for n in notes)

    def test_wide_window_assesses_aged_run(self, tmp_path):
        """Counterpart of the L7 test: widening the window to 72h brings the
        48h-old stalled run back INSIDE the assessment (window is the only
        thing that excluded it, not the stall)."""
        mod = _load_tool()
        rd = _make_run(tmp_path, "r_old", event_age_s=9999.0)
        old = time.time() - 48 * 3600
        for f in ("state.json", "trace.jsonl"):
            os.utime(rd / f, (old, old))
        problems, notes = mod.check_liveness(tmp_path, since_hours=72.0)
        assert any("r_old" in p for p in problems)


class TestMutationCanary:
    def test_broken_derivation_blinds_checker(self, tmp_path, monkeypatch):
        """Discriminating case: if derive_liveness lies (everything alive),
        the checker must go BLIND (no violation) — proving the verdict
        actually flows from the artifact derivation, not a hardcoded pass."""
        import agent_bridge.recovery as rec_mod
        mod = _load_tool()
        _make_run(tmp_path, "r_stall", event_age_s=9999.0)
        orig = rec_mod.derive_liveness

        def broken(run_id, **kw):
            d = dict(orig(run_id, **kw))
            d["live_paths"] = ["active_run"]
            d["stalled"] = False
            return d

        monkeypatch.setattr(rec_mod, "derive_liveness", broken)
        problems, _ = mod.check_liveness(tmp_path, since_hours=24.0)
        assert problems == []


class TestCLI:
    def _cli(self, root):
        return subprocess.run(
            [sys.executable, str(_TOOL), "--check", "liveness",
             "--root", str(root)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_TOOL.parents[2]))

    def test_choice_registered_and_passes(self, tmp_path):
        proc = self._cli(tmp_path)  # no runs/ dir: nothing to assess
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert "AUDIT INVARIANTS: PASS" in proc.stdout

    def test_stalled_fixture_exits_1_and_names_run(self, tmp_path):
        _make_run(tmp_path, "r_stall", event_age_s=9999.0)
        proc = self._cli(tmp_path)
        assert proc.returncode == 1
        assert "r_stall" in proc.stderr
        assert "AUDIT INVARIANTS: FAIL" in proc.stderr
