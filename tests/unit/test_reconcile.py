"""R17-W7: reconcile.py tests - hash-chain verification, row-level
reconciliation, and orphan sweep, all with SYNTHETIC fixtures (never real
WB files).
"""

from __future__ import annotations

import json

import pytest

from agent_bridge.reconcile import (
    _row_hash,
    load_chain,
    reconcile_rows,
    sweep_orphans,
    verify_chain,
)


def _make_chain(n: int = 5) -> list[dict]:
    """Build a valid hash chain of n rows."""
    rows = []
    prev_hash = None
    for i in range(1, n + 1):
        row = {"id": f"row-{i}", "sequence": i, "prevHash": prev_hash,
               "event": f"test_event_{i}", "run_id": "run-1"}
        row["hash"] = _row_hash(row)
        rows.append(row)
        prev_hash = row["hash"]
    return rows


class TestVerifyChain:
    def test_valid_chain(self):
        rows = _make_chain(5)
        result = verify_chain(rows)
        assert result["chain_ok"] is True
        assert result["broken_at"] is None
        assert result["rows_checked"] == 5

    def test_empty_chain(self):
        result = verify_chain([])
        assert result["chain_ok"] is True
        assert result["rows_checked"] == 0

    def test_single_row(self):
        rows = _make_chain(1)
        result = verify_chain(rows)
        assert result["chain_ok"] is True

    def test_hash_mismatch_detected(self):
        rows = _make_chain(4)
        rows[2]["hash"] = "0" * 64           # tamper with row 2
        result = verify_chain(rows)
        assert result["chain_ok"] is False
        assert result["broken_at"] == 2
        assert "hash mismatch" in result["reason"]

    def test_prevhash_mismatch_detected(self):
        rows = _make_chain(4)
        rows[2]["prevHash"] = "f" * 64       # break the prevHash link
        result = verify_chain(rows)
        assert result["chain_ok"] is False
        assert result["broken_at"] == 2
        assert "prevHash mismatch" in result["reason"]

    def test_sequence_gap_detected(self):
        rows = _make_chain(4)
        rows[2]["sequence"] = 99             # skip a sequence number
        result = verify_chain(rows)
        assert result["chain_ok"] is False
        assert result["broken_at"] == 2
        assert "sequence gap" in result["reason"]

    def test_load_chain_roundtrip(self, tmp_path):
        rows = _make_chain(3)
        path = tmp_path / "chain.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        loaded = load_chain(path)
        assert len(loaded) == 3
        result = verify_chain(loaded)
        assert result["chain_ok"] is True


class TestReconcileRows:
    def test_no_gap(self):
        ops = [{"run_id": "r1"}, {"run_id": "r1"}, {"run_id": "r2"}]
        events = [{"run_id": "r1"}, {"run_id": "r1"}, {"run_id": "r2"}]
        result = reconcile_rows(ops, events, run_id="r1")
        assert result["gap"] is False
        assert result["platform_count"] == 2
        assert result["backend_count"] == 2

    def test_gap_detected(self):
        ops = [{"run_id": "r1"}, {"run_id": "r1"}, {"run_id": "r1"}]
        events = [{"run_id": "r1"}]
        result = reconcile_rows(ops, events, run_id="r1")
        assert result["gap"] is True
        assert result["detail"] is not None
        assert "audit_gap" in result["detail"]

    def test_empty_both_no_gap(self):
        result = reconcile_rows([], [], run_id="r1")
        assert result["gap"] is False


class TestSweepOrphans:
    def test_orphan_detected(self):
        markers = [{"run_id": "r1", "pid": 100, "started_at": "T0",
                    "command": "python fake.py"}]
        orphans = sweep_orphans(dispatch_markers=markers, alive_pids={200, 300})
        assert len(orphans) == 1
        assert orphans[0]["pid"] == 100
        assert orphans[0]["orphan"] is True

    def test_alive_not_orphan(self):
        markers = [{"run_id": "r1", "pid": 100, "started_at": "T0",
                    "command": "python fake.py"}]
        orphans = sweep_orphans(dispatch_markers=markers, alive_pids={100})
        assert len(orphans) == 0

    def test_none_alive_pids_returns_empty(self):
        markers = [{"run_id": "r1", "pid": 100}]
        orphans = sweep_orphans(dispatch_markers=markers, alive_pids=None)
        assert orphans == []

    def test_no_markers_returns_empty(self):
        orphans = sweep_orphans(dispatch_markers=[], alive_pids={100})
        assert orphans == []
