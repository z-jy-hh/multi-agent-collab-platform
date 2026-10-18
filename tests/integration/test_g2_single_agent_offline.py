"""G2 gate: one single-agent run fully offline.

Wires ScriptedRuntime (no key → no crewai) through the engine: memory guard
publishes to the session blackboard, the event bus fans out to a JSONL trace,
and the op log is queryable afterwards.
"""

import json

from core.events import EventBus, EventJSONLWriter
from core.llm import FakeLLM
from core.memory import MemoryStore
from core.paths import run_dir
from core.runtime import ScriptedRuntime
from core.state import merge_blackboard


def test_single_agent_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")  # force offline state (C9)

    # 1. FakeLLM responds deterministically
    llm = FakeLLM(script=["要点1; 要点2; 结论:语料覆盖充分。"])
    reply = llm.complete("调研:多Agent协同", json_mode=False)
    assert reply.startswith("要点1")

    # 2. ScriptedRuntime executes the researcher agent
    runtime = ScriptedRuntime({"researcher": ["要点1; 要点2; 结论:语料覆盖充分。"]})
    result = runtime.execute("researcher", "调研:多Agent协同", {"role_prompt": "v1"})
    assert result.agent_id == "researcher"
    assert "结论" in result.output

    # 3. publish to the session blackboard through the guard (C10)
    store = MemoryStore()
    guard = store.guard("session-g2", "researcher")
    op_id = guard.derive_op_id("run-g2", "ask", "findings", 0)
    op = guard.publish("findings", result.output, op_id)
    assert op is not None and op.scope == "blackboard"

    # 4. pure reducer merges it into a state-shaped blackboard
    entry = {
        "key": op.key, "value": op.value, "clock": op.clock,
        "hlc": list(op.hlc), "op_id": op.op_id, "agent_id": op.agent_id,
    }
    bb = merge_blackboard(None, [entry])
    assert "findings" in bb and bb["findings"][0]["value"] == result.output

    # 5. event bus fans out to a JSONL trace (C12/C13)
    bus = EventBus()
    trace = tmp_path / "trace.jsonl"
    bus.subscribe(EventJSONLWriter(trace))
    event = bus.emit("agent_output", "run-g2", {"agent": "researcher", "chars": len(result.output)},
                     agent_id="researcher")
    assert event.seq == 1
    lines = trace.read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[0])["type"] == "agent_output"

    # 6. op log is queryable — late joiner would see it
    assert store.op_count == 1
    assert len(store.ops_since("session-g2", watermark={})) == 1


def test_factory_import_safe_without_key():
    """crews/factory must import cleanly with no key (C9) and only raise
    ConfigError at crew-build time — never at import time."""
    import crews.factory as factory

    assert callable(factory.make_crew)
    profiles = factory.load_profiles()
    assert "researcher" in profiles and "strength" in profiles["researcher"]


def test_search_local_corpus_tool():
    from crews.tools.search_local import search_local

    hits = search_local("记忆 串台 向量时钟")
    assert hits, "corpus search must find memory_consistency.md"
    assert all("corpus" not in h["doc"] for h in hits)  # doc names only
    assert hits[0]["doc"] == "memory_consistency.md"    # ranked by keyword overlap
    assert any(("记忆" in h["snippet"]) or ("串台" in h["snippet"]) for h in hits)
