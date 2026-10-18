"""D2: same op_id written twice → exactly 1 record (both backends)."""

import pytest

from core.memory import MemoryStore
from core.storage import InMemoryOpStore, SQLiteOpStore


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryStore(InMemoryOpStore())
    return MemoryStore(SQLiteOpStore(tmp_path / "ops.sqlite3"))


def test_same_op_id_written_twice_is_one_record(store):
    op1 = store.publish_blackboard("s1", "agent-a", "key1", "value-1", op_id="op-1")
    assert op1 is not None
    op2 = store.publish_blackboard("s1", "agent-a", "key1", "value-1", op_id="op-1")
    assert op2 is None  # duplicate rejected
    assert store.op_count == 1

    p1 = store.private_write("s1", "agent-a", "note", "n1", op_id="op-1")
    assert p1 is None  # dedupe is global across scopes too


def test_different_op_ids_both_recorded(store):
    store.publish_blackboard("s1", "agent-a", "k", "v1", op_id="op-1")
    store.publish_blackboard("s1", "agent-a", "k", "v2", op_id="op-2")
    assert store.op_count == 2


def test_deterministic_op_id_helper():
    from core.ids import derive_op_id

    a = derive_op_id("run-1", "worker", "key", 0)
    b = derive_op_id("run-1", "worker", "key", 0)
    c = derive_op_id("run-1", "worker", "key", 1)
    assert a == b  # replay-safe (C6)
    assert a != c
