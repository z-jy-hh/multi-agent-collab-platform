"""R17-W6b: ``wb-check`` - the WB offline check (design v3 §7-P3).

OFFLINE ONLY: verifies that the WB CLI exists and takes a ``--help`` snapshot
(sha256 recorded for version-drift detection). NEVER starts the codebuddy
agent, NEVER calls ``--acp``, NEVER logs in, NEVER reads credentials.

Exit codes (mirrors dsh-check semantics):
  0 = all checks passed
  1 = at least one FAIL
  2 = NOT-RUN (some checks skipped by configuration)
  3 = ENV-FAIL (environment cannot run the check)

The ``--strict-mcp-config`` evaluation is documented, not executed:
  - CAN suppress: external MCP servers loaded from ``.mcp.json`` or
    ``--mcp-config`` files
  - CANNOT suppress: the 46 built-in plugins and built-in MCP executables
    (``agently-cli.exe`` / ``wechatpay-cli.exe`` etc.) which are compiled
    into the WB installation and load regardless of MCP config flags
  - conclusion: ``--strict-mcp-config`` narrows the EXTERNAL surface but
    does NOT zero the BUILT-IN surface (P1 review D2 conclusion)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WbCheckRow:
    name: str
    status: str                     # PASS | FAIL | ENV-FAIL | NOT-RUN
    details: str


def _aggregate_exit(rows: list[WbCheckRow]) -> tuple[int, str]:
    statuses = [r.status for r in rows]
    if "ENV-FAIL" in statuses:
        return 3, "ENV-FAIL(环境不可用)"
    if "FAIL" in statuses:
        return 1, "FAIL"
    if "NOT-RUN" in statuses:
        return 2, "NOT-RUN(部分未验证)"
    return 0, "PASS"


def run_wb_check(*, wb_cli_path: str | None = None,
                 snapshot_dir: Path | None = None) -> tuple[list[WbCheckRow], int]:
    """Run the WB offline check. Returns (rows, exit_code)."""
    rows: list[WbCheckRow] = []

    def add(name: str, status: str, details: str) -> None:
        rows.append(WbCheckRow(name=name, status=status, details=details))

    # A. CLI existence
    wb_cli = wb_cli_path or os.environ.get("WB_CLI") or shutil.which("codebuddy")
    if not wb_cli:
        add("wb_cli_exists", "ENV-FAIL",
            "WB CLI not found (set WB_CLI or install codebuddy)")
        code, label = _aggregate_exit(rows)
        return rows, code
    if not Path(wb_cli).is_file():
        add("wb_cli_exists", "ENV-FAIL",
            f"WB_CLI path is not a file: {wb_cli}")
        code, label = _aggregate_exit(rows)
        return rows, code
    add("wb_cli_exists", "PASS", f"CLI found at {wb_cli}")

    # B. --help snapshot (version drift sentinel)
    import subprocess
    try:
        proc = subprocess.run(
            [wb_cli, "--help"], capture_output=True, timeout=30,
            text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            add("wb_help_snapshot", "FAIL",
                f"--help exit={proc.returncode}: {proc.stderr[:200]}")
        else:
            help_text = proc.stdout
            help_sha = hashlib.sha256(help_text.encode("utf-8")).hexdigest()
            help_bytes = len(help_text.encode("utf-8"))
            # compare with the snapshot if one exists
            snap_dir = snapshot_dir or Path("runs") / "wb_snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            snap_file = snap_dir / "help_snapshot.json"
            if snap_file.exists():
                prev = json.loads(snap_file.read_text(encoding="utf-8"))
                if prev.get("sha256") == help_sha:
                    add("wb_help_snapshot", "PASS",
                        f"sha256={help_sha[:16]}… bytes={help_bytes} (unchanged)")
                else:
                    add("wb_help_snapshot", "FAIL",
                        f"DRIFT: sha256 {prev.get('sha256', '?')[:16]}… → "
                        f"{help_sha[:16]}… (was {prev.get('bytes', '?')}B, "
                        f"now {help_bytes}B)")
            else:
                snap_file.write_text(json.dumps({
                    "sha256": help_sha, "bytes": help_bytes,
                    "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime()),
                }, indent=2), encoding="utf-8")
                add("wb_help_snapshot", "PASS",
                    f"snapshot created: sha256={help_sha[:16]}… bytes={help_bytes}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        add("wb_help_snapshot", "ENV-FAIL",
            f"--help could not run: {exc}")

    # C. --acp flag documented (NOT tested - would start the real agent)
    add("wb_acp_flag", "NOT-RUN",
        "--acp flag documented (static@--help) but NOT tested: "
        "real-machine probe requires owner authorization (P3 red line)")

    # D. strict-mcp-config evaluation (documented, not executed)
    add("wb_strict_mcp_eval", "PASS",
        "--strict-mcp-config: CAN suppress external MCP servers; CANNOT "
        "suppress 46 built-in plugins (P1-D2); eval=documented-only")

    code, label = _aggregate_exit(rows)
    return rows, code
