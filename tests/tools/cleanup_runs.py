"""R17-BCFIX3 ops tool: run-directory cleanup + ROI-1 quarantine.

P3 (T6, workorder sha256_16 a7cd4654648d2bd5) scope note — 仅供历史遗留:
since P3 the platform no longer CREATES statusless run directories
(run_topology closes its trace writer in a finally; test-side cleanup is
fail-loud via tests/integration/_runs_cleanup.py). This tool is therefore
FOR LEGACY DEBT ONLY: it sweeps residue created BEFORE P3 and provides the
zero-footprint census used by the T3 gate. It must never be needed on a
healthy tree — if it reports quarantinable dirs after a full suite run,
that is a regression, not housekeeping.

Usage:
  python tests/tools/cleanup_runs.py                      # dry run, 7 days
  python tests/tools/cleanup_runs.py --cutoff-days 14
  python tests/tools/cleanup_runs.py --apply              # actually delete

  python tests/tools/cleanup_runs.py --quarantine-statusless              # dry run
  python tests/tools/cleanup_runs.py --quarantine-statusless --apply      # move
  python tests/tools/cleanup_runs.py --quarantine-statusless --report-only  # census, NEVER moves
  python tests/tools/cleanup_runs.py --restore-quarantine <TS>            # inverse

Cleanup policy (review criterion 11): terminal-only. statusless / unreadable
state, running and summarized directories are NEVER removed -- they are
reserved for the --check liveness audit.

Quarantine policy (ROI-1, review 5.3 option (b)): STALE statusless dirs are
MOVED (rename, never deleted) into runs/_quarantine/<ts>/<dir>/ with a
per-file sha256 manifest; restore_quarantine moves them back byte-verified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from inspector.report import (cleanup_run_dirs, quarantine_statusless_dirs,
                              restore_quarantine)  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="run-directory cleanup (terminal-only) + statusless "
                    "quarantine (FOR LEGACY DEBT ONLY since P3)")
    ap.add_argument("--root", default=None,
                    help="runs root (default: core.paths.runs_dir())")
    ap.add_argument("--cutoff-days", type=float, default=7.0)
    ap.add_argument("--apply", action="store_true",
                    help="actually delete / move (default: dry run)")
    ap.add_argument("--quarantine-statusless", action="store_true",
                    help="ROI-1: MOVE stale statusless run dirs into "
                         "runs/_quarantine/<ts>/ (dry run without --apply)")
    ap.add_argument("--report-only", action="store_true",
                    help="P3 T3: census of quarantinable statusless dirs — "
                         "reports and counts, NEVER moves; mutually "
                         "exclusive with --apply")
    ap.add_argument("--stale-after-s", type=float, default=3600.0,
                    help="quarantine freshness guard: a statusless dir with "
                         "any file modified within N seconds is never touched "
                         "(default 3600)")
    ap.add_argument("--restore-quarantine", metavar="TS", default=None,
                    help="restore a quarantined timestamp back to runs/ "
                         "(byte-verified against its manifest)")
    a = ap.parse_args(argv)

    if a.report_only and a.apply:
        ap.error("--report-only is a census: it never moves; drop --apply")

    if a.restore_quarantine is not None:
        out = restore_quarantine(a.restore_quarantine, a.root)
        print(f"restored     : {out['restored_count']} dir(s) from {out['ts']}")
        for e in out["restored"]:
            print(f"  - {e['dir']} ({e['files']} files, sha256 verified)")
        print(f"verified     : {out['verified']}")
        return 0

    if a.quarantine_statusless:
        m = quarantine_statusless_dirs(a.root, stale_after_s=a.stale_after_s,
                                       apply=a.apply and not a.report_only)
        print(f"policy       : {m['policy']} (legacy-debt tool, 仅供历史遗留)")
        print(f"dry_run      : {m['dry_run'] or a.report_only}")
        if a.report_only:
            print(f"report_only  : True (census only — nothing moved)")
        print(f"quarantined  : {m['quarantined_count']}")
        print(f"skipped      : active-statusless={m['skipped_active_statusless']} "
              f"with-status={m['skipped_with_status']} "
              f"not-run-dir={m['skipped_not_run_dir']}")
        if m["quarantined_count"]:
            print("dirs         :", json.dumps(
                [e["dir"] for e in m["quarantined"]][:24]))
        print(f"manifest     : {m.get('manifest_path')}")
        return 0

    m = cleanup_run_dirs(a.root, cutoff_days=a.cutoff_days, apply=a.apply)
    print(f"policy      : {m['policy']}")
    print(f"dry_run     : {m['dry_run']}")
    print(f"removed     : {m['removed_count']}")
    print(f"kept        : {m['kept_count']} (statusless kept: {m['kept_statusless']})")
    print(f"manifest    : {m.get('manifest_path')}")
    if a.apply and m["removed_count"]:
        print("removed dirs:", json.dumps([e["dir"] for e in m["removed"]])[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
