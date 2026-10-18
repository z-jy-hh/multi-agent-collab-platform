"""Run EVERY gate once, from a clean tree, and print one summary.

Why: running tests on a dirty tree produces false failures (the self-dirtying
suite treats uncommitted edits as pollution), which this session hit twice.
The first thing this script does is refuse to run when the tree is dirty.

    .venv\\Scripts\\python.exe tests\\tools\\gate_all.py
    .venv\\Scripts\\python.exe tests\\tools\\gate_all.py --allow-dirty   (debug only)

Exit codes: 0 all pass, 2 tree dirty, 1 a gate failed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    tail = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, tail


def main(argv: list[str]) -> int:
    allow_dirty = "--allow-dirty" in argv
    code, status = _run(["git", "status", "--porcelain"])
    if code != 0:
        print("GATE_ALL: cannot read git status")
        return 1
    if status.strip() and not allow_dirty:
        print("GATE_ALL: REFUSED — working tree is dirty; commit first.")
        print(status)
        return 2

    steps = [
        ("pytest", [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"]),
        ("ledgercheck", [PY, "tests/tools/ledgercheck.py"]),
        ("audit", [PY, "tests/tools/audit_invariants.py", "--check", "clean"]),
        ("layercheck", [PY, "tests/tools/layercheck.py"]),
        ("ui_mutation", [PY, "tests/tools/ui_mutation_probe.py", "--quick"]),
    ]
    results: list[tuple[str, bool]] = []
    for name, cmd in steps:
        code, tail = _run(cmd)
        ok = code == 0
        results.append((name, ok))
        print(f"=== {name}: {'PASS' if ok else 'FAIL'} ===")
        print("\n".join(tail.strip().splitlines()[-4:]))
        if not ok:
            break

    print("\nGATE_ALL SUMMARY")
    for name, ok in results:
        print(f"  {name:12s} {'PASS' if ok else 'FAIL'}")
    return 0 if all(ok for _, ok in results) and len(results) == len(steps) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
