"""R17-BCFIX3: run-directory cleanup policy tests (review criterion 11 fix).

The discriminator requirement from the review order: a cleanup MUST keep
statusless / unreadable directories, otherwise the liveness audit loses the
exact evidence class it exists to review. The false-fix discriminator here is
``test_statusless_dir_is_never_removed``: a "fix" that drops the statusless
guard turns it red.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from inspector.report import (CLEANUP_TERMINAL_STATUSES, cleanup_run_dirs,
                              read_run_status)


_NO_STATE = object()          # module-level sentinel (a fresh object() is never it)


def _mk(root: Path, name: str, *, status=_NO_STATE, age_days: float = 30.0,
        raw: str | None = None) -> Path:
    rd = root / name
    rd.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (rd / "state.json").write_text(raw, encoding="utf-8")
    elif status is not _NO_STATE:
        (rd / "state.json").write_text(
            json.dumps({"run_id": name, "status": status}), encoding="utf-8")
    # no state.json at all when status is _NO_STATE and raw is None
    old = time.time() - age_days * 86400.0
    os.utime(rd, (old, old))
    return rd


def test_statusless_dir_is_never_removed(tmp_path):
    """DISCRIMINATOR: dropping the statusless guard turns this red."""
    rd = _mk(tmp_path, "statusless-no-state", age_days=30.0)
    manifest = cleanup_run_dirs(tmp_path, cutoff_days=7.0, apply=True,
                                write_manifest=False)
    assert rd.exists(), "statusless run dir must survive the cleanup"
    assert manifest["removed_count"] == 0
    assert manifest["kept_statusless"] == 1
    status, statusless = read_run_status(rd)
    assert status is None and statusless is True


def test_unreadable_state_is_never_removed(tmp_path):
    rd = _mk(tmp_path, "unreadable-state", raw="{not json", age_days=30.0)
    cleanup_run_dirs(tmp_path, cutoff_days=7.0, apply=True, write_manifest=False)
    assert rd.exists(), "unreadable state.json must survive the cleanup"


def test_no_status_key_is_never_removed(tmp_path):
    rd = _mk(tmp_path, "no-status-key", raw=json.dumps({"run_id": "x"}),
             age_days=30.0)
    cleanup_run_dirs(tmp_path, cutoff_days=7.0, apply=True, write_manifest=False)
    assert rd.exists(), "state without a status key must survive the cleanup"


def test_running_and_summarized_are_kept(tmp_path):
    a = _mk(tmp_path, "still-running", status="running", age_days=30.0)
    b = _mk(tmp_path, "summarized-run", status="summarized", age_days=30.0)
    cleanup_run_dirs(tmp_path, cutoff_days=7.0, apply=True, write_manifest=False)
    assert a.exists() and b.exists()


def test_old_terminal_is_removed_and_fresh_terminal_is_kept(tmp_path):
    old = _mk(tmp_path, "old-done", status="done", age_days=30.0)
    fresh = _mk(tmp_path, "fresh-done", status="done", age_days=0.1)
    manifest = cleanup_run_dirs(tmp_path, cutoff_days=7.0, apply=True,
                                write_manifest=False)
    assert not old.exists(), "old terminal dir must be removed"
    assert fresh.exists(), "fresh terminal dir must be kept"
    assert manifest["removed_count"] == 1
    assert [e["dir"] for e in manifest["removed"]] == ["old-done"]


def test_dry_run_deletes_nothing_but_counts(tmp_path):
    old = _mk(tmp_path, "old-done", status="done", age_days=30.0)
    manifest = cleanup_run_dirs(tmp_path, cutoff_days=7.0, apply=False,
                                write_manifest=False)
    assert old.exists(), "dry run must not delete"
    assert manifest["dry_run"] is True
    assert manifest["removed_count"] == 1


def test_manifest_records_policy_and_is_written(tmp_path):
    _mk(tmp_path, "old-done", status="done", age_days=30.0)
    _mk(tmp_path, "statusless", age_days=30.0)
    manifest = cleanup_run_dirs(tmp_path, cutoff_days=7.0, apply=False)
    assert "terminal-only" in manifest["policy"]
    assert "NEVER removed" in manifest["policy"]
    assert manifest["kept_statusless"] == 1
    mpath = Path(manifest["manifest_path"])
    assert mpath.exists() and mpath.parent == tmp_path
    on_disk = json.loads(mpath.read_text(encoding="utf-8"))
    assert on_disk["removed_count"] == 1 and on_disk["kept_statusless"] == 1


def test_terminal_status_vocabulary_is_closed():
    """The policy only ever removes declared terminal statuses."""
    assert "done" in CLEANUP_TERMINAL_STATUSES
    assert "running" not in CLEANUP_TERMINAL_STATUSES
    assert "summarized" not in CLEANUP_TERMINAL_STATUSES
    assert None not in CLEANUP_TERMINAL_STATUSES
