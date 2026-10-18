"""Health report persistence: runs/<run_id>/health.json (C13/D8)."""

from __future__ import annotations

import json
from pathlib import Path

from core.errors import CollabError
from core.paths import run_dir, runs_dir
from core.redact import (RULES_VERSION, record_run_redaction,
                         redact_strings_deep, redaction_enabled)
from core.schemas import HealthReport


def write_health(report: HealthReport) -> Path:
    """Write health.json for the run.

    R17-FLAKY-1 FIX (reviewer F-1): the previous "min-unused-generation"
    version had a NO-OP pairing check (empty pass), which allowed health
    generations to be created with ZERO trace generations. This version
    restores the N5 GUARDED rotation: if health.json already exists AND
    the target rotated name also exists, it raises CollabError (loud).
    The uuid unique run_id (also FLAKY-1) makes this conflict vanishingly
    rare in practice; the guard is there for the same-run_id edge case.

    GC: keeps at most GC_KEEP generations per run directory.
    """
    rd = Path(run_dir(report.run_id))
    rd.mkdir(parents=True, exist_ok=True)
    path = rd / "health.json"

    if path.exists():
        # N5 guarded rotation: rotate to the next trace generation index
        gens = [int(p.name.rsplit(".", 1)[1]) for p in rd.glob("trace.jsonl.*")
                if p.name.rsplit(".", 1)[1].isdigit()]
        k = max(gens) if gens else 1
        target = path.with_name(f"health.json.{k}")
        if target.exists():
            raise CollabError(
                f"health rotation conflict: {target.name} already exists - refusing "
                f"to write health.json unpaired with trace generation {k} "
                f"(clean the run dir or use a fresh run_id)")
        path.rename(target)

    # N2: mask rule hits before persistence + embed the rules version
    payload = report.model_dump()
    counts: dict = {}
    if redaction_enabled():
        payload, counts = redact_strings_deep(payload)
        if counts:
            record_run_redaction(rd, counts)
    payload["redaction_rules"] = RULES_VERSION

    # GC: keep at most GC_KEEP generations
    _gc_generations(rd, keep=GC_KEEP)

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


GC_KEEP = 20


def _gc_generations(rd: Path, *, keep: int = GC_KEEP) -> None:
    """Remove oldest rotated generations beyond ``keep``."""
    for prefix in ("trace.jsonl", "health.json"):
        gens = sorted(
            (int(p.name.rsplit(".", 1)[1]), p)
            for p in rd.glob(f"{prefix}.*")
            if p.name.rsplit(".", 1)[1].isdigit())
        while len(gens) > keep:
            gens.pop(0)[1].unlink(missing_ok=True)


def gc_run_dir(run_id: str, *, keep: int = GC_KEEP) -> dict:
    """Public GC entry: clean old generations for a run directory.
    Returns {prefix: {"before": n, "after": n, "removed": n}}."""
    rd = Path(run_dir(run_id))
    result = {}
    for prefix in ("trace.jsonl", "health.json"):
        gens = sorted(
            (int(p.name.rsplit(".", 1)[1]), p)
            for p in rd.glob(f"{prefix}.*")
            if p.name.rsplit(".", 1)[1].isdigit())
        before = len(gens)
        while len(gens) > keep:
            gens.pop(0)[1].unlink(missing_ok=True)
        after = len(gens)
        result[prefix] = {"before": before, "after": after,
                          "removed": before - after}
    return result


def load_health(run_id: str) -> dict | None:
    path = Path(run_dir(run_id)) / "health.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# --- R17-BCFIX3: run-directory cleanup (review criterion 11 fix) -------------
#
# Review finding (BCFIX-2, criterion 11): the one-off cleanup used the policy
# "terminal-or-statusless", which DELETED seven run directories whose state was
# missing/unreadable. The criterion (and the delivery report's own prose)
# require statusless directories to be KEPT so that --check liveness can review
# them; deletion is irreversible. This entry makes the policy explicit and
# testable.

CLEANUP_TERMINAL_STATUSES = frozenset(
    {"done", "cancelled", "error", "dispatched", "concluded"})

CLEANUP_POLICY = ("terminal-only run dirs older than {days} days; "
                  "statusless / unreadable / running / summarized dirs are "
                  "NEVER removed (reserved for --check liveness review)")


def read_run_status(rd: Path) -> tuple[str | None, bool]:
    """Return ``(status, statusless)`` for a run directory.

    ``statusless`` is True when ``state.json`` is absent, unreadable, not a
    JSON object, or carries no ``status`` key -- exactly the class of
    directories the cleanup must never delete."""
    fp = Path(rd) / "state.json"
    if not fp.exists():
        return None, True
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None, True
    if not isinstance(data, dict):
        return None, True
    st = data.get("status")
    if st is None or str(st) == "":
        return None, True
    return str(st), False


