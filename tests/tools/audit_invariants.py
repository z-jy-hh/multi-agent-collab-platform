"""Audit invariants checker (task book J1/J2/J3 · 20260926).

Single entry point that MACHINE-CHECKS the audit invariants the project has
so far only claimed in prose:

- J1 证据冻结: the 5 fixed-name evidence logs under reports/layercheck/ are
  byte-frozen via LF-normalised sha256_16 against FROZEN_MANIFEST.json.
  Normalisation (CRLF and lone CR -> LF) is mandated by K2: raw-byte hashes
  broke three times under core.autocrlf=true checkout differences.
- J2 运行零污染: running the audit tools leaves the repository unpolluted —
  with a temp log-dir the worktree must stay completely clean (a); with the
  default log-dir every dirty entry must be an untracked file under
  reports/layercheck/ (b). "git status must be empty" is NOT a valid
  criterion (K4) because layercheck legitimately writes timestamped logs.
  J2(c) additionally machine-checks the OUT-OF-PROJECT footprint at CONTENT
  level (round-8): name-set equality alone cannot see a change inside a
  PRE-EXISTING directory — measured, a 139 KB crewai kickoff DB was
  rewritten while the name set stayed 9 -> 9.
- J3 失败出声: tool failures must be LOUD — non-zero exit AND non-empty
  stderr (K5); an injected anchor-missing fault must abort with ABORT.

Negative fixtures (healthy tree) must pass; positive fixtures (tampered
copy) must fail. The checker computes its own hashes / inspects its own
git status — it never takes the checked tool's output as the criterion (K3).

Usage:
    python tests/tools/audit_invariants.py --check frozen|clean|failure|liveness|redaction|all
        [--root <dir>]        # operate on a COPY (fixtures); default: repo root
        [--log-dir-tmp <dir>] # where layercheck's temp logs go during J2(a)
        [--liveness-since-hours <h>] # L7 window rule for --check liveness (default 24)
        [--redaction-allow <glob>]   # N2: extra exclusions for --check redaction
Exit code 0 = invariants hold; 1 = at least one violation (each violation
names the file/path and both sides of the comparison).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mutation_probe import DEFECTS

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FROZEN_FILES = [
    "reports/layercheck/pytest_core.log",
    "reports/layercheck/pytest_unit.log",
    "reports/layercheck/import_all_core.log",
    "reports/layercheck/import_all_unit.log",
    "reports/layercheck/g1_run.log",
]

MUTATION_ANCHOR = DEFECTS[0]["anchor"]  # 补遗 C: single source of truth — from mutation_probe itself


def _norm(text: str) -> str:
    """K2 normalisation: CRLF and lone CR -> LF, then hash."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _sha16_norm(path: Path) -> str:
    return hashlib.sha256(_norm(path.read_text(encoding="utf-8")).encode("utf-8")).hexdigest()[:16]


