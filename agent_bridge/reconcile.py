"""R17-W7: dual-source audit reconciliation (design v3 §6.4) + orphan sweep.

Two independent checks:

1. **Hash-chain verification** (``verify_chain``): for WB-style audit logs
   (``{id, sequence, prevHash, hash, ...}`` JSONL), verify that
   ``prevHash == previous row's hash`` and ``hash == H(row)`` for every row.
   A break ⇒ ``audit_chain_broken``. The hash function is deterministic
   SHA-256 over the JSON serialisation of the row minus the ``hash`` field
   (CT/RFC 9162 principle: the chain is only tamper-evident if the
   serialisation is deterministic).

2. **Row-level reconciliation** (``reconcile_rows``): compare platform
   op-log counts against backend audit counts for a given run.
   A mismatch ⇒ ``audit_gap``.

3. **Orphan sweep** (``sweep_orphans``): scan for descendant processes whose
   command line matches the platform's dispatch pattern and whose parent is
   no longer alive. Alerts, never auto-kills (D5/R13).

All functions operate on IN-MEMORY data or on files explicitly passed in.
They NEVER touch real WB files or real processes unless the caller provides
them. Tests use synthetic fixtures exclusively.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Sequence


# --- 1 · hash chain verification ---------------------------------------------

def _row_hash(row: dict) -> str:
    """Deterministic SHA-256 over the JSON serialisation of the row minus
    the ``hash`` field (CT/RFC 9162: deterministic serialisation)."""
    payload = {k: v for k, v in row.items() if k != "hash"}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_chain(rows: Sequence[dict]) -> dict:
    """Verify a hash-chained audit log. Returns a result dict:
    ``{chain_ok: bool, broken_at: int | None, reason: str | None,
       rows_checked: int}``
    """
    result = {"chain_ok": True, "broken_at": None, "reason": None,
              "rows_checked": len(rows)}
    prev_hash = None
    prev_seq = None
    for i, row in enumerate(rows):
        # sequence continuity
        seq = row.get("sequence")
        if seq is not None and prev_seq is not None and seq != prev_seq + 1:
            result["chain_ok"] = False
            result["broken_at"] = i
            result["reason"] = f"sequence gap: {prev_seq} → {seq}"
            return result
        # prevHash linkage
        if prev_hash is not None and row.get("prevHash") != prev_hash:
            result["chain_ok"] = False
            result["broken_at"] = i
            result["reason"] = (f"prevHash mismatch at row {i}: "
                                f"expected {prev_hash[:16]}…, "
                                f"got {str(row.get('prevHash'))[:16]}…")
            return result
        # hash integrity
        expected_hash = _row_hash(row)
        if row.get("hash") != expected_hash:
            result["chain_ok"] = False
            result["broken_at"] = i
            result["reason"] = (f"hash mismatch at row {i}: "
                                f"stored {str(row.get('hash'))[:16]}…, "
                                f"computed {expected_hash[:16]}…")
            return result
        prev_hash = row.get("hash")
        prev_seq = seq
    return result


def load_chain(path: Path) -> list[dict]:
    """Load a JSONL hash-chain file. Each line is a JSON object."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# --- 2 · row-level reconciliation ---------------------------------------------

def reconcile_rows(platform_ops: list[dict], backend_events: list[dict],
                   *, run_id: str) -> dict:
    """Compare platform op-log entries against backend audit events for a
    given run. Returns ``{run_id, platform_count, backend_count,
    gap: bool, detail: str | None}``.
    """
    p_count = sum(1 for op in platform_ops if op.get("run_id") == run_id)
    b_count = sum(1 for ev in backend_events if ev.get("run_id") == run_id)
    gap = p_count != b_count
    detail = None
    if gap:
        detail = (f"audit_gap for run {run_id}: "
                  f"platform={p_count} backend={b_count} "
                  f"(delta={p_count - b_count})")
    return {"run_id": run_id, "platform_count": p_count,
            "backend_count": b_count, "gap": gap, "detail": detail}


# --- 3 · orphan sweep (D5/R13: scan + alert, never auto-kill) ------------------

def sweep_orphans(*, dispatch_markers: list[dict],
                  alive_pids: set[int] | None = None) -> list[dict]:
    """Scan for orphaned dispatch processes.

    ``dispatch_markers`` is a list of dicts, each representing a recorded
    dispatch: ``{run_id, pid, started_at, command}``. A marker is an ORPHAN
    if its ``pid`` is NOT in ``alive_pids`` (meaning the parent platform
    process has died but the child may still be running).

    Returns a list of orphan dicts (subset of the input markers). The caller
    is responsible for alerting and for any kill decision (this function
    NEVER kills anything).

    ``alive_pids`` should be the set of PIDs currently alive on the system
    (from ``psutil.process_iter()`` or ``tasklist`` or any process listener).
    If ``alive_pids`` is None, the function returns an empty list (nothing
    to compare against).
    """
    if alive_pids is None:
        return []
    orphans = []
    for marker in dispatch_markers:
        pid = marker.get("pid")
        if pid is not None and pid not in alive_pids:
            # parent is dead; the child may or may not still be running
            orphans.append({
                "run_id": marker.get("run_id"),
                "pid": pid,
                "started_at": marker.get("started_at"),
                "command": marker.get("command"),
                "orphan": True,
            })
    return orphans
