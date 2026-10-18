"""R17-BCFIX2: B-2.iv / B-2.v / B-3 / B-5 acceptance tests.

Direct function calls (no TestClient — Windows ProactorEventLoop crash,
known since P4FIX). The recovery/ops storage is redirected into tmp_path so
tests never write the project's real op-log DB (J2 zero-pollution).
"""
from __future__ import annotations

import json
import time

import pytest

from api import routes


@pytest.fixture()
def run_env(tmp_path, monkeypatch):
    rid = "bcfix2-run"
    rd = tmp_path / rid
    rd.mkdir()
    monkeypatch.setattr(routes, "run_dir", lambda r: tmp_path / r)
    import agent_bridge.recovery as rec_mod
    monkeypatch.setattr(rec_mod, "run_dir", lambda r: tmp_path / r)
    # op-log store into tmp (recover_run builds its own store lazily)
    monkeypatch.setattr("core.paths.storage_path",
                        lambda: tmp_path / "ops_root" / "ops.db")
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh_home"))
    monkeypatch.setenv("WB_AUDIT_DIR", str(tmp_path / "wb_audit"))

    def _write_state(status):
        (rd / "state.json").write_text(json.dumps({"status": status}),
                                       encoding="utf-8")

    def _write_trace(events):
        lines = [json.dumps({"seq": i + 1, "ts": time.time() - age, "type": t})
                 for i, (t, age) in enumerate(events)]
        (rd / "trace.jsonl").write_text("\n".join(lines) + "\n",
                                        encoding="utf-8")

    return {"run_id": rid, "dir": rd, "write_state": _write_state,
            "write_trace": _write_trace}


class TestB2RecoverRoute:
    """B-2.iv: the bounded recovery has a PRODUCTION caller."""

    def test_unknown_run_404(self, run_env):
        with pytest.raises(routes.HTTPException) as ei:
            routes.recover_run("bcfix2-missing")
        assert ei.value.status_code == 404

    def test_stalled_run_queues_with_op_log(self, run_env, tmp_path):
        from core.storage import SQLiteOpStore
        run_env["write_state"]("running")
        run_env["write_trace"]([("node_end", 9999)])
        out = routes.recover_run(run_env["run_id"])
        assert out["recovery"]["state"] == "queued"
        assert out["recovery"]["attempt_count"] == 1
        # recovery ledger persisted (auditable artifact, not just a response)
        saved = json.loads((run_env["dir"] / "recovery.json")
                           .read_text(encoding="utf-8"))
        assert saved["state"] == "queued"
        # op-log actually written through the real store machinery
        store = SQLiteOpStore(tmp_path / "ops_root" / "ops.db")
        assert len(store.ops_for_session(f"recovery-{run_env['run_id']}")) >= 1

    def test_repeated_call_unchanged_situation_is_noop(self, run_env):
        """Fingerprint unchanged + within delivery window => no attempt
        growth (the bounded machine, reached through the production entry)."""
        run_env["write_state"]("running")
        run_env["write_trace"]([("node_end", 9999)])
        out1 = routes.recover_run(run_env["run_id"])
        assert out1["recovery"]["attempt_count"] == 1
        # still inside the 600s queued_continuation window => alive => no-op
        out2 = routes.recover_run(run_env["run_id"])
        assert out2["recovery"]["state"] == "queued"
        assert out2["recovery"]["attempt_count"] == 1


class TestB2NoFalseLivePath:
    """B-2.v: GLOBAL backend activity must not make THIS run look alive."""

    def test_global_active_backends_not_a_live_path(self, run_env, monkeypatch):
        import agent_bridge.gate_counters as gc
        run_env["write_state"]("running")
        run_env["write_trace"]([("node_end", 9999)])
        monkeypatch.setattr(gc, "snapshot",
                            lambda: {"active": {"dsh": 2}, "daily": 5})
        from agent_bridge.recovery import derive_liveness
        liv = derive_liveness(run_env["run_id"])
        # discriminating: the old derivation returned live_paths=['active_run']
        assert liv["stalled"] is True
        assert "active_run" not in liv["live_paths"]
        # ...but the global signal stays OBSERVABLE in the derivation output
        assert liv["active_backends"] == {"dsh": 2}

    def test_pending_permission_parsed_not_substring(self, run_env):
        """B-2.v: a payload that merely MENTIONS permission_request must not
        read as a pending permission (parsed type match, not raw substring)."""
        run_env["write_state"]("running")
        run_env["write_trace"]([("node_end", 0)])
        with (run_env["dir"] / "trace.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"seq": 2, "ts": time.time(),
                                 "type": "node_end",
                                 "payload": {"note": "permission_request "
                                                     "resolved"}}) + "\n")
        from agent_bridge.recovery import derive_liveness
        liv = derive_liveness(run_env["run_id"])
        assert liv["pending_permission"] is False


