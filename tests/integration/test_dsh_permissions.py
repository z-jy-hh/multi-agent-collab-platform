"""R16-D4/F5 tests: the permission policy on REAL-machine frame shapes.

F5(复核): real ``session/request_permission`` frames carry ONLY
sessionId/toolCall.toolCallId/options — no path evidence. The synthetic-path
tests of the previous round were green while production could only ever hit
default-reject; these tests use the REAL frame shape and pin the redesigned
policy:
- default (workspace-write, no hook) → reject-once (fail-closed);
- human permission card approve → allow-once; deny → reject-once;
- card timeout → reject-once + turn cancel;
- danger-full-access run tier → allow-once (owner-authorized, audited);
- the answer shape is exactly the measured one;
- every answer is audited to the op-log.
"""

import os

import pytest

from core.storage import SQLiteOpStore
from dsh_bridge.permissions import (
    ALLOW,
    PermissionGate,
    PermissionPolicy,
    REJECT,
)

CWD = "D:\\proj\\runs\\dsh_dispatch\\run-1"


def real_frame(tool_call_id="call_00_1") -> dict:
    """The REAL frame shape (reviewer-captured): no path, no rawInput."""
    return {"sessionId": "sess-1",
            "toolCall": {"toolCallId": tool_call_id},
            "options": [{"optionId": "allow-once", "name": "Allow once",
                         "kind": "allow_once"},
                        {"optionId": "reject-once", "name": "Reject",
                         "kind": "reject_once"}]}


@pytest.fixture()
def store(tmp_path):
    return SQLiteOpStore(tmp_path / "ops.sqlite3")


def make_gate(store, mode="workspace-write", **kwargs):
    return PermissionGate(run_id="run-1", platform_session_id="psess",
                          dsh_session_id="sess-1", cwd=CWD, mode=mode,
                          store=store, **kwargs)


def test_policy_default_reject_on_real_frames(store):
    gate = make_gate(store)
    answer = gate.handle_server_request("session/request_permission",
                                        real_frame())
    # F5: production frames carry no path evidence → the ONLY evidence-free
    # decision is fail-closed reject
    assert answer == {"outcome": {"outcome": "selected", "optionId": REJECT}}
    ops = store.ops_for_session("psess", scope="private")
    row = [o.value for o in ops if o.key == "dsh_permission::run-1"][-1]
    assert row["basis"] == "default-deny"
    assert row["tool_call_id"] == "call_00_1"


def test_policy_danger_full_access_run_tier_allows(store):
    gate = make_gate(store, mode="danger-full-access")
    answer = gate.handle_server_request("session/request_permission",
                                        real_frame("call_00_2"))
    assert answer["outcome"]["optionId"] == ALLOW
    row = [o.value for o in store.ops_for_session("psess", scope="private")
           if o.key == "dsh_permission::run-1"][-1]
    assert row["basis"] == "run-tier"


def test_human_card_approve_is_reachable(store):
    gate = make_gate(store, human_hook=lambda p: ALLOW, timeout=5.0)
    answer = gate.handle_server_request("session/request_permission",
                                        real_frame("call_00_3"))
    assert answer["outcome"]["optionId"] == ALLOW
    row = [o.value for o in store.ops_for_session("psess", scope="private")
           if o.key == "dsh_permission::run-1"][-1]
    assert row["basis"] == "human-hook"


def test_human_card_deny_is_reachable(store):
    gate = make_gate(store, human_hook=lambda p: REJECT, timeout=5.0)
    answer = gate.handle_server_request("session/request_permission",
                                        real_frame("call_00_4"))
    assert answer["outcome"]["optionId"] == REJECT


def test_human_card_timeout_auto_rejects_and_cancels(store):
    import time
    cancels: list[int] = []

    def never(params):
        time.sleep(5)
        return None

    gate = make_gate(store, human_hook=never, timeout=0.3,
                     cancel_session=lambda: cancels.append(1))
    answer = gate.handle_server_request("session/request_permission",
                                        real_frame("call_00_5"))
    assert answer["outcome"]["optionId"] == REJECT
    assert cancels == [1]
    row = [o.value for o in store.ops_for_session("psess", scope="private")
           if o.key == "dsh_permission::run-1"][-1]
    assert row["timed_out"] is True


def test_policy_still_rejects_malformed_frames(store):
    policy = PermissionPolicy(CWD, mode="danger-full-access")
    assert policy.decide({"sessionId": "sess-1"}).option_id == REJECT


def test_gate_rejects_other_methods(store):
    gate = make_gate(store)
    with pytest.raises(Exception, match="session/request_permission"):
        gate.handle_server_request("session/update", {})
