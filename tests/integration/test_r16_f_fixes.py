"""R16 F-fixes regression tests (third-round work order, review findings).

Each test mirrors the reviewer's minimal reproduction:
- F1: constructing DshSessionManager WITHOUT dispatch_root must work
      (the parameter used to shadow the module function of the same name);
- F2: two turns in one run → BOTH permission audits land; start→close→start
      → all three session mappings land (op_id collisions were silently
      dropped by INSERT OR IGNORE);
- F3: a NOT-RUN full tier must NOT make ok=True (third check state);
- F5: REAL permission frames carry no path evidence (only toolCallId) →
      default reject; the human hook is the reachable allow path;
- F6: a dispatch root whose sandbox provisioning fails (ACL defect) is an
      ENV-FAIL environment problem, distinct from task failures;
- F7: real-machine usage frames are {used, size} (context pressure, NOT
      billing tokens) → budget trips on used-over-threshold; unknown usage
      shapes warn instead of silently counting zero.
"""

import os
import sys

import pytest

from core.storage import SQLiteOpStore
from dsh_bridge.session import (
    DshDispatchError,
    DshEnvironmentError,
    DshSessionManager,
    default_dispatch_root,
)

FAKE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                    "fixtures", "fake_acp_server.py")


def test_f1_manager_constructs_without_dispatch_root(tmp_path):
    """F1: the default branch used to call the SHADOWED parameter (None) →
    TypeError. Now it must resolve the module-level default root."""
    store = SQLiteOpStore(tmp_path / "ops.sqlite3")
    manager = DshSessionManager(FAKE, store=store, node_bin=sys.executable)
    assert manager._root == default_dispatch_root()
    # and the parameterised path still wins
    manager2 = DshSessionManager(FAKE, store=store, node_bin=sys.executable,
                                 dispatch_root=tmp_path / "dispatch")
    assert manager2._root == tmp_path / "dispatch"


def test_f2_session_mapping_survives_start_close_start(tmp_path):
    """F2b: start → close → start (same run) = THREE mapping ops; the old
    op_id `dsh-session-{run}-{action}` collided on the second start."""
    store = SQLiteOpStore(tmp_path / "ops.sqlite3")
    manager = DshSessionManager(FAKE, store=store, node_bin=sys.executable,
                                dispatch_root=tmp_path / "dispatch")
    manager.start("run-1", "psess")
    manager.close_session("run-1")
    manager.start("run-1", "psess")
    ops = store.ops_for_session("psess", scope="private")
    actions = [o.value["action"] for o in ops if o.key == "dsh_session::run-1"]
    assert actions == ["new", "close", "new"], actions


def test_f6_acl_probe_failure_is_environment_error(tmp_path):
    """F6(复核 第3轮): plain file create/delete PASSES on an unauthorized
    root (Authenticated-Users Modify) while dsh's provisioning fails with
    WinError 5 — the smoke therefore needs the icacls integrity probe; its
    failure is an ENV-FAIL-class environment problem, not a task failure."""
    from dsh_bridge.session import cwd_smoke, DshEnvironmentError

    def acl_defect(cmd):
        return 5, "SetNamedSecurityInfoW failed (WinError 5): Access is denied."

    with pytest.raises(DshEnvironmentError, match="ACL probe failed"):
        cwd_smoke(tmp_path / "probe", acl_runner=acl_defect)


def test_f6_healthy_acl_probe_passes(tmp_path):
    from dsh_bridge.session import cwd_smoke
    cwd_smoke(tmp_path / "probe", acl_runner=lambda cmd: (0, ""))   # no raise


def test_f6_manager_start_reports_acl_defect_as_environment(tmp_path):
    """委托方工作令：授权缺失的根 ⇒ ENV-FAIL（不是 task FAIL，也不是 skip）。"""
    def acl_defect(cmd):
        return 5, "Access is denied."

    store = SQLiteOpStore(tmp_path / "ops.sqlite3")
    manager = DshSessionManager(FAKE, store=store, node_bin=sys.executable,
                                dispatch_root=tmp_path / "dispatch",
                                acl_runner=acl_defect)
    with pytest.raises(DshEnvironmentError, match="ACL probe failed"):
        manager.start("run-1", "psess")


def test_sr1_second_turn_on_same_runner_still_cancels(tmp_path):
    """SR1(自查成立): the cancelled latch used to survive across turns —
    the SECOND turn's budget/deadline cancel would be a no-op and hang.
    Per-turn reset must make both turns cancellable."""
    from dsh_bridge.runner import DshTurnRunner
    store = SQLiteOpStore(tmp_path / "ops.sqlite3")
    manager = DshSessionManager(FAKE, store=store, node_bin=sys.executable,
                                dispatch_root=tmp_path / "dispatch",
                                client_extra_args=["usage_burst"])
    session = manager.start("run-1", "psess")
    runner = DshTurnRunner(session, store=store, budget_context_used=10000)
    for turn in (1, 2):
        result = runner.run_turn(f"turn {turn}")
        assert result.budget_exceeded is True
        assert result.stop_reason == "cancelled", (
            f"turn {turn} was not cancellable — SR1 per-turn reset regressed")


def test_sr2_concurrent_turn_on_same_client_rejected_loudly(tmp_path):
    """SR2(自查成立): one client has ONE handler slot — a concurrent turn
    would cross-wire events/permission answers. Single-flight: the second
    turn is rejected LOUDLY, never silently interleaved."""
    import threading
    from dsh_bridge.runner import DshTurnRunner
    from dsh_bridge.session import DshDispatchError
    store = SQLiteOpStore(tmp_path / "ops.sqlite3")
    manager = DshSessionManager(FAKE, store=store, node_bin=sys.executable,
                                dispatch_root=tmp_path / "dispatch",
                                client_extra_args=["cancel_shape"])
    session = manager.start("run-1", "psess")
    runner = DshTurnRunner(session, store=store, turn_timeout=2.0)
    results: dict = {}

    def first():
        results["first"] = runner.run_turn("holds the single flight")

    t = threading.Thread(target=first)
    t.start()
    import time
    time.sleep(0.4)                    # let the first turn take the lock
    with pytest.raises(DshDispatchError, match="another turn is already active"):
        runner.run_turn("must be rejected loudly")
    t.join(timeout=10)
    assert "first" in results          # the first turn completed


def test_f2_permission_audits_survive_second_turn(tmp_path, monkeypatch):
    """F2a: two turns in the SAME run each answer a permission request —
    both audits must land (the per-gate _seq reset silently dropped the
    second one)."""
    from dsh_bridge.runner import DshTurnRunner
    store = SQLiteOpStore(tmp_path / "ops.sqlite3")
    manager = DshSessionManager(FAKE, store=store, node_bin=sys.executable,
                                dispatch_root=tmp_path / "dispatch",
                                client_extra_args=["permission_roundtrip"])
    session = manager.start("run-1", "psess")

    for turn in range(2):
        runner = DshTurnRunner(session, store=store,
                               emit=lambda t, p: None)
        result = runner.run_turn(f"turn {turn}")
        assert result.stop_reason == "end_turn"

    ops = store.ops_for_session("psess", scope="private")
    perm_ops = [o for o in ops if o.key == "dsh_permission::run-1"]
    assert len(perm_ops) == 2, (
        f"second-turn audit was silently dropped by op_id collision: "
        f"{[o.op_id for o in perm_ops]}")
    assert len({o.op_id for o in perm_ops}) == 2
