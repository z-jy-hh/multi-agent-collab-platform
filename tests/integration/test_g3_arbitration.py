"""G3: conflict detection + arbitration node — two concurrent writes to one
key are detected by vector clocks and resolved (rebase by LLM, or
deterministic LWW fallback under the stable HLC order)."""

from core.crdt import clock_compare, clock_merge, ClockOrder
from core.events import EventBus
from core.llm import FakeLLM
from core.memory import MemoryStore
from core.registry import load_registry
from core.paths import configs_dir
from core.state import merge_blackboard

from graphs.builder import RunContext, make_arbitrate_node


def _ctx(llm_script) -> RunContext:
    bus = EventBus()
    events: list = []
    bus.subscribe(events.append)
    return RunContext(
        run_id="run-arb", session_id="sess-arb", task="t", mode="supervisor",
        runtime=None, llm=FakeLLM(script=llm_script),
        registry=load_registry(configs_dir()), memory=MemoryStore(), bus=bus,
        config={"nodes": [], "edges": []},
    ), events


def _entry_of(op):
    return {"key": op.key, "value": op.value, "clock": dict(op.clock),
            "hlc": list(op.hlc), "op_id": op.op_id, "agent_id": op.agent_id}


def test_concurrent_writes_detected_and_arbitrated():
    def arbiter(prompt: str, json_mode: bool) -> str:
        assert "并发写" in prompt
        return '{"action": "rebase", "value": "统一后的结论", "why": "意图不冲突,执行版本偏移转换"}'

    ctx, events = _ctx(arbiter)
    a = ctx.guard("agent-a")
    b = ctx.guard("agent-b")
    a.publish("conclusion", "A 的结论", op_id=ctx.op_id("worker", "conclusion", 0))
    b.publish("conclusion", "B 的结论", op_id=ctx.op_id("worker", "conclusion", 1))

    ops = ctx.memory.blackboard_ops("sess-arb")
    clocks = [o.clock for o in ops]
    assert clock_compare(clocks[0], clocks[1]) is ClockOrder.CONCURRENT  # C11

    state = {"blackboard": merge_blackboard(None, [_entry_of(o) for o in ops])}
    out = make_arbitrate_node(ctx, {"id": "arbitrate", "output_schema": {}})(state)
    assert out["conflicts"] == ["conclusion"]
    assert out["arbitration"]["conclusion"]["action"] == "rebase"
    assert out["arbitration"]["conclusion"]["value"] == "统一后的结论"
    assert any(e.type == "arbitration" for e in events)


def test_arbitration_llw_fallback_is_deterministic():
    ctx, events = _ctx(lambda p, j: "看不懂,随便")  # unusable LLM answer
    a = ctx.guard("agent-a")
    a.publish("conclusion", "旧", op_id=ctx.op_id("w", "conclusion", 0))
    b = ctx.guard("agent-b")
    b.publish("conclusion", "新", op_id=ctx.op_id("w", "conclusion", 1))

    state = {"blackboard": merge_blackboard(None, [_entry_of(o) for o in ctx.memory.blackboard_ops("sess-arb")])}
    out = make_arbitrate_node(ctx, {"id": "arbitrate", "output_schema": {}})(state)
    res = out["arbitration"]["conclusion"]
    assert res["action"] == "pick"          # LWW fallback (C11)
    assert "LWW fallback" in res["why"]
    # stable order: b's HLC stamps after a's → b wins, regardless of input order
    assert res["value"] == "新"


def test_no_conflict_round_reports_none():
    ctx, events = _ctx(lambda p, j: "{}")
    a = ctx.guard("agent-a")
    a.publish("result::s1", "只有一条", op_id=ctx.op_id("w", "result::s1", 0))
    state = {"blackboard": merge_blackboard(None, [_entry_of(o) for o in ctx.memory.blackboard_ops("sess-arb")])}
    out = make_arbitrate_node(ctx, {"id": "arbitrate", "output_schema": {}})(state)
    assert out["conflicts"] == []
    assert any(e.type == "arbitration" and e.payload["action"] == "none" for e in events)


def test_merged_clock_is_component_wise_max():
    ctx, _ = _ctx(lambda p, j: "{}")
    ctx.guard("agent-a").publish("k", 1, op_id=ctx.op_id("w", "k", 0))
    ctx.guard("agent-b").publish("k", 2, op_id=ctx.op_id("w", "k", 1))
    merged = ctx.memory.merged_clock("sess-arb")
    assert merged == clock_merge({"agent-a": 1}, {"agent-b": 1})
