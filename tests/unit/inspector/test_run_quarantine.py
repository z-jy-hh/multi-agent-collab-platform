"""R17-BCFIX3 R2 (ROI-1): move-only quarantine tests.

Discriminators required by the work order:
  (1) after quarantine the original dir is GONE from the runs top level;
  (2) ZERO content loss (file count + per-file sha256 identical);
  (3) restore is byte-identical (sha256);
  (4) terminal / running dirs NEVER enter quarantine.
Plus: active (freshly written) statusless dirs are never touched, dry run
moves nothing, restore collisions are loud, and the whole op is proven
deletion-free by a global file census.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from inspector.report import (_dir_inventory, quarantine_statusless_dirs,
                              restore_quarantine)


def _sha_of(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _mk(root: Path, name: str, *, status: str | None = None, files: int = 2,
        age_days: float = 30.0, run_shaped: bool = True) -> Path:
    rd = root / name
    rd.mkdir(parents=True, exist_ok=True)
    if run_shaped:
        # a run dir carries at least one liveness artifact (trace/health/...)
        (rd / "trace.jsonl").write_text(
            json.dumps({"seq": 1, "ts": 1.0, "type": "node_end"}) + "\n",
            encoding="utf-8")
    if status is not None:
        (rd / "state.json").write_text(
            json.dumps({"run_id": name, "status": status}), encoding="utf-8")
    for i in range(files):
        (rd / f"artifact_{i}.bin").write_bytes(f"payload-{name}-{i}".encode())
    if age_days:
        old = time.time() - age_days * 86400.0
        os.utime(rd, (old, old))
        for p in rd.rglob("*"):
            os.utime(p, (old, old))
    return rd


def _census(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file())


class TestQuarantine:
    def test_statusless_moved_out_of_top_level(self, tmp_path):
        rd = _mk(tmp_path, "stale-statusless", age_days=30.0)
        m = quarantine_statusless_dirs(tmp_path, apply=True)
        assert not rd.exists(), "quarantined dir must leave the top level"
        ts = m["ts"]
        qdir = tmp_path / "_quarantine" / ts / "stale-statusless"
        assert qdir.is_dir()
        assert m["quarantined_count"] == 1
        assert m["quarantined"][0]["dir"] == "stale-statusless"

    def test_zero_loss_sha256(self, tmp_path):
        rd = _mk(tmp_path, "stale-statusless", files=3, age_days=30.0)
        before = _dir_inventory(rd)
        m = quarantine_statusless_dirs(tmp_path, apply=True)
        entry = m["quarantined"][0]
        assert entry["files"] == before
        assert entry["file_count"] == len(before)
        qdir = tmp_path / "_quarantine" / m["ts"] / "stale-statusless"
        assert _dir_inventory(qdir) == before, "content must survive the move"

    def test_restore_byte_identical(self, tmp_path):
        rd = _mk(tmp_path, "stale-statusless", files=3, age_days=30.0)
        before = _dir_inventory(rd)
        m = quarantine_statusless_dirs(tmp_path, apply=True)
        out = restore_quarantine(m["ts"], tmp_path)
        assert out["verified"] is True
        assert out["restored_count"] == 1
        assert _dir_inventory(rd) == before, "restore must be byte-identical"
        qts = tmp_path / "_quarantine" / m["ts"]
        # only the audit manifest remains; no dir entries left behind
        leftover_dirs = [p for p in qts.iterdir() if p.is_dir()]
        assert leftover_dirs == []
        assert (qts / "manifest.json").exists()

    def test_terminal_and_running_never_quarantined(self, tmp_path):
        done = _mk(tmp_path, "old-done", status="done", age_days=30.0)
        run = _mk(tmp_path, "old-running", status="running", age_days=30.0)
        m = quarantine_statusless_dirs(tmp_path, apply=True)
        assert done.exists() and run.exists()
        names = [e["dir"] for e in m["quarantined"]]
        assert "old-done" not in names and "old-running" not in names
        assert m["skipped_with_status"] == 2

    def test_active_statusless_not_quarantined(self, tmp_path):
        """A statusless dir being written RIGHT NOW (fresh mtimes) is never
        touched -- it may be a live run that simply has not reached its
        terminal state write yet."""
        rd = _mk(tmp_path, "fresh-statusless", age_days=0.0)
        m = quarantine_statusless_dirs(tmp_path, apply=True)
        assert rd.exists()
        assert m["quarantined_count"] == 0
        assert m["skipped_active_statusless"] == 1

    def test_dry_run_moves_nothing(self, tmp_path):
        rd = _mk(tmp_path, "stale-statusless", age_days=30.0)
        m = quarantine_statusless_dirs(tmp_path, apply=False)
        assert rd.exists(), "dry run must not move"
        assert m["dry_run"] is True
        assert m["quarantined_count"] == 1  # counted, not moved
        assert not (tmp_path / "_quarantine" / m["ts"]).exists()

    def test_restore_collision_is_loud(self, tmp_path):
        _mk(tmp_path, "stale-statusless", age_days=30.0)
        m = quarantine_statusless_dirs(tmp_path, apply=True)
        # someone recreates the same name at the top level before restore
        _mk(tmp_path, "stale-statusless", age_days=0.0)
        import pytest
        with pytest.raises(RuntimeError, match="restore collision"):
            restore_quarantine(m["ts"], tmp_path)

    def test_infrastructure_dirs_never_quarantined(self, tmp_path):
        """Workspace / fixture dirs (dispatch roots with counters, session
        subdirs) carry NO run artifacts -- they are not run dirs and must
        never be quarantined, even when stale and statusless."""
        ws = tmp_path / "dsh_dispatch"
        ws.mkdir()
        (ws / "gate_counters.json").write_text("{}", encoding="utf-8")
        (ws / "somesession").mkdir()
        (ws / "somesession" / "log.txt").write_text("x", encoding="utf-8")
        m = quarantine_statusless_dirs(tmp_path, apply=True)
        assert ws.exists(), "infrastructure dir must never be quarantined"
        assert m["skipped_not_run_dir"] == 1
        assert m["quarantined_count"] == 0

    def test_no_deletion_invariant(self, tmp_path):
        """The whole operation is a rename: every PRE-EXISTING file survives;
        the only new file is the quarantine manifest itself."""
        _mk(tmp_path, "stale-statusless", files=3, age_days=30.0)
        _mk(tmp_path, "old-done", status="done", files=1, age_days=30.0)
        before = _census(tmp_path)
        before_sha = {_sha_of(p) for p in tmp_path.rglob("*") if p.is_file()}
        quarantine_statusless_dirs(tmp_path, apply=True)
        after_files = [p for p in tmp_path.rglob("*") if p.is_file()]
        manifest_files = sum(
            1 for p in after_files
            if "_quarantine" in p.parts and p.name == "manifest.json")
        assert len(after_files) - manifest_files == before, \
            "no pre-existing file may vanish"
        after_sha = {_sha_of(p) for p in after_files}
        assert before_sha <= after_sha
