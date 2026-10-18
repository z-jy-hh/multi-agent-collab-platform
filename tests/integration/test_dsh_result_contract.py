import os
import sys

"""R16-D7 tests: the structured result contract.

dsh has NO forced-JSON-schema output switch (measured) — so the contract is:
the agent (prompt contract) writes its result JSON to the agreed path inside
the session cwd, the platform reads that file and validates it against ITS
OWN schema; validation failure degrades to text-only (retry is the caller's
choice). Offline via the fake server's writes_result scenario.
"""

import os

from dsh_bridge.runner import DshTurnRunner

SCHEMA = {"type": "object", "required": ["status"]}




def test_result_contract_valid(tmp_path):
    from dsh_bridge.session import DshSessionManager
    from core.storage import SQLiteOpStore
    fake = _fake()
    target = str(tmp_path / "dispatch" / "run-1" / "dsh_result.json")
    content = '{"status": "ok", "summary": "done"}'
    store = SQLiteOpStore(tmp_path / "ops.sqlite3")
    manager = DshSessionManager(fake, store=store, node_bin=sys.executable,
                                dispatch_root=tmp_path / "dispatch",
                                client_extra_args=["writes_result", target,
                                                   content])
    session = manager.start("run-1", "psess-1")
    runner = DshTurnRunner(session, store=store, result_schema=SCHEMA)
    result = runner.run_turn("produce the result file")
    assert result.stop_reason == "end_turn"
    assert result.result_data == {"status": "ok", "summary": "done"}
    assert result.result_validation_errors == []


def test_result_contract_invalid_degrades_to_text(tmp_path):
    from dsh_bridge.session import DshSessionManager
    from core.storage import SQLiteOpStore
    fake = _fake()
    target = str(tmp_path / "dispatch" / "run-1" / "dsh_result.json")
    store = SQLiteOpStore(tmp_path / "ops.sqlite3")
    manager = DshSessionManager(fake, store=store, node_bin=sys.executable,
                                dispatch_root=tmp_path / "dispatch",
                                client_extra_args=["writes_result", target,
                                                   '{"nope": 0}'])
    session = manager.start("run-1", "psess-1")
    runner = DshTurnRunner(session, store=store, result_schema=SCHEMA)
    result = runner.run_turn("produce an invalid result")
    assert result.stop_reason == "end_turn"
    assert result.result_data is None                    # degraded
    assert any("status" in e for e in result.result_validation_errors)


def test_result_contract_missing_file_degrades(tmp_path):
    from dsh_bridge.session import DshSessionManager
    from core.storage import SQLiteOpStore
    store = SQLiteOpStore(tmp_path / "ops.sqlite3")
    manager = DshSessionManager(
        _fake(), store=store, node_bin=sys.executable,
        dispatch_root=tmp_path / "dispatch",
        client_extra_args=["updates_then_end"])          # writes nothing
    session = manager.start("run-1", "psess-1")
    runner = DshTurnRunner(session, store=store, result_schema=SCHEMA)
    result = runner.run_turn("no file this time")
    assert result.stop_reason == "end_turn"
    assert result.result_data is None
    assert result.result_validation_errors == [
        "result file missing: " + os.path.join(session.cwd, "dsh_result.json")]


def _fake():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "fixtures", "fake_acp_server.py")



