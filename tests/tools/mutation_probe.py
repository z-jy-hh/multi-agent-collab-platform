"""Reverse mutation probe (task book 三·D13 / 判据 2b / U4).

For each of three seeded defects:
1. make a FRESH copy of core/ + tests/unit/ (never reuse, never in-place);
2. assert the injection anchor exists in the copy — otherwise ABORT;
3. inject the defect;
4. run the defect's corresponding test file on the copy → MUST fail (RED).
Finally run the suite on a pristine copy → MUST pass (GREEN).

Expected result: ``3 RED + pristine GREEN`` — proof that the test suite
actually catches these regressions (the tests are not vacuous).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFECTS = [
    {
        "name": "vc_merge_takes_min",
        "file": "core/crdt.py",
        "anchor": "max(a.get(k, 0), b.get(k, 0))",
        "old": "max(a.get(k, 0), b.get(k, 0))",
        "new": "min(a.get(k, 0), b.get(k, 0))",
        "test": "tests/unit/core/test_crdt.py",
    },
    {
        "name": "drop_op_id_dedupe",
        "file": "core/storage.py",
        "anchor": "if op.op_id in self._by_id:",
        "old": "            if op.op_id in self._by_id:\n                return False\n",
        "new": "",
        "test": "tests/unit/core/test_memory_idempotent.py",
    },
    {
        "name": "drop_coverage_gate",
        "file": "core/matcher.py",
        "anchor": 'raise NoCapabilityMatch(missing, "hard coverage gate failed; dispatch stopped")',
        "old": ('    missing = missing_capabilities(subtasks, profiles, th)\n'
                '    if missing:\n'
                '        raise NoCapabilityMatch(missing, "hard coverage gate failed; dispatch stopped")\n'),
        "new": "    missing = missing_capabilities(subtasks, profiles, th)  # gate dropped\n",
        "test": "tests/unit/core/test_matcher.py",
    },
]


def make_copy(source_root: Path, root: Path) -> Path:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(source_root / "core", root / "core", ignore=ignore)
    shutil.copytree(source_root / "tests" / "unit", root / "tests" / "unit", ignore=ignore)
    return root


def run_pytest(root: Path, target: str) -> int:
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    env.pop("LAYERCHECK_NEGATIVE", None)
    env.pop("NETWORK_NEGATIVE", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider"],
        cwd=str(root), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",  # J3/D: never let locale decoding mute the channel
    )
    print(proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "(no output)")
    return proc.returncode


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=PROJECT_ROOT,
                        help="source tree to copy from (J3-b fixture: a copy with a removed anchor)")
    args = parser.parse_args()
    source_root = Path(args.root).resolve()

    reds = 0
    with tempfile.TemporaryDirectory(prefix="mutation_") as td:
        base = Path(td)
        for i, defect in enumerate(DEFECTS):
            copy = make_copy(source_root, base / f"defect-{i}")
            target_file = copy / defect["file"]
            content = target_file.read_text(encoding="utf-8")
            if defect["anchor"] not in content:  # abort rather than fake it (K5: loud, on stderr)
                print(f"ABORT: anchor missing for {defect['name']} in {defect['file']}",
                      file=sys.stderr)
                return 2
            target_file.write_text(content.replace(defect["old"], defect["new"], 1),
                                   encoding="utf-8")
            code = run_pytest(copy, defect["test"])
            status = "RED" if code != 0 else "GREEN(unexpected!)"
            reds += 1 if code != 0 else 0
            print(f"{defect['name']}: {status} (exit={code})")

        pristine = make_copy(source_root, base / "pristine")
        code = run_pytest(pristine, "tests/unit/core")
        pristine_ok = code == 0
        print(f"pristine: {'GREEN' if pristine_ok else 'RED(unexpected!)'} (exit={code})")

    ok = reds == len(DEFECTS) and pristine_ok
    print(f"mutation probe: {'PASS' if ok else 'FAIL'} ({reds} RED + pristine "
          f"{'GREEN' if pristine_ok else 'RED'})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