class TestUnifiedViewFields:
    """B-3 (liveness block) + B-5 (source_trust / independent_review)."""

    def test_liveness_block_present(self, run_env):
        run_env["write_state"]("running")
        run_env["write_trace"]([("node_end", 0)])
        view = routes.get_unified_view(run_env["run_id"])
        assert "liveness" in view
        assert view["liveness"]["status"] == "running"
        assert "active_run" in view["liveness"]["live_paths"]
        assert view["liveness"]["stalled"] is False

    def test_liveness_block_on_stalled_run(self, run_env):
        run_env["write_state"]("running")
        run_env["write_trace"]([("node_end", 9999)])
        view = routes.get_unified_view(run_env["run_id"])
        assert view["liveness"]["stalled"] is True
        assert view["liveness"]["live_paths"] == []

    def test_source_trust_defaults_explicitly(self, run_env):
        run_env["write_state"]("running")
        view = routes.get_unified_view(run_env["run_id"])
        assert view["source_trust"] == "unspecified"
        assert view["independent_review"] is None

    def test_source_trust_from_run_meta(self, run_env):
        run_env["write_state"]("running")
        (run_env["dir"] / "run_meta.json").write_text(json.dumps(
            {"source_trust": "external-agent", "independent_review": None}),
            encoding="utf-8")
        view = routes.get_unified_view(run_env["run_id"])
        assert view["source_trust"] == "external-agent"

    def test_review_roundtrip(self, run_env):
        run_env["write_state"]("running")
        (run_env["dir"] / "run_meta.json").write_text(json.dumps(
            {"source_trust": "human"}), encoding="utf-8")
        out = routes.record_review(run_env["run_id"],
                                   {"reviewer": "zcode", "verdict": "pass"})
        assert out["independent_review"]["verdict"] == "pass"
        view = routes.get_unified_view(run_env["run_id"])
        assert view["independent_review"] == {"reviewer": "zcode",
                                              "verdict": "pass"}

    def test_review_validation(self, run_env):
        with pytest.raises(routes.HTTPException) as ei:
            routes.record_review(run_env["run_id"],
                                 {"reviewer": "", "verdict": "pass"})
        assert ei.value.status_code == 422
        with pytest.raises(routes.HTTPException) as ei:
            routes.record_review(run_env["run_id"],
                                 {"reviewer": "x", "verdict": "bogus"})
        assert ei.value.status_code == 422
        with pytest.raises(routes.HTTPException) as ei:
            routes.record_review("bcfix2-missing",
                                 {"reviewer": "x", "verdict": "pass"})
        assert ei.value.status_code == 404


class TestCreateRunPersistsTrust:
    """B-5: create_run persists source_trust where the view reads it."""

    def test_create_run_writes_run_meta(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISPATCH_HETERO", "0")
        monkeypatch.setenv("DSH_DISPATCH_ROOT", str(tmp_path))
        import agent_bridge.recovery as rec_mod
        from api.routes import RunRequest, create_run
        resp = create_run(RunRequest(mode="single", task="meta test",
                                     llm="fake", source_trust="platform"))
        rid = resp["run_id"]
        meta_fp = rec_mod.run_dir(rid) / "run_meta.json"
        assert meta_fp.exists()
        meta = json.loads(meta_fp.read_text(encoding="utf-8"))
        assert meta["source_trust"] == "platform"
        assert meta["independent_review"] is None
