"""G3 gate / D8: all four topologies run through with FakeLLM +
ScriptedRuntime, producing trace.jsonl per run (health.json arrives with the
G4 inspector)."""

import uuid
import json

import pytest

from core.events import EventBus
from core.llm import FakeLLM
from graphs.runner import run_topology


def _supervisor_llm():
    def script(prompt: str, json_mode: bool) -> str:
        if "拆解" in prompt:
            return json.dumps({
                "subtasks": [
                    {"subtask_id": "s1", "description": "调研多Agent协同的工程现状",
                     "required_capabilities": ["research"]},
                    {"subtask_id": "s2", "description": "对比单Agent与多Agent的收益",
                     "required_capabilities": ["analysis"]},
                ]
            }, ensure_ascii=False)
        if "审稿人" in prompt:
            return json.dumps({"verdict": "PASS", "comments": "ok"})
        if "辩论裁判" in prompt:
            return json.dumps({"verdict": "CONCLUDED",
                               "conclusion": "多Agent协同适合可分解任务,不可分解任务保留单Agent路径。"},
                              ensure_ascii=False)
        if "并发写" in prompt:
            return json.dumps({"action": "pick", "value": "仲裁后的统一结论",
                               "why": "两版本意图不冲突"})
        return "ok"

    return FakeLLM(script=script)


@pytest.mark.parametrize("mode", ["single", "pipeline", "supervisor", "debate"])
def test_topology_runs_offline(mode, tmp_path):
    captured: list = []
    bus = EventBus()
    bus.subscribe(lambda e: captured.append(e))

    final = run_topology(
        mode, "多Agent协同的工程现状调研",
        run_id=(rid := f"run-g3-{mode}-{uuid.uuid4().hex[:8]}"), session_id=f"sess-g3-{mode}",
        llm=_supervisor_llm(), bus=bus,
    )
    assert final["final_report"], f"{mode} must produce a final report"
    assert final["status"] in {"done", "summarized", "concluded", "debating"}

    # trace.jsonl exists and contains the run's events (C13)
    from core.paths import run_dir

    trace = run_dir(rid) / "trace.jsonl"
    assert trace.exists(), "trace.jsonl must be written"
    lines = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l]
    assert lines, "trace must not be empty"
    types = {e["type"] for e in lines}
    assert "agent_output" in types
    assert lines[-1]["type"] == "run_finished"

    if mode == "supervisor":
        # criterion 10 ingredients: agent cards + assignment reasons with scores
        assert any(e["type"] == "agent_card" for e in lines)
        assignments = [e for e in lines if e["type"] == "assignment"]
        assert len(assignments) == 2
        assert all("score" in e["payload"] and "reason" in e["payload"] for e in assignments)
        assert any(e["type"] == "arbitration" for e in lines)
        # two subtasks dispatched to capability-matched agents
        assert {a["payload"]["agent_id"] for a in assignments} == {"researcher", "analyst"}


def test_supervisor_review_rework_loop():
    """REWORK verdict loops back to summarize; rounds cap ends the run."""
    def always_rework(prompt: str, json_mode: bool) -> str:
        if "拆解" in prompt:
            return json.dumps({"subtasks": [
                {"subtask_id": "s1", "description": "调研", "required_capabilities": ["research"]}]},
                ensure_ascii=False)
        if "审稿人" in prompt:
            return json.dumps({"verdict": "REWORK", "comments": "再精炼"})
        return "ok"

    final = run_topology(
        "supervisor", "多Agent协同调研",
        run_id=(rid := f"run-g3-rework-{uuid.uuid4().hex[:8]}"), session_id="sess-g3-rework",
        llm=FakeLLM(script=always_rework),
    )
    assert final["review_round"] == 2          # max_review_rounds reached (C18)
    assert final["review_verdict"] == "REWORK"
