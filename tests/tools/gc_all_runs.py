"""R17-BCFIX-2 P0: batch GC for ALL run directories (one-time cleanup).

Removes rotated health.json.* and trace.jsonl.* beyond keep=20 for every
run directory under runs/. Also removes run directories that have NO
state.json (abandoned runs from old test runs).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

def gc_all_runs(runs_dir: Path, *, keep: int = 20) -> dict:
    result = {"dirs_scanned": 0, "dirs_removed": 0,
              "health_gens_before": 0, "health_gens_after": 0,
              "trace_gens_before": 0, "trace_gens_after": 0}
    if not runs_dir.is_dir():
        return result
    for rd in sorted(runs_dir.iterdir()):
        if not rd.is_dir():
            continue
        result["dirs_scanned"] += 1
        # remove rotated generations beyond keep
        for prefix in ("health.json", "trace.jsonl"):
            gens = sorted(
                (int(p.name.rsplit(".", 1)[1]), p)
                for p in rd.glob(f"{prefix}.*")
                if p.name.rsplit(".", 1)[1].isdigit())
            key = f"{prefix.split('.')[0]}_gens"
            before = len(gens)
            if key.startswith("health"):
                result["health_gens_before"] += before
            else:
                result["trace_gens_before"] += before
            while len(gens) > keep:
                _, old = gens.pop(0)
                old.unlink(missing_ok=True)
            after = len(gens)
            if key.startswith("health"):
                result["health_gens_after"] += after
            else:
                result["trace_gens_after"] += after
        # remove empty/abandoned run dirs (no state.json, no trace.jsonl)
        if not (rd / "state.json").exists() and not (rd / "trace.jsonl").exists():
            has_content = any(rd.iterdir())
            if not has_content:
                rd.rmdir()
                result["dirs_removed"] += 1
            elif all(f.name.startswith(".") or f.name == "__pycache__"
                     for f in rd.iterdir() if f.is_file()):
                shutil.rmtree(rd, ignore_errors=True)
                result["dirs_removed"] += 1
    return result


if __name__ == "__main__":
    from core.paths import run_dir
    runs_dir = run_dir("dummy").parent  # runs/
    before = gc_all_runs(runs_dir, keep=20)
    print(json.dumps(before, indent=2))
