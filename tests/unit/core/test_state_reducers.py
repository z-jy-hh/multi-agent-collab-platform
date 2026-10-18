"""D7: reducers are pure — same input → same output, no side effects on
arguments, no arrival-order dependence (C5)."""

import copy

from core.state import (
    GraphState,
    conflict_keys,
    merge_blackboard,
    merge_notes,
    merge_subtasks,
)


def _entry(op_id, agent, clock, value, key="k"):
    return {"key": key, "value": value, "clock": clock,
            "hlc": (1000, 0, agent), "op_id": op_id, "agent_id": agent}


def test_merge_blackboard_same_input_same_output():
    incoming = [_entry("op-a", "a", {"a": 1}, "v1")]
    assert merge_blackboard(None, incoming) == merge_blackboard(None, incoming)


def test_merge_blackboard_does_not_mutate_arguments():
    existing = {"k": [_entry("op-a", "a", {"a": 1}, "v1")]}
    snapshot = copy.deepcopy(existing)
    incoming = [_entry("op-b", "b", {"b": 1}, "v2")]
    incoming_snapshot = copy.deepcopy(incoming)
    merge_blackboard(existing, incoming)
    assert existing == snapshot
    assert incoming == incoming_snapshot


def test_merge_subtasks_order_independent():
    a = {"subtask_id": "s1", "description": "a", "required_capabilities": []}
    b = {"subtask_id": "s2", "description": "b", "required_capabilities": []}
    c = {"subtask_id": "s3", "description": "c", "required_capabilities": []}
    assert merge_subtasks([a, b], [c]) == merge_subtasks([c], [a, b]) == merge_subtasks(None, [a, b, c])


def test_merge_notes_order_independent():
    assert merge_notes(["b", "a"], ["c"]) == merge_notes(["c"], ["a", "b"]) == ["a", "b", "c"]


def test_conflict_keys_pure_and_sorted():
    bb = {
        "z": [_entry("o1", "a", {"a": 1}, "v1"), _entry("o2", "b", {"b": 1}, "v2")],
        "a": [_entry("o3", "a", {"a": 2}, "v3"), _entry("o4", "a", {"a": 3}, "v4")],
    }
    snapshot = copy.deepcopy(bb)
    assert conflict_keys(bb) == ["z"]
    assert bb == snapshot  # untouched


def test_graph_state_typeddict_is_annotated_with_reducers():
    reducers = GraphState.__annotations__["blackboard"].__metadata__
    assert any(r is merge_blackboard for r in reducers)
    reducers_sub = GraphState.__annotations__["subtasks"].__metadata__
    assert any(r is merge_subtasks for r in reducers_sub)