def cleanup_run_dirs(root: Path | str | None = None, *, cutoff_days: float = 7.0,
                     apply: bool = False, now: float | None = None,
                     write_manifest: bool = True) -> dict:
    """Clean up TERMINAL run directories older than ``cutoff_days``.

    Safety contract (review criterion 11):
      * only directories whose ``status`` is in :data:`CLEANUP_TERMINAL_STATUSES`
        AND older than ``cutoff_days`` are removable;
      * statusless / unreadable state, ``running`` and ``summarized`` are KEPT;
      * ``apply=False`` (default) is a DRY RUN: nothing is deleted, the manifest
        records what *would* be removed.

    Returns the manifest dict; also writes ``runs/cleanup_manifest_<ts>.json``
    when ``write_manifest`` is True.

    Age basis = the run directory's own mtime. Deliberately conservative: any
    recent touch (for example a ``--check liveness`` assessment writing
    ``recovery.json``) makes a directory look fresh and therefore NOT
    removable, so the policy can only ever under-delete."""
    import time as _time

    runs_root = Path(root) if root is not None else runs_dir()
    now = now or _time.time()
    removed: list[dict] = []
    kept: list[dict] = []
    kept_statusless = 0
    for rd in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        status, statusless = read_run_status(rd)
        age_days = round((now - rd.stat().st_mtime) / 86400.0, 2)
        entry = {"dir": rd.name, "status": status, "statusless": statusless,
                 "age_days": age_days}
        if statusless:
            kept_statusless += 1
            kept.append(entry)
            continue
        if status in CLEANUP_TERMINAL_STATUSES and age_days > cutoff_days:
            removed.append(entry)
        else:
            kept.append(entry)

    manifest = {
        "tool": "R17-BCFIX3 inspector.report.cleanup_run_dirs",
        "policy": CLEANUP_POLICY.format(days=cutoff_days),
        "cutoff_days": cutoff_days,
        "dry_run": not apply,
        "removed_count": len(removed),
        "kept_count": len(kept),
        "kept_statusless": kept_statusless,
        "removed": removed,
    }
    if apply:
        import shutil as _shutil
        for entry in removed:
            _shutil.rmtree(runs_root / entry["dir"], ignore_errors=True)
    if write_manifest:
        ts = _time.strftime("%Y%m%dT%H%M%SZ", _time.gmtime(now))
        mpath = runs_root / f"cleanup_manifest_{ts}.json"
        mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        manifest["manifest_path"] = str(mpath)
    return manifest


# -- R17-BCFIX3 R2 (ROI-1): move-only quarantine for stale statusless dirs ----

QUARANTINE_POLICY = ("move-only quarantine of STALE STATUSLESS run dirs into "
                     "runs/_quarantine/<ts>/<dir>/; nothing is ever deleted; "
                     "restorable via restore_quarantine")


