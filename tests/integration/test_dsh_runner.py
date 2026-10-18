"""R16-D6 tests: the turn runner — budget trip / deadline cancel / process
death, all offline against the scriptable fake ACP server (no real model).

Also covers the wiring the taskbook calls out as known trouble spots:
- cancel goes out as a NOTIFICATION (no id) even from the budget and the
  deadline paths (regression for measured V5');
- the prompt request waits turn_timeout + grace so the cancelled stopReason
  can arrive instead of surfacing as a client-side timeout;
- process death mid-turn is a LOUD error on the TurnResult.
"""

import json
import os
import sys

import pytest

from core.storage import SQLiteOpStore
from dsh_bridge.acp_client import AcpProcessDied
from dsh_bridge.runner import DshTurnRunner
from dsh_bridge.session import DshSessionManager

FAKE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                    "fixtures", "fake_acp_server.py")


def make_manager(tmp_path, extra_args=None):
    store = SQLiteOpStore(tmp_path / "ops.sqlite3")
    events: list[tuple[str, dict]] = []
    manager = DshSessionManager(
        FAKE, store=store, node_bin=sys.executable,
        dispatch_root=tmp_path / "dispatch", client_extra_args=extra_args,
        emit=lambda t, p: events.append((t, p)))
    return manager, store, events


def test_happy_turn_end_to_end(tmp_path):
    manager, store, events = make_manager(tmp_path,
                                          extra_args=["updates_then_end"])
    session = manager.start("run-1", "psess-1")
    runner = DshTurnRunner(session, emit=lambda t, p: events.append((t, p)), store=store)
    result = runner.run_turn("do the thing")
    # F4: notifications dispatch asynchronously — drain before asserting
    session.client._notif_queue.join()
    assert result.stop_reason == "end_turn"
    assert result.turn_timed_out is False
    assert result.budget_exceeded is False
    # five measured update kinds flowed through the mapping
    assert result.event_kinds == ["dsh_thought_chunk", "dsh_message_chunk",
                                  "dsh_tool_call", "dsh_tool_call_update",
                                  "dsh_usage_update"]
    manager.close_session("run-1")


def test_budget_trip_ends_turn_cancelled_via_notification(tmp_path):
    """F7: the fake now emits the REAL-MACHINE usage shape ({used,size} =
    context pressure); the budget gate trips on budget_context_used and the
    turn ends CANCELLED via the notification-shaped session/cancel."""
    manager, store, events = make_manager(tmp_path, extra_args=["usage_burst"])
    session = manager.start("run-1", "psess-1")
    runner = DshTurnRunner(session, emit=lambda t, p: events.append((t, p)),
                           store=store, budget_context_used=10000)
    result = runner.run_turn("blow the budget")
    assert result.budget_exceeded is True
    assert "context budget" in result.budget_reason
    assert result.stop_reason == "cancelled"
    # the trip must reach the server as the notification-shaped cancel
    assert result.event_kinds[-1] == "dsh_usage_update"
    budget_events = [p for t, p in events if t == "dsh_budget_exceeded"]
    assert budget_events and "context budget" in budget_events[0]["reason"]
    # cancel_had_id is asserted inside the fake's prompt reply — verify via
    # the turn's stopReason being "cancelled" (the fake only answers cancel
    # notifications; a request-shaped cancel would leave the turn hanging)
    manager.close_session("run-1")


def test_turn_deadline_cancels(tmp_path):
    manager, store, events = make_manager(tmp_path, extra_args=["cancel_shape"])
    session = manager.start("run-1", "psess-1")
    runner = DshTurnRunner(session, emit=lambda t, p: events.append((t, p)), store=store,
                           turn_timeout=0.6)
    result = runner.run_turn("hang until the deadline")
    assert result.stop_reason == "cancelled"
    assert result.turn_timed_out is True
    manager.close_session("run-1")


def test_process_death_mid_turn_is_loud(tmp_path):
    manager, store, events = make_manager(tmp_path, extra_args=["die_mid_turn"])
    session = manager.start("run-1", "psess-1")
    runner = DshTurnRunner(session, emit=lambda t, p: events.append((t, p)), store=store)
    result = runner.run_turn("crash")
    assert result.stop_reason is None
    assert result.error is not None and "code=9" in result.error


def test_on_usage_failure_fails_safe_cancels_visibly(tmp_path, monkeypatch):
    """R16 实测教训回归：on_usage 里的任何异常绝不能静默假死——
    必须发 dsh_bridge_error 事件并失败安全地取消回合。"""
    class BrokenGate:
        def __init__(self, **kwargs):
            pass

        def accumulate(self, usage):
            raise RuntimeError("boom in budget")

    monkeypatch.setattr("dsh_bridge.runner.BudgetGate", BrokenGate)
    manager, store, events = make_manager(tmp_path, extra_args=["usage_burst"])
    session = manager.start("run-1", "psess-1")
    runner = DshTurnRunner(session, emit=lambda t, p: events.append((t, p)), store=store)
    result = runner.run_turn("budget explodes")
    assert result.stop_reason == "cancelled"       # failed-safe, NOT hung
    errors = [p for t, p in events if t == "dsh_bridge_error"]
    assert errors and "boom in budget" in errors[0]["error"]


def test_permission_gate_wired_into_turn(tmp_path):
    manager, store, events = make_manager(
        tmp_path, extra_args=["permission_roundtrip"])
    session = manager.start("run-1", "psess-1")
    runner = DshTurnRunner(session, emit=lambda t, p: events.append((t, p)), store=store)
    outside = {"toolCall": {"toolCallId": "cX",
                            "rawInput": {"path": "D:\\elsewhere\\x"}}}
    # the fake asks for permission to write OUTSIDE the cwd; the platform
    # policy (workspace-write) must answer reject-once automatically
    fake_rejects = manager._client is not None
    result = runner.run_turn("try to escape the sandbox")
    assert result.stop_reason == "end_turn"          # rejected, turn still ends
    ops = store.ops_for_session("psess-1", scope="private")
    perms = [o.value for o in ops if o.key == "dsh_permission::run-1"]
    assert perms and perms[-1]["decision"] == "reject-once"
    assert fake_rejects
