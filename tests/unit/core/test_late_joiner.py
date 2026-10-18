"""D6: late joiner pulls unseen ops by watermark and never re-sees known ops
(history backfill, C10)."""

from core.memory import MemoryStore


def _seed(store):
    a = store.guard("s1", "agent-a")
    b = store.guard("s1", "agent-b")
    a.publish("k1", "a1", op_id="op-1")   # clock {agent-a: 1}
    a.publish("k1", "a2", op_id="op-2")   # clock {agent-a: 2}
    b.publish("k2", "b1", op_id="op-3")   # clock {agent-b: 1}
    return a, b


def test_fresh_joiner_pulls_everything():
    store = MemoryStore()
    _seed(store)
    newcomer = store.guard("s1", "agent-c")
    unseen = newcomer.backfill_from(watermark={})
    assert len(unseen) == 3


def test_partially_caught_up_joiner_gets_only_unseen():
    store = MemoryStore()
    _seed(store)
    late = store.guard("s1", "agent-c")
    # it has already merged agent-a's first op only
    watermark = {"agent-a": 1}
    unseen = late.backfill_from(watermark=watermark)
    ids = {o.op_id for o in unseen}
    assert "op-1" not in ids       # seen: watermark EQUALs its clock
    assert {"op-2", "op-3"} == ids  # unseen: dominated + concurrent


def test_fully_synced_joiner_gets_nothing():
    store = MemoryStore()
    _seed(store)
    synced = store.guard("s1", "agent-c")
    full = store.merged_clock("s1")
    assert synced.backfill_from(watermark=full) == []


def test_watermark_is_per_agent_view():
    store = MemoryStore()
    _seed(store)
    # agent-a has seen only its own writes
    wm = store.watermark_for("s1", "agent-a")
    assert wm == {"agent-a": 2}