def _sha256_file(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _dir_inventory(rd: Path) -> dict:
    """Per-file sha256 inventory of a run dir: {relpath: sha256}."""
    return {str(p.relative_to(rd)).replace("\\", "/"): _sha256_file(p)
            for p in sorted(rd.rglob("*")) if p.is_file()}


def quarantine_statusless_dirs(root=None, *, stale_after_s: float = 3600.0,
                               apply: bool = False, now: float | None = None,
                               write_manifest: bool = True) -> dict:
    """ROI-1: move stale STATUSLESS run dirs into ``runs/_quarantine/<ts>/``.

    Eligible = ``read_run_status`` says statusless AND the dir LOOKS like a
    run dir (carries at least one of trace.jsonl / health.json /
    recovery.json / run_meta.json -- the same shape rule the ``--check
    liveness`` audit uses, so infrastructure dirs like ``dsh_dispatch/`` or
    test dispatch roots are never touched) AND nothing inside was modified
    within ``stale_after_s`` (a dir being written RIGHT NOW is never
    touched; terminal/running dirs have a readable status and can never
    qualify). The move is a RENAME on the same volume -- zero deletions.

    Every manifest entry records ``file_count`` and per-file sha256 BEFORE
    the move; the inventory is re-verified inside the quarantine copy (a
    mismatch moves the dir back and raises). ``apply=False`` (default) is a
    dry run: candidates are listed, nothing moves.
    """
    import time as _time

    RUN_SHAPE = ("trace.jsonl", "health.json", "recovery.json", "run_meta.json")
    runs_root = Path(root) if root is not None else runs_dir()
    now = now or _time.time()
    qroot = runs_root / "_quarantine"
    ts = _time.strftime("%Y%m%dT%H%M%SZ", _time.gmtime(now))

    candidates: list[dict] = []
    skipped_active = 0
    skipped_with_status = 0
    skipped_not_run_dir = 0
    for rd in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        if rd.name == "_quarantine":
            continue
        status, statusless = read_run_status(rd)
        if not statusless:
            skipped_with_status += 1
            continue
        if not any((rd / a).exists() for a in RUN_SHAPE):
            skipped_not_run_dir += 1   # workspace / fixture dir, not a run
            continue
        mtimes = [p.stat().st_mtime for p in rd.rglob("*")]
        mtimes.append(rd.stat().st_mtime)
        freshness = max(mtimes)
        age_s = now - freshness
        if age_s <= stale_after_s:
            skipped_active += 1        # possibly an ACTIVE run: never touch
            continue
        inventory = _dir_inventory(rd)
        candidates.append({"dir": rd.name, "file_count": len(inventory),
                           "files": inventory,
                           "bytes": sum((rd / rel).stat().st_size
                                        for rel in inventory)})

    manifest = {
        "tool": "R17-BCFIX3 inspector.report.quarantine_statusless_dirs",
        "policy": QUARANTINE_POLICY,
        "stale_after_s": stale_after_s,
        "dry_run": not apply,
        "ts": ts,
        "quarantined_count": len(candidates),
        "skipped_active_statusless": skipped_active,
        "skipped_with_status": skipped_with_status,
        "skipped_not_run_dir": skipped_not_run_dir,
        "quarantined": candidates,
    }
    if apply:
        qts = qroot / ts
        qts.mkdir(parents=True, exist_ok=True)
        moved: list[dict] = []
        for entry in candidates:
            src = runs_root / entry["dir"]
            dst = qts / entry["dir"]
            src.rename(dst)            # MOVE = rename; never delete
            after = _dir_inventory(dst)
            if after != entry["files"]:
                dst.rename(src)        # verify-then-move-back: zero loss
                raise RuntimeError(
                    f"quarantine inventory mismatch for {entry['dir']!r} - "
                    f"moved back, nothing changed")
            moved.append(entry)
        manifest["quarantined"] = moved
        mpath = qts / "manifest.json"
        mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        manifest["manifest_path"] = str(mpath)
    elif write_manifest:
        qroot.mkdir(parents=True, exist_ok=True)
        mpath = qroot / f"quarantine_dryrun_{ts}.json"
        mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        manifest["manifest_path"] = str(mpath)
    return manifest


def restore_quarantine(ts: str, root=None, *,
                       now: float | None = None) -> dict:
    """Inverse op for :func:`quarantine_statusless_dirs`: move every dir of
    ``runs/_quarantine/<ts>/`` back to the runs root, byte-verified against
    the manifest (per-file sha256). A name collision at the destination is a
    LOUD error (nothing is overwritten). The emptied per-dir shells are
    removed (the CONTENT was moved back, not deleted); the manifest.json is
    KEPT in place as the audit record of the restore."""
    runs_root = Path(root) if root is not None else runs_dir()
    qts = runs_root / "_quarantine" / ts
    mpath = qts / "manifest.json"
    if not mpath.exists():
        raise FileNotFoundError(f"no quarantine manifest: {mpath}")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    if manifest.get("dry_run"):
        raise ValueError(f"{ts!r} is a DRY-RUN manifest - nothing to restore")
    restored = []
    for entry in manifest["quarantined"]:
        src = qts / entry["dir"]
        dst = runs_root / entry["dir"]
        if dst.exists():
            raise RuntimeError(f"restore collision: {dst} already exists")
        if not src.exists():
            raise RuntimeError(f"quarantine entry missing: {src}")
        after = _dir_inventory(src)
        if after != entry["files"]:
            raise RuntimeError(
                f"quarantined copy differs from manifest for "
                f"{entry['dir']!r} - restore refused, nothing changed")
        src.rename(dst)
        verified = _dir_inventory(dst)
        if verified != entry["files"]:
            raise RuntimeError(
                f"post-restore verification failed for {entry['dir']!r}")
        restored.append({"dir": entry["dir"], "files": len(verified)})
    for entry in manifest["quarantined"]:
        shell = qts / entry["dir"]
        if shell.is_dir() and not any(shell.iterdir()):
            shell.rmdir()              # emptied by the move-back (no content)
    # manifest.json stays: audit record of what was moved and restored
    return {"ts": ts, "restored_count": len(restored), "restored": restored,
            "verified": True}
