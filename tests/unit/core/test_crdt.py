"""D1: vector clock four-state compare, component-wise merge, HLC monotonicity."""

import pytest

from core.crdt import (
    ClockOrder,
    HLC,
    HLCClock,
    MVEntry,
    clock_compare,
    clock_increment,
    clock_merge,
    dedupe_entries,
    lww_pick,
    mv_merge,
)


class TestVectorClockFourStates:
    def test_equal(self):
        assert clock_compare({"a": 1, "b": 2}, {"b": 2, "a": 1}) is ClockOrder.EQUAL

    def test_dominates(self):
        assert clock_compare({"a": 2, "b": 1}, {"a": 1, "b": 1}) is ClockOrder.DOMINATES

    def test_dominated(self):
        assert clock_compare({"a": 1}, {"a": 1, "b": 3}) is ClockOrder.DOMINATED

    def test_concurrent(self):
        assert clock_compare({"a": 1}, {"b": 1}) is ClockOrder.CONCURRENT

    def test_missing_components_are_zero(self):
        assert clock_compare({}, {"a": 0}) is ClockOrder.EQUAL
        assert clock_compare({}, {"a": 1}) is ClockOrder.DOMINATED

    def test_increment_is_pure(self):
        clock = {"a": 1}
        out = clock_increment(clock, "b")
        assert out == {"a": 1, "b": 1}
        assert clock == {"a": 1}  # input untouched


class TestClockMerge:
    def test_merge_takes_component_wise_max(self):
        merged = clock_merge({"a": 1, "b": 3}, {"a": 2, "c": 1})
        assert merged == {"a": 2, "b": 3, "c": 1}

    def test_merge_empty(self):
        assert clock_merge({}, {}) == {}


class TestHLC:
    def test_monotonic_same_millisecond(self):
        now = {"ms": 1000}
        clock = HLCClock("agent-a", now_ms=lambda: now["ms"])
        s1 = clock.tick()
        s2 = clock.tick()
        assert (s2.wall, s2.counter) > (s1.wall, s1.counter)

    def test_monotonic_under_wall_clock_rollback(self):
        now = {"ms": 1000}
        clock = HLCClock("agent-a", now_ms=lambda: now["ms"])
        s1 = clock.tick()
        now["ms"] = 500  # clock rolled back
        s2 = clock.tick()
        assert s2 > s1  # wall held at 1000, counter advanced
        assert s2.wall == 1000 and s2.counter == 1

    def test_total_order_breaks_ties_on_agent_id(self):
        a = HLC(1000, 0, "agent-a")
        b = HLC(1000, 0, "agent-b")
        assert a < b  # deterministic winner for LWW

    def test_lww_pick_is_stable(self):
        e1 = MVEntry(value="x", clock={"a": 1}, hlc=HLC(1000, 0, "a"), op_id="o1", agent_id="a")
        e2 = MVEntry(value="y", clock={"b": 1}, hlc=HLC(1000, 0, "b"), op_id="o2", agent_id="b")
        assert lww_pick([e1, e2]) is e2
        assert lww_pick([e2, e1]) is e2  # order independent


class TestMVRegister:
    def test_concurrent_values_all_survive(self):
        e1 = MVEntry(value="x", clock={"a": 1}, hlc=HLC(1000, 0, "a"), op_id="o1", agent_id="a")
        e2 = MVEntry(value="y", clock={"b": 1}, hlc=HLC(1000, 0, "b"), op_id="o2", agent_id="b")
        merged = mv_merge([e1, e2])
        assert {m.value for m in merged} == {"x", "y"}  # nothing dropped

    def test_dominated_entry_subsumed(self):
        e1 = MVEntry(value="old", clock={"a": 1}, hlc=HLC(1000, 0, "a"), op_id="o1", agent_id="a")
        e2 = MVEntry(value="new", clock={"a": 2}, hlc=HLC(1001, 0, "a"), op_id="o2", agent_id="a")
        merged = mv_merge([e1, e2])
        assert [m.value for m in merged] == ["new"]

    def test_same_op_id_dedupes(self):
        e = MVEntry(value="x", clock={"a": 1}, hlc=HLC(1000, 0, "a"), op_id="o1", agent_id="a")
        assert len(dedupe_entries([e, e])) == 1
