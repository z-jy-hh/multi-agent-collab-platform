"""R16-D2 tests: session lifecycle bound to platform runs (offline, fake ACP
server — no real model, no real dsh binary).

Covers taskbook 判据 2 at the bridge layer:
- start: session/new with a PREPARED cwd under the dispatch root; mapping
  run_id↔dsh_session_id↔cwd↔mode lands in the platform op-log (private
  scope) and as a platform event;
- resume: session/list {cwd} → session/resume; unknown/cross-cwd sessions
  are a LOUD error;
- close: session/close + mapping recorded; shutdown idempotent;
- validation: relative cwd, cwd outside the dispatch root, unknown permission
  mode, double-start — all rejected BEFORE anything spawns;
- environment failures (cwd not preparable, spawn failure) raise
  DshEnvironmentError — distinct from task failures (owner work order).
"""

import os
import sys

import pytest

from core.storage import SQLiteOpStore
from dsh_bridge.session import (
    DshDispatchError,
    DshEnvironmentError,
    DshSessionManager,
)

FAKE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                    "fixtures", "fake_acp_server.py")


@pytest.fixture()
def setup(tmp_path):
    store = SQLiteOpStore(tmp_path / "ops.sqlite3")
    events: list[tuple[str, dict]] = []
    manager = DshSessionManager(
        FAKE, store=store, node_bin=sys.executable,
        dispatch_root=tmp_path / "dispatch",
        emit=lambda t, p: events.append((t, p)))
    return manager, store, events, tmp_path


def test_start_records_mapping_to_oplog_and_event(setup):
    manager, store, events, tmp_path = setup
    session = manager.start("run-1", "platform-sess-1")
    assert session.dsh_session_id == "sess-1"
    assert session.cwd.startswith(str(tmp_path / "dispatch"))
    # op-log is queryable (判据 2): private-scope ops under the platform session
    ops = store.ops_for_session("platform-sess-1", scope="private")
    assert [o.value["action"] for o in ops] == ["new"]
    mapping = ops[0].value
    assert mapping["run_id"] == "run-1"
    assert mapping["dsh_session_id"] == "sess-1"
    assert mapping["permission_mode"] == "workspace-write"
    assert any(t == "dsh_session" for t, _ in events)
    manager.close_session("run-1")
    ops = store.ops_for_session("platform-sess-1", scope="private")
    assert [o.value["action"] for o in ops] == ["new", "close"]


def test_resume_requires_known_session_under_same_cwd(setup):
    manager, store, events, tmp_path = setup
    first = manager.start("run-1", "platform-sess-1")
    resumed = manager.resume("run-1", "platform-sess-1", "sess-9",
                             cwd=first.cwd)
    assert resumed.dsh_session_id == "sess-9"
    with pytest.raises(DshDispatchError, match="not found under cwd"):
        manager.resume("run-2", "platform-sess-2", "sess-does-not-exist",
                       cwd=first.cwd)


def test_close_unknown_run_is_noop_and_shutdown_idempotent(setup):
    manager, store, events, tmp_path = setup
    manager.close_session("never-started")          # no-op, no raise
    manager.start("run-1", "platform-sess-1")
    manager.shutdown()
    manager.shutdown()                              # idempotent


def test_cwd_validation_rejects_relative_and_outside_root(setup):
    manager, store, events, tmp_path = setup
    with pytest.raises(DshDispatchError, match="absolute"):
        manager.start("run-1", "platform-sess-1", cwd="relative/path")
    outside = tmp_path / "outside" / "dir"
    with pytest.raises(DshDispatchError, match="dispatch root"):
        manager.start("run-1", "platform-sess-1", cwd=str(outside))


def test_unknown_permission_mode_rejected_before_spawn(setup):
    manager, store, events, tmp_path = setup
    with pytest.raises(DshDispatchError, match="unknown permission mode"):
        manager.start("run-1", "platform-sess-1", permission_mode="yolo")


def test_double_start_rejected(setup):
    manager, store, events, tmp_path = setup
    manager.start("run-1", "platform-sess-1")
    with pytest.raises(DshDispatchError, match="already has a dsh session"):
        manager.start("run-1", "platform-sess-1")


def test_unusable_cwd_is_environment_error_not_task_error(setup):
    """红线 13 / 委托方工作令：环境问题必须与任务失败分开上报。"""
    manager, store, events, tmp_path = setup
    blocker = tmp_path / "dispatch" / "blocker"      # a FILE where a dir is needed
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(DshEnvironmentError, match="ENVIRONMENT problem"):
        manager.start("run-blocked", "platform-sess-blocked",
                      cwd=str(blocker))


def test_spawn_failure_is_environment_error(setup, tmp_path):
    manager, store, events, tmp_path = setup
    manager._dsh_bin_js = tmp_path / "definitely-missing.js"
    manager._client = None
    with pytest.raises(DshEnvironmentError, match="cannot run dsh acp subprocess"):
        manager.start("run-1", "platform-sess-1")
