"""H1 archive-export: post-run snapshot to the personal memory drop zone.

Task-book: D:\\dsh-work\\2026-10-10\\记忆库联通自改进闭环方案_V2_定稿.md
(sha256_16=4343828b54bd4985). V2.1 review amendments applied: two-stage
masking (mask FIRST, then self-check the assembled text), error snapshots
carry ``error{type, detail}``, loud failure (never silent).

Contract (V2 §3.1/§3.5):
- ``export_snapshot`` NEVER raises: callers may invoke it bare on the run
  tail and inside an ``except`` branch without changing run semantics.
- Switch: env ``ARCHIVE_EXPORT_ENABLED`` -- strict ``"1"`` (V2 M6). Off =>
  zero writes, zero reads, zero events, zero log noise.
- Drop dir: env ``ARCHIVE_EXPORT_DIR``, default
  ``D:\\ai-memory-drop\\workbuddy\\inbox``. This is the ONLY module allowed
  to write outside PROJECT_ROOT -- an owner-approved, env-gated C1
  exception (V2 §3.4 / M2).
- Two-stage masking (V2.1 必改一):
    stage 1  ``redact_strings_deep`` over the assembled payload -- same
             rules, same call shape as the state.json pre-persistence
             redaction. The runner hands us the UNREDACTED in-memory final
             ("the in-memory final is untouched for the caller"), so
             masking here is mandatory, not optional.
    stage 2  re-scan the FULL assembled .md text with ``redact()``; any
             non-empty counts means a field BYPASSED the stage-1 mask =>
             REJECT the write and fail loudly (archive_export_error.json +
             ``archive_export_failed`` event). Stage 2 runs regardless of
             REDACTION_ENABLED: the drop zone is an external trust boundary.
  ``core/redact.py`` is only ever CALLED here -- its rules and
  RULES_VERSION are frozen (V2 §6 red line).
- sha256/sha16 hash the canonical snapshot CORE (every field except
  ``snapshot_id`` / ``sha256`` / ``sha16`` / ``exported_at``) so a
  same-content re-export is deterministic; ``snapshot_id = <run_id>-<sha16>``
  is the A7 idempotence key. ``exported_at`` is informational only.
- Digest policy (V2 M4): head 20 + tail 60 events, plus EVERY event whose
  type is in DIGEST_KEEP_TYPES; each item clamped to 200 chars AFTER mask
  expansion (a clamped tail can only cut a mask fingerprint, never leak
  plaintext); ``truncated`` flags items whose RAW render was cut; error
  runs append one synthesized ``run_error`` termination item.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from core.paths import PROJECT_ROOT, run_dir
from core.redact import RULES_VERSION, redact, redact_strings_deep

SCHEMA_VERSION = 1
PRODUCER = "multi_agent_collab"
SOURCE_TRUST = "platform"
DEFAULT_DROP_DIR = r"D:\ai-memory-drop\workbuddy\inbox"

DIGEST_HEAD = 20
DIGEST_TAIL = 60
DIGEST_MAX_CHARS = 200
DIGEST_KEEP_TYPES = frozenset({
    "plan", "assignment", "arbitrate", "review", "health",
    "run_finished", "run_cancelled", "run_error",
})
TASK_SUMMARY_CHARS = 80
REPORT_SUMMARY_CHARS = 400
ERROR_DETAIL_CHARS = 300

_WS = re.compile(r"\s+")


class ArchiveExportRejection(Exception):
    """Stage-2 self-check found unmasked rule hits; the write is rejected."""

    def __init__(self, message: str, suspected_kinds: dict):
        super().__init__(message)
        self.suspected_kinds = dict(suspected_kinds or {})


class ArchiveExportConflict(Exception):
    """Same-day drop file for the same run exists with DIFFERENT content."""


def export_enabled() -> bool:
    """Strict kill-switch: only the literal ``"1"`` enables (V2 M6)."""
    return os.environ.get("ARCHIVE_EXPORT_ENABLED", "0") == "1"


def export_dir() -> Path:
    return Path(os.environ.get("ARCHIVE_EXPORT_DIR", DEFAULT_DROP_DIR))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ts_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _platform_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()[:12]
    except Exception:
        pass
    return "unknown"


def _render_event(e) -> str:
    try:
        payload = json.dumps(e.payload, ensure_ascii=False, sort_keys=True,
                             default=str)
    except Exception:
        payload = "<unserializable-payload>"
    head = f"#{e.seq} {e.type}"
    if getattr(e, "agent_id", None):
        head += f" [{e.agent_id}]"
    if payload and payload != "{}":
        return f"{head} {payload}"
    return head


def _build_digest(events, exc) -> dict:
    """Head 20 + tail 60 + every kept-type event; raw items clamped to 200."""
    total = len(events)
    by_seq = {e.seq: e for e in events}
    head = events[:DIGEST_HEAD]
    tail = events[max(DIGEST_HEAD, total - DIGEST_TAIL):] \
        if total > DIGEST_HEAD else []
    selected: dict[int, str] = {}
    for e in head:
        selected[e.seq] = "head"
    for e in tail:
        selected.setdefault(e.seq, "tail")
    by_type = 0
    for e in events:
        if e.type in DIGEST_KEEP_TYPES and e.seq not in selected:
            selected[e.seq] = "by_type"
            by_type += 1
    kept_by_reason = {"head": len(head), "tail": len(tail),
                      "by_type": by_type}
    items: list[str] = []
    truncated = False
    for seq in sorted(selected):
        s = _render_event(by_seq[seq])
        if len(s) > DIGEST_MAX_CHARS:
            truncated = True
            s = s[:DIGEST_MAX_CHARS]
        items.append(s)
    if exc is not None:
        items.append(f"synth run_error type={type(exc).__name__} "
                     f"detail={str(exc)[:150]}")
        kept_by_reason["synth"] = 1
    return {"total_events": total, "kept": len(items),
            "truncated": truncated, "kept_by_reason": kept_by_reason,
            "items": items}


def _extract_verdict(final, events):
    if final:
        for key in ("verdict", "review_verdict", "debate_verdict"):
            if final.get(key):
                return final.get(key)
    for e in reversed(events):
        if e.type in ("review", "judge"):
            v = (e.payload or {}).get("verdict")
            if v:
                return v
    return None


def _node_agents(config, events) -> dict:
    out: dict = {}
    for nc in ((config or {}).get("nodes") or []):
        if isinstance(nc, dict) and nc.get("id"):
            out[str(nc["id"])] = nc.get("agent")
    if not out:
        for e in events:
            if e.type == "node_enter":
                node = (e.payload or {}).get("node")
                if node and getattr(e, "agent_id", None):
                    out.setdefault(str(node), e.agent_id)
    return out


def _health_payload(health):
    if health is None:
        return None
    try:
        return {"score": float(health.score), "summary": health.summary,
                "checks": [c.model_dump() for c in health.checks]}
    except Exception:
        return {"score": None,
                "summary": f"<unserializable:{type(health).__name__}>",
                "checks": []}


def _loud_fail(run_id, bus, error: str, stage: str,
               suspected_kinds=None) -> None:
    """M5: never silent. Best-effort marker + event; itself never raises."""
    payload = {"exported_at": _now_iso(), "stage": stage,
               "error": str(error)[:500],
               "suspected_kinds": dict(suspected_kinds or {})}
    try:
        marker = run_dir(run_id) / "archive_export_error.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.parent / (marker.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, marker)
    except Exception:
        pass
    if bus is not None:
        try:
            bus.emit("archive_export_failed", run_id, payload)
        except Exception:
            pass


def _export(*, run_id, session_id, mode, task, events, final, health, bus,
            status, config, exc):
    counts: dict[str, int] = {}

    def _merge(c: dict) -> None:
        for k, v in (c or {}).items():
            counts[k] = counts.get(k, 0) + int(v)

    # ---- stage 1a: mask the task text (full text, before any truncation)
    task_masked, task_counts = redact(task or "")
    _merge(task_counts)
    task_summary = _WS.sub(" ", task_masked).strip()[:TASK_SUMMARY_CHARS]
    task_sha16 = hashlib.sha256((task or "").encode("utf-8")).hexdigest()[:16]

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "session_id": session_id,
        "topology": mode,
        "status": status,
        "producer": PRODUCER,
        "source_trust": SOURCE_TRUST,
        "platform_commit": _platform_commit(),
        "finished_at": _ts_iso(events[-1].ts) if events else None,
        "node_agents": _node_agents(config, events),
        "verdict": _extract_verdict(final, events),
        "health": _health_payload(health),
        "audit_five_checks": None,
        "audit_five_checks_note": ("five checks are an out-of-band CLI "
                                   "criterion; not available at export "
                                   "time (V2 M4)"),
        "trace_digest": _build_digest(events, exc),
        "task_summary": task_summary,
        "task_sha16": task_sha16,
        "redaction": {"rules_version": RULES_VERSION,
                      "counts_by_kind": {}},
    }
    if exc is not None:
        snapshot["error"] = {"type": type(exc).__name__,
                             "detail": str(exc)[:ERROR_DETAIL_CHARS]}
    if final is not None:
        snapshot["final_status"] = final.get("status")
        report = final.get("final_report")
        if report:
            snapshot["final_report_summary"] = _WS.sub(
                " ", str(report)).strip()[:REPORT_SUMMARY_CHARS]

    # ---- stage 1b: mask the WHOLE payload in one deep pass (same call as
    # the state.json pre-persistence redaction). Counts captured ONCE here.
    masked, deep_counts = redact_strings_deep(snapshot)
    _merge(deep_counts)
    # keep the V2 length promise AFTER mask expansion
    items = masked["trace_digest"]["items"]
    masked["trace_digest"]["items"] = [i[:DIGEST_MAX_CHARS] for i in items]
    masked["redaction"]["counts_by_kind"] = dict(sorted(counts.items()))

    # ---- idempotence key + content hash (A7). Hashed CORE excludes
    # snapshot_id / sha256 / sha16 / exported_at.
    exported_at = _now_iso()
    core = {k: v for k, v in masked.items()
            if k not in ("snapshot_id", "sha256", "sha16", "exported_at")}
    core_json = json.dumps(core, ensure_ascii=False, sort_keys=True,
                           default=str)
    sha = hashlib.sha256(core_json.encode("utf-8")).hexdigest()
    sha16 = sha[:16]
    snapshot_id = f"{run_id}-{sha16}"
    masked["sha256"] = sha
    masked["sha16"] = sha16
    masked["snapshot_id"] = snapshot_id
    masked["exported_at"] = exported_at
    file_json = json.dumps(masked, ensure_ascii=False, indent=2,
                           sort_keys=True, default=str)

    # ---- assemble the .md drop file
    date = time.strftime("%Y-%m-%d")
    hs = masked.get("health") or {}
    human = (f"platform run snapshot status={status} topology={mode} "
             f"verdict={masked.get('verdict')} "
             f"health_score={hs.get('score')} "
             f"events={masked['trace_digest']['total_events']} "
             f"masked_kinds={sorted(counts)}; detail in the JSON block.")
    frontmatter = (
        "---\n"
        f"id: {snapshot_id}\n"
        "type: session-draft\n"
        "agent: platform\n"
        f"date: {date}\n"
        f"task: \"@task#{str(run_id)[:8]}:{task_summary}\"\n"
        "status: draft\n"
        f"run_id: {run_id}\n"
        f"redaction_rules: {RULES_VERSION}\n"
        f"sha256: {sha}\n"
        "---\n"
    )
    full_md = (frontmatter + "\n" + human + "\n\n```json\n"
               + file_json + "\n```\n")

    # ---- stage 2: self-check the ASSEMBLED text (V2.1 必改一). Non-empty
    # counts means a field BYPASSED the stage-1 mask => reject + loud fail.
    _recheck, recheck_counts = redact(full_md)
    if recheck_counts:
        raise ArchiveExportRejection(
            "self-check found unmasked hits (field bypassed the mask): "
            f"{sorted(recheck_counts)}", recheck_counts)

    # ---- write: idempotent on snapshot_id, conflict => loud fail (A7)
    drop_dir = export_dir()
    target = drop_dir / f"{date}-{run_id}.md"
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if f"id: {snapshot_id}\n" in existing:
            return target, False          # same run + same content => no-op
        raise ArchiveExportConflict(
            f"drop file exists with different content: {target.name}")
    drop_dir.mkdir(parents=True, exist_ok=True)
    tmp = drop_dir / (target.name + ".tmp")
    tmp.write_text(full_md, encoding="utf-8")
    os.replace(tmp, target)
    return target, True


def export_snapshot(*, run_id, session_id, mode, task, events, final, health,
                    bus=None, status="done", config=None, exc=None):
    """Export a run snapshot to the drop zone. NEVER raises (V2 §3.1).

    Returns the drop file Path, or None when the switch is off / the export
    was rejected / nothing new was written (no-op re-export returns the
    existing path). A loud-failure marker (archive_export_error.json) plus
    an ``archive_export_failed`` event accompany every rejection.
    """
    if not export_enabled():
        return None
    try:
        path, created = _export(run_id=run_id, session_id=session_id,
                                mode=mode, task=task,
                                events=list(events or []), final=final,
                                health=health, bus=bus, status=status,
                                config=config, exc=exc)
        if created and bus is not None:
            try:
                bus.emit("archive_export_done", run_id,
                         {"file": path.name})
            except Exception:
                pass
        return path
    except Exception as inner:
        suspected = getattr(inner, "suspected_kinds", None)
        try:
            _loud_fail(run_id, bus, f"{type(inner).__name__}: {inner}",
                       stage="export", suspected_kinds=suspected)
        except Exception:
            pass
        return None
