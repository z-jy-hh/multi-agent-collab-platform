"""Rectification C2: the J2(c) baseline must not self-dirty the tree —
two consecutive `--check clean` runs both PASS, and between them
`git status --porcelain` never shows the baseline file (it lives under
gitignored runs/ since A2 option ①)."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# N7 (round-6): the measured channel here is `git status` — in a non-git
# tree (e.g. a tarball checkout) the check cannot run and the test would
# turn red for the wrong reason. Skip instead.
if not (ROOT / ".git").exists():
    pytest.skip("requires a git worktree (git status is the measured channel)",
                allow_module_level=True)


def _run_check():
    return subprocess.run(
        [sys.executable, "tests/tools/audit_invariants.py", "--check", "clean"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace")


def test_double_clean_run_passes_without_self_dirtying():
    for i in (1, 2):
        proc = _run_check()
        assert proc.returncode == 0, f"run {i} failed:\n{proc.stdout}\n{proc.stderr}"
        status = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                                capture_output=True, text=True).stdout
        assert "crewai_names_state" not in status, \
            f"run {i}: the baseline file must live under gitignored runs/, not in git status"
