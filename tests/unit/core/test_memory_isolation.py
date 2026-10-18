"""D3: cross-namespace private access raises NamespaceViolation; sessions
are independent; the session blackboard is the sanctioned shared channel."""

import pytest

from core.errors import NamespaceViolation
from core.memory import MemoryStore


def test_cross_namespace_write_raises():
    store = MemoryStore()
    guard = store.guard("s1", "agent-a")
    with pytest.raises(NamespaceViolation):
        guard.private_write_to("s1", "agent-b", "key", "v", op_id="op-1")


def test_cross_namespace_read_raises():
    store = MemoryStore()
    store.guard("s1", "agent-b").private_write("key", "secret", op_id="op-b1")
    attacker = store.guard("s1", "agent-a")
    with pytest.raises(NamespaceViolation):
        attacker.private_read_from("s1", "agent-b", "key")


def test_cross_session_access_raises():
    store = MemoryStore()
    guard = store.guard("s1", "agent-a")
    with pytest.raises(NamespaceViolation):
        guard.private_write_to("s2", "agent-a", "key", "v", op_id="op-2")
    with pytest.raises(NamespaceViolation):
        guard.read_blackboard("s2")


def test_own_namespace_roundtrip():
    store = MemoryStore()
    guard = store.guard("s1", "agent-a")
    guard.private_write("notes", "hello", op_id="op-1")
    entries = guard.private_read("notes")
    assert [e.value for e in entries] == ["hello"]


def test_two_sessions_are_independent():
    store = MemoryStore()
    s1 = store.guard("s1", "agent-a")
    s2 = store.guard("s2", "agent-a")
    s1.private_write("key", "from-s1", op_id="op-1")
    s2.private_write("key", "from-s2", op_id="op-2")  # same agent, other session
    assert [e.value for e in s1.private_read("key")] == ["from-s1"]
    assert [e.value for e in s2.private_read("key")] == ["from-s2"]


def test_blackboard_publish_is_session_scoped_and_shared():
    store = MemoryStore()
    a = store.guard("s1", "agent-a")
    b = store.guard("s1", "agent-b")
    a.publish("finding", "x=1", op_id="op-1")
    # session-level shared: b can read the session blackboard (C10)
    ops = b.read_blackboard()
    assert [o.value for o in ops if o.key == "finding"] == ["x=1"]
