"""D4: concurrent writes to one key never drop values; mutual non-domination
is surfaced as a conflict by the pure marker (the reducer itself only merges,
C5)."""

from core.crdt import HLC, MVEntry
from core.state import conflict_keys, merge_blackboard, resolve_blackboard


def _entry(op_id, agent, clock, value, key="k1", wall=1000, counter=0):
    d = MVEntry(
        value=value, clock=clock, hlc=HLC(wall, counter, agent),
        op_id=op_id, agent_id=agent,
    ).to_dict()
    d["key"] = key
    return d


def test_concurrent_writes_keep_every_value():
    existing = None
    incoming = [
        _entry("op-a", "agent-a", {"agent-a": 1}, "finding from A"),
        _entry("op-b", "agent-b", {"agent-b": 1}, "finding from B"),
    ]
    merged = merge_blackboard(existing, incoming)
    assert len(merged["k1"]) == 2  # nothing dropped
    assert {e["value"] for e in merged["k1"]} == {"finding from A", "finding from B"}


def test_concurrent_key_is_marked_as_conflict():
    merged = merge_blackboard(None, [
        _entry("op-a", "agent-a", {"agent-a": 1}, "A says x"),
        _entry("op-b", "agent-b", {"agent-b": 1}, "B says y"),
    ])
    assert conflict_keys(merged) == ["k1"]


def test_dominated_write_is_subsumed_not_conflict():
    merged = merge_blackboard(None, [
        _entry("op-a", "agent-a", {"agent-a": 1}, "draft", key="k2", wall=1000),
        _entry("op-a2", "agent-a", {"agent-a": 2}, "final", key="k2", wall=1001),
    ])
    assert len(merged["k2"]) == 1
    assert merged["k2"][0]["value"] == "final"
    assert conflict_keys(merged) == []


def test_merge_is_additive_across_steps():
    step1 = merge_blackboard(None, [_entry("op-a", "agent-a", {"agent-a": 1}, "v1")])
    step2 = merge_blackboard(step1, [_entry("op-b", "agent-b", {"agent-b": 1}, "v2")])
    assert len(step2["k1"]) == 2


def test_lww_resolution_picks_hlc_largest():
    merged = merge_blackboard(None, [
        _entry("op-a", "agent-a", {"agent-a": 1}, "older", wall=1000),
        _entry("op-b", "agent-b", {"agent-b": 1}, "newer", wall=2000),
    ])
    assert resolve_blackboard(merged)["k1"] == "newer"
