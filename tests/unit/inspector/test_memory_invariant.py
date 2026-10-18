"""W4-1 D: ``memory_invariant`` must ignore the bridge's audit mapping ops and
nothing else (a wide exemption would silently disable C10)."""
from __future__ import annotations

from inspector.checks import memory_invariant


class _Op:
    def __init__(self, op_id, agent_id, clock, key, session_id="s1"):
        self.op_id = op_id
        self.agent_id = agent_id
        self.clock = clock
        self.key = key
        self.session_id = session_id


class _Store:
    def __init__(self, ops):
        self._ops = ops

    def ops_for_session(self, session_id):
        return self._ops


class _Memory:
    def __init__(self, ops):
        self._store = _Store(ops)


def _ctx(ops):
    return {"memory": _Memory(ops), "session_id": "s1"}


def test_bridge_mapping_ops_are_exempt_but_agent_clocks_still_checked():
    ops = [
        _Op("op1", "researcher", {"researcher": 1}, "out::ask"),
        _Op("op2", "dsh_bridge", {}, "dsh_session::run-1"),
        _Op("op3", "researcher", {"researcher": 2}, "out::ask-2"),
    ]
    result = memory_invariant.run(_ctx(ops))
    assert result.status == "PASS", result.details
    assert "2 agent ops (3 total)" in result.details


def test_exemption_is_narrow_and_a_gap_still_fails():
    # (a) a non-mapping bridge op is NOT exempt (clock {} would otherwise hide)
    ops = [_Op("op1", "dsh_bridge", {}, "other::x")]
    assert memory_invariant.run(_ctx(ops)).status == "FAIL"
    # (b) a real agent clock gap still fails
    ops = [_Op("op1", "researcher", {"researcher": 2}, "out::ask")]
    result = memory_invariant.run(_ctx(ops))
    assert result.status == "FAIL"
    assert "clock component 2 != 1" in result.details