def _git_status(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        return [f"git status failed: {proc.stderr.strip()}"]
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _run(cmd: list[str], cwd: Path | None = None):
    return subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


# -- J1 ----------------------------------------------------------------------


def check_frozen(root: Path) -> list[str]:
    """J1: LF-normalised hashes of the frozen evidence files must match
    FROZEN_MANIFEST.json; missing files are violations too."""
    problems: list[str] = []
    manifest_path = root / "reports" / "layercheck" / "FROZEN_MANIFEST.json"
    if not manifest_path.exists():
        return [f"manifest missing: {manifest_path}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    seen = set(manifest.get("files", {}))
    for rel in FROZEN_FILES:
        if rel not in seen:
            problems.append(f"{rel}: not covered by manifest (K2 single-truth violated)")
    for rel, expected in manifest.get("files", {}).items():
        path = root / rel
        if not path.exists():
            problems.append(f"{rel}: MISSING (expected sha256_16_norm={expected})")
            continue
        actual = _sha16_norm(path)
        if actual != expected:
            problems.append(f"{rel}: MISMATCH expected={expected} actual={actual}")
    return problems


# -- J2 ----------------------------------------------------------------------


def _status_violations_scoped(lines: list[str]) -> list[str]:
    """J2(b): every dirty entry must be an untracked file (??) under
    reports/layercheck/. Anything else (M/D/R tracked changes, or untracked
    files elsewhere) is pollution."""
    violations = []
    for line in lines:
        ok = line.startswith("?? reports/layercheck/")
        if not ok:
            violations.append(line)
    return violations


def _crewai_storage_names() -> tuple[str | None, list[str]]:
    """J2(c): sorted name set of %LOCALAPPDATA%\\CrewAI. Returns a skip
    reason when LOCALAPPDATA is unset — the SKIP must be VISIBLE, never a
    silent PASS (K3)."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return "LOCALAPPDATA not set", None
    base = Path(local) / "CrewAI"
    if not base.is_dir():
        return "", []
    return "", sorted(p.name for p in base.iterdir())


def _crewai_name_state_path(root: Path) -> Path:
    """A2 (round-5, option (1)): the baseline lives at runs/crewai_names_state.json
    — `runs/` is gitignored, so the baseline is a MACHINE-/CLONE-LOCAL state
    that never appears in git status and never dirties the evidence layer.
    Round-8: the payload carries CONTENT-level fingerprints, not just names."""
    return root / "runs" / "crewai_names_state.json"


def _load_crewai_state(root: Path) -> dict | None:
    state = _crewai_name_state_path(root)
    if not state.exists():
        return None
    try:
        return json.loads(state.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _save_crewai_state(root: Path, names: list[str], fingerprints: dict) -> None:
    state = _crewai_name_state_path(root)
    state.parent.mkdir(parents=True, exist_ok=True)
    # A2①: no volatile fields (no mtimes, no timestamps) — byte-stable across runs
    payload = {"names": sorted(names),
               "fingerprints": {k: fingerprints[k] for k in sorted(fingerprints)}}
    tmp = state.with_suffix(".tmp")
    tmp.write_bytes(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))  # LF bytes
    tmp.replace(state)


def _fp16(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# R9-6: crewai's config write-test is TRANSIENT by design — created and
# self-deleted within one import (README known-boundaries). A J2(c) snapshot
# landing inside that window would report a false ADDED / CHANGED. Named
# exclusion (NOT a baseline re-record): the file stays invisible to the
# fingerprint criterion, and every skip is printed as a visible NOTE.
TRANSIENT_FILENAMES = {".crewai_write_test"}


def _fingerprint_tree(base: Path, prefix: str, out: dict, problems: list,
                      notes: list | None = None) -> None:
    """Content-level fingerprint of one out-of-project area:
    ``<prefix>/<relpath> -> 'dir' | '<size>:<sha256_16>'``. The base directory
    ITSELF is fingerprinted too (round-8: an EMPTY directory such as
    ~/.config/crewai yielded zero entries otherwise, i.e. zero coverage).
    Deliberately NOT fingerprinted:
      * directory MTIMES — a transient write-test bumps them on every crewai
        import, which is not a footprint change (existence is);
      * SQLite ``-wal`` / ``-shm`` sidecars — volatile by design (created and
        removed by ANY writer, and even by a read-only open in some modes), so
        including them would make the criterion flaky, which is worse than
        narrow. The database FILE itself is hashed, so content writes are
        still caught;
      * ``TRANSIENT_FILENAMES`` (``.crewai_write_test``, R9-6) — same
        rationale as the sidecars: self-annihilating crewai write-test; each
        sighting is reported as a NOTE, never silently."""
    if not base.is_dir():
        return
    out[prefix] = "dir"
    for p in sorted(base.rglob("*")):
        rel = f"{prefix}/{p.relative_to(base).as_posix()}"
        if p.is_dir():
            out[rel] = "dir"
            continue
        if p.name.endswith(("-wal", "-shm")):
            continue
        if p.name in TRANSIENT_FILENAMES:
            if notes is not None:
                notes.append(f"[J2(c)] NOTE: transient file '{rel}' seen and EXCLUDED "
                             f"from fingerprints (crewai creates and self-deletes it; "
                             f"R9-6 named exclusion)")
            continue
        try:
            out[rel] = f"{p.stat().st_size}:{_fp16(p)}"
        except OSError as exc:
            problems.append(f"{rel}: unreadable ({type(exc).__name__}: {exc})")


def _crewai_footprints() -> tuple[str, dict, list[str]]:
    """J2(c) round-8: CONTENT fingerprints of the two out-of-project areas this
    platform can cause: ``%LOCALAPPDATA%\\CrewAI`` (the appdirs base the
    redirect is supposed to keep empty of OUR data) and
    ``%USERPROFILE%\\.config\\crewai`` (hardcoded inside crewai; disclosed
    boundary, round-8 C). Returns (skip_reason, fingerprints, problems)."""
    problems: list[str] = []
    notes: list[str] = []
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return "LOCALAPPDATA not set", {}, problems, notes
    out: dict[str, str] = {}
    _fingerprint_tree(Path(local) / "CrewAI", "LOCALAPPDATA/CrewAI", out, problems, notes)
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    _fingerprint_tree(Path(home) / ".config" / "crewai",
                      "USERPROFILE/.config/crewai", out, problems, notes)
    return "", out, problems, notes


def check_clean(root: Path, log_dir_tmp: Path | None, run_tools: bool) -> tuple[list[str], list[str]]:
    """J2: zero-pollution check. Full mode (real repo): (a) tools run with a
    temp log-dir + mutation_probe -> status must be COMPLETELY empty; (b) a
    default-log-dir layercheck run may only add untracked files under
    reports/layercheck/. Fixture mode (--root given): only the scoped (b)
    assertion runs against the copy's git status.

    Returns (problems, notes). Notes are VISIBLE stdout lines that are
    neither PASS nor FAIL — used for the J2(c) SKIP and the name-set
    summary (K3)."""
    problems: list[str] = []
    notes: list[str] = []
    pre = _git_status(root)
    skip, names_before = _crewai_storage_names()
    fp_skip, fps_before, fp_problems, fp_notes = _crewai_footprints()
    problems += fp_problems
    notes += fp_notes
    if run_tools:
        # the pre-cleanliness baseline only applies to the REAL repo flow,
        # where we measure pollution caused by the tools themselves; fixture
        # copies (git init, nothing committed) are intentionally "dirty"
        # K4: "git status 为空"是错判据 —— 前一轮合法写入的 ?? reports/layercheck/
        # 时间戳日志不算污染。基线守卫同样范围化:只有范围外改动(M/D/R 或范围外 ??)
        # 才算"不洁";合法残留进入 before 快照参与"无新增"比较。
        pre_violations = _status_violations_scoped(pre)
        if pre_violations:
            return [f"baseline not clean (scoped) — cannot evaluate pollution:\n  "
                    + "\n  ".join(pre_violations)], notes

        py = sys.executable
        tool = Path(__file__).resolve().parent
        tmp_log = Path(log_dir_tmp) if log_dir_tmp else Path(tempfile.mkdtemp(prefix="audit_j2a_logs_"))
        proc = _run([py, tool / "layercheck.py", "--scope", "unit", "--log-dir", tmp_log], cwd=root)
        if proc.returncode != 0:
            problems.append(f"J2(a): layercheck (temp log-dir) itself failed: exit={proc.returncode}")

        proc = _run([py, tool / "mutation_probe.py", "--root", str(root)], cwd=root)
        if proc.returncode != 0:
            problems.append(f"J2(a): mutation_probe itself failed: exit={proc.returncode}")

        lines_a = _git_status(root)
        new_after_a = sorted(set(lines_a) - set(pre))
        if new_after_a:
            problems.append("J2(a): NEW pollution after temp-log-dir tool runs:\n  "
                            + "\n  ".join(new_after_a))

        proc = _run([py, tool / "layercheck.py", "--scope", "unit"], cwd=root)
        if proc.returncode != 0:
            problems.append(f"J2(b): layercheck (default log-dir) itself failed: exit={proc.returncode}")

    lines_b = _git_status(root)
    violations = _status_violations_scoped(lines_b)
    if violations:
        problems.append("J2(b): out-of-scope pollution (must be '??' under reports/layercheck/ only):\n  "
                        + "\n  ".join(violations))

    # J2(c): out-of-project footprint, machine-checked at TWO layers (K2/K3),
    # round-8: CONTENT level (size+sha256), not just names:
    # (i) in-run: name-set AND fingerprint equality before vs after the tool
    #     runs — catches residue CREATED BY THE TOOLS;
    # (ii) cross-run: current state vs the PERSISTED baseline
    #     (runs/crewai_names_state.json) — catches dirs/files that appeared or
    #     CHANGED outside any tool run (criterion 2's "new -> run criterion ->
    #     name it" semantics). Legal historical residue lives in the baseline;
    #     only NEW names, ADDED entries and CHANGED bytes fail. Coverage:
    #     %LOCALAPPDATA%\CrewAI + %USERPROFILE%\.config\crewai. Read-only
    #     listing (K3); SKIP must be visible, never a silent PASS.
    if skip:
        notes.append(f"[J2(c)] SKIP — {skip}")
    elif not run_tools:
        notes.append("[J2(c)] skipped in fixture mode (--root): the name-set "
                     "baseline is machine-global")
    else:
        _, names_after = _crewai_storage_names()
        _, fps_after, fp_after_problems, fp_after_notes = _crewai_footprints()
        problems += fp_after_problems
        notes += fp_after_notes

        # (i) in-run, CONTENT level: a tool run must not change one byte of the
        # out-of-project footprint. Name-set equality alone is blind to this
        # (measured, round-8: a 139 KB kickoff DB was rewritten while the name
        # set stayed 9 -> 9).
        in_run_new = sorted(set(names_after) - set(names_before))
        if in_run_new:
            problems.append(f"[J2(c)] tool run created new out-of-project dirs: {in_run_new}")
        in_run_changed = sorted(k for k in set(fps_before) & set(fps_after)
                                if fps_before[k] != fps_after[k])
        in_run_added = sorted(set(fps_after) - set(fps_before))
        if in_run_changed or in_run_added:
            problems.append(f"[J2(c)] tool run changed out-of-project CONTENT: "
                            f"changed={in_run_changed} added={in_run_added}")
        if not in_run_new and not in_run_changed and not in_run_added:
            home = os.environ.get("USERPROFILE") or "~"
            notes.append(f"[J2(c)] out-of-project footprint: names before == after "
                         f"({len(names_after)} entries); content fingerprints before == after "
                         f"({len(fps_after)} entries) [dirs="
                         f"{Path(os.environ['LOCALAPPDATA']) / 'CrewAI'}, "
                         f"{Path(home) / '.config' / 'crewai'}]")

        # (ii) cross-run, vs the PERSISTED baseline: catches anything that
        # appeared or changed outside a tool run (criterion 2's
        # "new -> run criterion -> name it" semantics). Legal historical
        # residue lives in the baseline; only NEW/CHANGED entries fail.
        persisted = _load_crewai_state(root)
        if persisted is None or "fingerprints" not in persisted:
            _save_crewai_state(root, names_after, fps_after)
            once = ("recorded" if persisted is None
                    else "upgraded from name-only to content-level")
            notes.append(f"[J2(c)] baseline {once} "
                         f"({len(names_after)} names, {len(fps_after)} fingerprints)")
        else:
            base_names = persisted.get("names") or []
            base_fps = persisted.get("fingerprints") or {}
            # R10-3 (residual R5): a covered area disappearing WHOLESALE used
            # to be absorbed silently — removed entries only surfaced inside
            # a note. A legitimate cleanup may empty an area, but the loss of
            # COVERAGE must be visible: WARN, do not FAIL (K3: absorption is
            # never silent; the baseline is re-recorded below as by design).
            _area_paths = {
                "LOCALAPPDATA/CrewAI":
                    Path(os.environ.get("LOCALAPPDATA", "")) / "CrewAI",
                "USERPROFILE/.config/crewai":
                    Path(os.environ.get("USERPROFILE")
                         or os.path.expanduser("~")) / ".config" / "crewai",
            }
            for _prefix, _area in _area_paths.items():
                if _prefix in base_fps and _prefix not in fps_after:
                    notes.append(f"[J2(c)] WARN: covered area missing: {_area} "
                                 f"(was in baseline; removals are absorbed by design)")
            new_vs_baseline = sorted(set(names_after) - set(base_names))
            if new_vs_baseline:
                problems.append(f"[J2(c)] %LOCALAPPDATA%\\CrewAI name-set changed vs baseline: "
                                f"new={new_vs_baseline} (baseline has {len(base_names)} entries)")
            changed = sorted(k for k in set(base_fps) & set(fps_after)
                             if base_fps[k] != fps_after[k])
            added = sorted(set(fps_after) - set(base_fps))
            removed = sorted(set(base_fps) - set(fps_after))
            if changed:
                problems.append("[J2(c)] out-of-project content CHANGED vs baseline "
                                "(same path, different bytes): "
                                + str([(k, base_fps[k], fps_after[k]) for k in changed]))
            if added:
                problems.append(f"[J2(c)] out-of-project footprint ADDED vs baseline: {added}")
            if not (new_vs_baseline or changed or added):
                notes.append(f"[J2(c)] vs persisted baseline ({len(base_names)} names, "
                             f"{len(base_fps)} fingerprints): no new or changed entries"
                             + (f"; absorbed removals={removed}" if removed else ""))
                # absorb legitimate removals; NEVER absorb failures (K4 spirit)
                _save_crewai_state(root, names_after, fps_after)
    return problems, notes


# -- J3 ----------------------------------------------------------------------


def check_failure(root: Path) -> list[str]:
    """J3: failures must be loud (K5) — positive fixtures must exit non-zero
    with non-empty stderr; the injected anchor fault must abort with ABORT."""
    problems: list[str] = []
    py = sys.executable
    tool = Path(__file__).resolve().parent

    # (a) impossible log-dir: a subpath under an EXISTING FILE
    impossible = root / "README.md" / "not" / "a" / "dir"
    proc = _run([py, tool / "layercheck.py", "--scope", "unit", "--log-dir", impossible], cwd=root)
    if proc.returncode == 0:
        problems.append("J3(a): layercheck exited 0 despite an impossible --log-dir")
    if not proc.stderr.strip():
        problems.append("J3(a): layercheck failed SILENTLY (stderr empty) — K5 violation")

    # (b) anchor missing in a copy -> mutation_probe must ABORT loudly (K5).
    # 补遗 C: the anchor constant comes from mutation_probe.DEFECTS itself
    # (single source of truth), and the replacement must be PROVEN effective —
    # a silently ineffective fixture reports FIXTURE INVALID, never
    # "not aborted" (K5 wording).
    with tempfile.TemporaryDirectory(prefix="audit_j3b_") as td:
        copy = Path(td)
        shutil.copytree(root / "core", copy / "core")
        shutil.copytree(root / "tests" / "unit", copy / "tests" / "unit")
        crdt = copy / "core" / "crdt.py"
        original = crdt.read_text(encoding="utf-8")
        modified = original.replace(MUTATION_ANCHOR, "")
        if modified == original:
            problems.append("FIXTURE INVALID: anchor not found in core/crdt.py — "
                            "mutation_probe anchor constant does not match the copy")
            return problems
        crdt.write_text(modified, encoding="utf-8")
        proc = _run([py, tool / "mutation_probe.py", "--root", str(copy)], cwd=root)
        if proc.returncode == 0:
            problems.append("J3(b): mutation_probe exited 0 despite a missing injection anchor")
        if "ABORT" not in proc.stderr:
            problems.append("J3(b): mutation_probe abort message not on stderr (K5 violation)")

    # (c) 补遗 B2 fixture: layercheck --import-all --root must fail LOUDLY on a
    # broken core copy (B1: summary on stderr) and pass on the healthy tree.
    with tempfile.TemporaryDirectory(prefix="audit_j3c_") as td:
        copy = Path(td)
        for d in ("core", "tests", "dsh_bridge", "agent_bridge",
                    "agents", "evolver", "crews", "configs", "data", "prompts"):
            src = root / d
            if src.is_dir():
                shutil.copytree(src, copy / d,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        broken = copy / "core" / "crdt.py"
        broken.write_text(broken.read_text(encoding="utf-8") + "\ndef _broken(:\n",
                          encoding="utf-8")
        proc = _run([py, tool / "layercheck.py", "--scope", "unit", "--import-all",
                     "--root", str(copy)], cwd=root)
        if proc.returncode == 0:
            problems.append("J3(c): layercheck exited 0 on a broken core copy")
        if "LAYERCHECK FAIL" not in proc.stderr:
            problems.append("J3(c): failure summary not on stderr (B1 violation)")
        proc = _run([py, tool / "layercheck.py", "--scope", "unit", "--import-all"], cwd=root)
        if proc.returncode != 0:
            problems.append("J3(c) negative: layercheck failed on the healthy repository")
    return problems


# -- L (R17-BCFIX2 B-1) -------------------------------------------------------


def check_liveness(root: Path, since_hours: float,
                   now: float | None = None) -> tuple[list[str], list[str]]:
    """B1 (--check liveness): stalled NON-TERMINAL runs must be LOUD.

    Liveness is derived from EXISTING artifacts by
    ``agent_bridge.recovery.derive_liveness`` — the same read-only machinery
    the bounded recovery (B-2) and the unified view (B-3) use: no shadow
    state, nothing caller-supplied. A run is a violation when, inside the
    window: its status is non-terminal AND derive_liveness finds NO live
    path AND the recovery ledger records no owner action yet
    (escalated/action_open are already visible escalations -> notes;
    silently-stalled runs with recovery_state none/queued are violations).

    L7 window rule (--liveness-since-hours, default 24h): only runs whose
    artifact activity (max mtime of state/trace/recovery) is INSIDE the
    window are assessed; older runs are counted visibly as outside_window,
    so a legacy backlog cannot paint the first run red.

    Returns (problems, notes); notes are visible stdout (K3)."""
    import time as _time
    problems: list[str] = []
    notes: list[str] = []
    now = now or _time.time()
    runs_root = root / "runs"
    if not runs_root.is_dir():
        return problems, ["[L] no runs/ directory — nothing to assess"]

    # agent_bridge lives at the repo root (this script's dir is sys.path[0]
    # when run as a subprocess); data comes from `root`, code from PROJECT_ROOT
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    import agent_bridge.recovery as rec_mod
    orig_run_dir = rec_mod.run_dir
    rec_mod.run_dir = lambda rid: runs_root / rid
    try:
        cutoff = now - since_hours * 3600.0
        assessed = outside = 0
        for rd in sorted(runs_root.iterdir()):
            if not rd.is_dir():
                continue  # runs/ also holds file-level artifacts (counters)
            arts = [rd / "state.json", rd / "trace.jsonl", rd / "recovery.json"]
            present = [p for p in arts if p.exists()]
            if not present:
                continue  # not a run directory
            last_activity = max(p.stat().st_mtime for p in present)
            if last_activity < cutoff:
                outside += 1
                continue
            assessed += 1
            liv = rec_mod.derive_liveness(rd.name, now=now)
            if liv["status"] not in rec_mod.NON_TERMINAL:
                continue  # terminal: nothing to escalate
            if not liv["stalled"]:
                continue  # alive: nothing to report
            rec_state = liv["recovery_state"]
            if rec_state in ("escalated", "action_open"):
                notes.append(f"[L] run {rd.name}: stalled, already {rec_state} "
                             f"(visible escalation, not a new violation)")
                continue
            problems.append(f"run {rd.name}: non-terminal status="
                            f"{liv['status']!r} with NO live path (stalled) "
                            f"and no recorded recovery action — recovery "
                            f"state={rec_state!r}")
        notes.append(f"[L] assessed {assessed} run(s) inside the "
                     f"{since_hours}h window; {outside} outside_window "
                     f"(skipped by the L7 window rule)")
    finally:
        rec_mod.run_dir = orig_run_dir
    return problems, notes


# -- N2 (R17): --check redaction -----------------------------------------------


def _load_redaction_allows(root: Path) -> list[str]:
    """Committed default allowlist (globs, repo-relative posix) for artifacts
    that legitimately contain unmasked rule-like forms (e.g. docs quoting
    broken-up examples). Extendable per invocation with --redaction-allow."""
    default = root / "tests" / "tools" / "redaction_allow.json"
    if default.exists():
        try:
            return json.loads(default.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def check_redaction(root: Path, extra_allows: list[str] | None = None,
                    now: float | None = None) -> tuple[list[str], list[str]]:
    """N2 (--check redaction): no unmasked sensitive form may sit in any
    persisted artifact.

    Scope (per the work order): runs/<id>/{trace.jsonl,health.json,state.json,
    run_meta.json,recovery.json} + reports/*.md (non-recursive). Detection
    uses the SAME deterministic rule set as core/redact.py and SKIPS
    already-masked spans, so normal masked runs never self-flag. Violations
    report file + kind + count -- NEVER the matched content.
    Quarantine copies under runs/_quarantine/ are outside the scope by
    construction (depth-3 paths): they hold evidence moved out precisely
    because it contains plaintext.
    """
    import time as _time
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))  # script-run: repo root absent
    from core.redact import RULES_VERSION, _RULES, _mask_spans

    problems: list[str] = []
    notes: list[str] = []
    allows = _load_redaction_allows(root) + list(extra_allows or [])
    import fnmatch

    def _allowed(rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, g) for g in allows)

    targets: list[Path] = []
    runs_root = root / "runs"
    if runs_root.is_dir():
        for rd in sorted(runs_root.iterdir()):
            if not rd.is_dir():
                continue
            for fname in ("trace.jsonl", "health.json", "state.json",
                          "run_meta.json", "recovery.json"):
                fp = rd / fname
                if fp.exists():
                    targets.append(fp)
    reports_root = root / "reports"
    if reports_root.is_dir():
        targets.extend(sorted(reports_root.glob("*.md")))

    scanned_files = scanned_db_rows = flagged_hits = 0
    counts_by_kind: dict[str, int] = {}
    for fp in targets:
        rel = fp.relative_to(root).as_posix()
        if _allowed(rel):
            notes.append(f"[R] NOTE: {rel} skipped by redaction allowlist")
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned_files += 1
        spans = _mask_spans(text)
        for kind, rx in _RULES:
            n = 0
            for m in rx.finditer(text):
                s, e = m.span()
                if any(s < pe and ps < e for ps, pe in spans):
                    continue  # already masked: not a violation
                n += 1
            if n:
                # name the file and the kind + count; never print the content
                problems.append(f"{rel}: kind={kind} count={n} "
                                f"(unmasked form present - quarantine or "
                                f"re-run with redaction on)")
                flagged_hits += n
                counts_by_kind[kind] = counts_by_kind.get(kind, 0) + n

    # N2-FIX1 (review F1): the runs registry (SQLite) carries text columns
    # too -- scan runs.task and ops.value with the same rules; report only
    # table/column/kind/count, never the stored content.
    db = runs_root / "collab.sqlite3"
    if db.is_file():
        import sqlite3 as _sq
        try:
            conn = _sq.connect(f"file:{db}?mode=ro", uri=True)
            try:
                for table, col in (("runs", "task"), ("ops", "value")):
                    try:
                        rows = conn.execute(
                            f"SELECT {col} FROM {table}").fetchall()
                    except _sq.Error:
                        notes.append(f"[R] NOTE: {table}.{col} not readable "
                                     f"(table absent?) -- skipped")
                        continue
                    db_counts: dict[str, int] = {}
                    for (text,) in rows:
                        if not isinstance(text, str) or not text:
                            continue
                        scanned_db_rows += 1
                        spans = _mask_spans(text)
                        for kind, rx in _RULES:
                            n = 0
                            for m in rx.finditer(text):
                                s, e = m.span()
                                if any(s < pe and ps < e
                                       for ps, pe in spans):
                                    continue
                                n += 1
                            if n:
                                db_counts[kind] = db_counts.get(kind, 0) + n
                    for kind, n in sorted(db_counts.items()):
                        problems.append(
                            f"runs/collab.sqlite3#{table}.{col}: "
                            f"kind={kind} count={n} (unmasked form present "
                            f"in DB text column)")
                        flagged_hits += n
            finally:
                conn.close()
        except _sq.Error as exc:
            problems.append(f"runs/collab.sqlite3: unreadable ({exc})")
    # summary note lives AFTER the DB section so DB hits are counted too;
    # counters are explicit (files vs db_rows) and flags are reported as
    # hit(s) (a single file/row can match several kinds).
    notes.append(f"[R] scanned files={scanned_files}, "
                 f"db_rows={scanned_db_rows} with rules {RULES_VERSION}; "
                 f"{flagged_hits} hit(s) flagged; "
                 f"allowlist entries in effect: {len(allows)}")
    return problems, notes


# -- entry -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check",
                        choices=["frozen", "clean", "failure", "liveness",
                                 "redaction", "all"],
                        default="all")
    parser.add_argument("--root", default=None,
                        help="operate on a COPY (fixtures); default: this repository root")
    parser.add_argument("--log-dir-tmp", default=None,
                        help="J2(a): where layercheck's temp logs go (default: a system temp dir)")
    parser.add_argument("--liveness-since-hours", type=float, default=24.0,
                        help="L7 window rule: only runs active within the last "
                             "N hours are assessed by --check liveness (default 24)")
    parser.add_argument("--redaction-allow", action="append", default=None,
                        metavar="GLOB",
                        help="N2: extra repo-relative glob(s) excluded from "
                             "--check redaction (repeatable; the committed "
                             "default list is tests/tools/redaction_allow.json)")
    parser.add_argument("--forget-crewai-baseline", action="store_true",
                        help="N4: delete the persisted J2(c) CrewAI name baseline "
                             "(use after manually cleaning %LOCALAPPDATA%\\CrewAI)")
    parser.add_argument("--yes", action="store_true",
                        help="N4: confirm the forget action (without it the "
                             "names to be absorbed are only printed)")
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else PROJECT_ROOT

    if args.forget_crewai_baseline:
        state = _crewai_name_state_path(root)
        if not state.exists():
            print("J2(c) baseline: none to delete")
            return 0
        state_payload = _load_crewai_state(root) or {}
        names = state_payload.get("names", [])
        fps = state_payload.get("fingerprints", {})
        print(f"J2(c) forget: the following {len(names)} name(s) and "
              f"{len(fps)} content fingerprint(s) will be absorbed into the "
              f"fresh baseline on the next --check clean:")
        for n in names:
            print(f"  - {n}")
        if not args.yes:
            # N4: never legitimize existing pollution silently — require an
            # explicit confirmation flag
            print("J2(c) forget: NOT deleted — re-run with --yes to confirm")
            return 0
        state.unlink()
        print(f"J2(c) baseline deleted: {state}")
        return 0
    problems: list[str] = []

    if args.check in ("frozen", "all"):
        problems += [f"[J1] {p}" for p in check_frozen(root)]
    if args.check in ("clean", "all"):
        # fixture mode (--root given, not the real repo): the copy has no
        # committed baseline, so only the scoped (b) assertion can run
        fixture_mode = args.root is not None
        j2_problems, j2_notes = check_clean(root, args.log_dir_tmp, run_tools=not fixture_mode)
        for note in j2_notes:
            print(note)  # J2(c) SKIP / name-set summary — visible stdout (K3)
        problems += [f"[J2] {p}" for p in j2_problems]
    if args.check in ("failure", "all"):
        problems += [f"[J3] {p}" for p in check_failure(root)]
    if args.check in ("liveness", "all"):
        l_problems, l_notes = check_liveness(root, args.liveness_since_hours)
        for note in l_notes:
            print(note)  # window-rule counts / visible escalations (K3)
        problems += [f"[L] {p}" for p in l_problems]
    if args.check in ("redaction", "all"):
        r_problems, r_notes = check_redaction(root, args.redaction_allow)
        for note in r_notes:
            print(note)
        problems += [f"[R] {p}" for p in r_problems]

    if problems:
        print("AUDIT INVARIANTS: FAIL", file=sys.stderr)
        for p in problems:
            print(p, file=sys.stderr)
        return 1
    if args.check in ("frozen", "all"):
        manifest = json.loads((root / "reports" / "layercheck" / "FROZEN_MANIFEST.json")
                              .read_text(encoding="utf-8"))
        for rel, expected in manifest["files"].items():
            print(f"OK {rel} sha256_16_norm={expected}")
        print(f"{len(manifest['files'])} frozen files OK")
    print("AUDIT INVARIANTS: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
