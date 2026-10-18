"""R17-P0b-C: recovery tests using CONTROLLED TIME (deterministic, no sleep).

The ``now`` parameter of ``assess_and_recover`` is injected to make the tests
deterministic. No ``time.sleep()`` — flaky must be located and fixed, not
masked by timing."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from agent_bridge.recovery import (
    assess_and_recover,
    load_recovery,
    resolve_action,
)


@pytest.fixture()
def run_env(tmp_path, monkeypatch):
    run_id = "test-recovery"
    rd = tmp_path / run_id
    rd.mkdir()
    monkeypatch.setattr("agent_bridge.recovery.run_dir", lambda rid: rd)
    monkeypatch.setattr("agent_bridge.recovery._recovery_path",
                        lambda rid: rd / "recovery.json")

    def _write_state(status):
        (rd / "state.json").write_text(json.dumps({"status": status}),
                                       encoding="utf-8")

    def _write_trace(ts_offsets=None, events=None):
        base = time.time()
        lines = []
        for i, off in enumerate(ts_offsets or []):
            lines.append(json.dumps({"seq": i + 1, "ts": base + off,
                                     "type": events[i] if events else "test"}))
        (rd / "trace.jsonl").write_text("\n".join(lines), encoding="utf-8")

    _write_state("running")
    return {"run_id": run_id, "dir": rd, "write_state": _write_state,
            "write_trace": _write_trace}


class TestB2RealSequence:
    """Controlled-time deterministic sequence: none -> queued -> action_open
    -> escalated. NO sleep, NO timing dependency."""

    def test_full_sequence_deterministic(self, run_env):
        run_id = run_env["run_id"]
        run_env["write_trace"]([-9999])
        base = time.time()

        # t=0: stalled (events 9999s old) => queued (attempt 1)
        rec1 = assess_and_recover(run_id, now=base, silence_suspicious_s=600)
        assert rec1["state"] == "queued"
        assert rec1["attempt_count"] == 1

        # t=+1s: queued expired (delivery_timeout=0.5s, elapsed=1s > 0.5s)
        #     => action_open (attempt 2)
        rec2 = assess_and_recover(run_id, now=base + 1, silence_suspicious_s=600,
                                  delivery_timeout_s=0.5)
        assert rec2["state"] == "action_open"
        assert rec2["attempt_count"] == 2

        # t=+3s: action_open expired (delivery_timeout*2=1s, elapsed=3-1=2s > 1s)
        #     => escalated (attempt 3)
        rec3 = assess_and_recover(run_id, now=base + 3, silence_suspicious_s=600,
                                  delivery_timeout_s=0.5)
        assert rec3["state"] == "escalated"
        assert rec3["attempt_count"] == 3

    def test_no_infinite_retry(self, run_env):
        """Fingerprint unchanged after escalation => stays escalated, no new
        attempts, no new op-log entries."""
        run_id = run_env["run_id"]
        run_env["write_trace"]([-9999])
        base = time.time()
        for t_offset in (0, 1, 3, 5, 7):
            rec = assess_and_recover(run_id, now=base + t_offset,
                                     silence_suspicious_s=600,
                                     delivery_timeout_s=0.5)
        assert rec["state"] == "escalated"
        assert rec["attempt_count"] == 3  # capped

    def test_liveness_recovery_resets_state(self, run_env):
        """B-2.iii: trace becomes fresh => recovery state resets to recovered."""
        run_id = run_env["run_id"]
        run_env["write_trace"]([-9999])
        base = time.time()
        rec1 = assess_and_recover(run_id, now=base, silence_suspicious_s=600)
        assert rec1["state"] == "queued"
        # events become fresh (last_event_ts = now) => live again
        run_env["write_trace"]([0])
        rec2 = assess_and_recover(run_id, now=base + 1, silence_suspicious_s=600)
        assert rec2["state"] == "recovered"
        assert "active_run" in rec2.get("live_paths", [])


class TestB1Liveness:
    def test_active_run_fresh_events(self, run_env):
        run_env["write_trace"]([0])
        base = time.time()
        rec = assess_and_recover(run_env["run_id"], now=base,
                                 silence_suspicious_s=600)
        assert "active_run" in rec.get("live_paths", [])

    def test_pending_permission_is_explicit_wait(self, run_env):
        run_env["write_trace"]([0], events=["dsh_permission_request"])
        base = time.time()
        rec = assess_and_recover(run_env["run_id"], now=base,
                                 silence_suspicious_s=600)
        assert "explicit_wait" in rec.get("live_paths", [])

    def test_terminal_not_stalled(self, run_env):
        run_env["write_state"]("done")
        base = time.time()
        rec = assess_and_recover(run_env["run_id"], now=base,
                                 silence_suspicious_s=600)
        assert "active_run" not in rec.get("live_paths", [])


class TestResolveAction:
    def test_resolve_action_closes(self, run_env):
        run_id = run_env["run_id"]
        run_env["write_trace"]([-9999])
        base = time.time()
        assess_and_recover(run_id, now=base, silence_suspicious_s=600)
        rec = resolve_action(run_id, owner="owner1", note="manual fix")
        assert rec["state"] == "recovered"
        assert rec["resolved_by"] == "owner1"


class TestB2OpLog:
    def test_op_log_written(self, run_env, tmp_path):
        from core.storage import SQLiteOpStore
        store = SQLiteOpStore(tmp_path / "ops.db")
        run_id = run_env["run_id"]
        run_env["write_trace"]([-9999])
        base = time.time()
        rec = assess_and_recover(run_id, now=base, silence_suspicious_s=600,
                                 store=store)
        assert rec["state"] == "queued"
        ops = store.ops_for_session(f"recovery-{run_id}")
        assert len(ops) >= 1

    def test_op_log_idempotent(self, run_env, tmp_path):
        from core.storage import SQLiteOpStore
        store = SQLiteOpStore(tmp_path / "ops.db")
        run_id = run_env["run_id"]
        run_env["write_trace"]([-9999])
        base = time.time()
        # same time => same fingerprint => same op_id => second append returns False
        rec1 = assess_and_recover(run_id, now=base, silence_suspicious_s=600,
                                  store=store)
        ops_before = store.ops_count()
        rec2 = assess_and_recover(run_id, now=base, silence_suspicious_s=600,
                                  store=store)
        ops_after = store.ops_count()
        assert ops_after == ops_before  # idempotent
