"""R16-D8/F3/F8 tests: the dsh-check self-check, fully OFFLINE via the
scriptable fake ACP server (the --full tier with the REAL dsh costs tokens
and is gated behind owner authorization; with the fake it is offline and
exact).

Exit-code semantics (F3/F8 — the reviewer measured the old code returning 0
with an unverified permission/cancel round-trip):
  0 = everything ran and passed; 1 = at least one FAIL;
  2 = nothing failed but something NOT-RUN; 3 = ENV-FAIL.
A NOT-RUN row must NEVER make the check look verified.
"""

import os
import sys

from dsh_bridge.selfcheck import run_dsh_check
from dsh_bridge.session import DshEnvironmentError

FAKE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                    "fixtures", "fake_acp_server.py")


def test_basic_tier_all_pass_but_not_run_is_its_own_state(tmp_path):
    rows, code = run_dsh_check(
        command_builder=lambda: [sys.executable, FAKE, "happy"],
        dispatch_root=tmp_path / "dispatch")
    names = {r.name: r.status for r in rows}
    assert names["dispatch_cwd_smoke"] == "PASS"
    assert names["acp_handshake"] == "PASS"
    assert names["session_roundtrip"] == "PASS"
    assert names["session_list"] == "PASS"
    # F3: the skipped full tier is a NOT-RUN row and its OWN exit code —
    # an unverified permission/cancel round-trip must not look verified
    assert names["full_tier"] == "NOT-RUN"
    assert code == 2


def test_protocol_mismatch_fails_loud(tmp_path):
    rows, code = run_dsh_check(
        command_builder=lambda: [sys.executable, FAKE, "bad_protocol"],
        dispatch_root=tmp_path / "dispatch")
    handshake = {r.name: (r.status, r.details) for r in rows}["acp_handshake"]
    assert handshake[0] == "FAIL"
    assert "protocolVersion" in handshake[1]
    assert code == 1                              # FAIL beats NOT-RUN


def test_environment_failure_is_its_own_class(tmp_path):
    def broken_builder():
        raise DshEnvironmentError(
            "ACL defect: sandbox provisioning unavailable on this host")

    rows, code = run_dsh_check(command_builder=broken_builder,
                               dispatch_root=tmp_path / "dispatch")
    handshake = {r.name: (r.status, r.details) for r in rows}["acp_handshake"]
    assert handshake[0] == "ENV-FAIL"             # NOT "FAIL"
    assert "ACL defect" in handshake[1]
    assert code == 3                              # ENV-FAIL beats everything


def test_full_tier_offline_all_pass_exit_zero(tmp_path):
    # spawn #1 = handshake + session roundtrip + list (one connection);
    # spawn #2 = permission roundtrip; spawn #3 = cancel roundtrip
    scenarios = ["happy", "permission_roundtrip", "cancel_shape"]
    rows, code = run_dsh_check(
        command_builder=lambda: [sys.executable, FAKE, scenarios.pop(0)],
        dispatch_root=tmp_path / "dispatch", full=True, timeout=30.0)
    names = {r.name: (r.status, r.details) for r in rows}
    assert names["permission_roundtrip"][0] == "PASS"
    assert "seen=True" in names["permission_roundtrip"][1]
    assert names["cancel_roundtrip"][0] == "PASS"
    assert "cancelled" in names["cancel_roundtrip"][1]
    assert names["update_kind_counts"][0] == "PASS"
    assert code == 0                              # everything ran and passed
