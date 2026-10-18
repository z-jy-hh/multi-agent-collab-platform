"""P1: profile_guard — checkable rules are enforced; soft rules never block."""
from __future__ import annotations

import pytest

from core.events import EventBus
from core.schemas import AgentResult
from graphs.identity import Binding, ResolvedProfile
from graphs.profile_guard import ProfileViolation, execute_with_checks
from graphs.profiles import CheckSpec, Preset, RuleCheck


def _preset() -> Preset:
    return Preset(
        id="probe", version=1, status="active", name="probe", description="",
        cost="low", capabilities={"domains": {"research": 9}, "tags": ["research"]},
        output_schema="worker_output", tools=[], budget={}, behavior={},
        rules=[], skills=[])


def _resolved(rc: RuleCheck | None, *, brief: str = "BRIEF") -> ResolvedProfile:
    return ResolvedProfile(
        binding=Binding("w1", "a1", "probe"), preset=_preset(), rules=[],
        checks={"r1": rc} if rc is not None else {}, skills=[], temp_rules=[],
        brief=brief, brief_sha256="x", selection_id="sel-x", prompt_sha256="x")


class Fake:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def execute(self, agent_id, prompt, context):
        self.prompts.append(prompt)
        out = self.outputs.pop(0) if self.outputs else ""
        return AgentResult(agent_id=agent_id, output=out, meta={})


def _events(bus):
    return [e for e in bus.replay(0) if e.type == "profile_check"]


def test_hard_path_violation_retries_once_then_raises():
    bus = EventBus()
    inner = Fake([r"C:\temp\a.txt", r"C:\temp\a.txt"])
    rc = RuleCheck("写盘", "hard", CheckSpec("path_under", r"D:\dsh-work"))
    with pytest.raises(ProfileViolation):
        execute_with_checks(inner, "a1", "BRIEF + task", {}, _resolved(rc),
                            bus=bus, run_id="r1")
    assert len(inner.prompts) == 2, "exactly one corrective retry"
    assert "纠正" in inner.prompts[1]
    assert [e.payload["status"] for e in _events(bus)] == ["fail", "fail"]


def test_hard_path_pass_returns_first_result():
    bus = EventBus()
    inner = Fake([r"D:\dsh-work\a.txt"])
    rc = RuleCheck("写盘", "hard", CheckSpec("path_under", r"D:\dsh-work"))
    result = execute_with_checks(inner, "a1", "BRIEF + task", {}, _resolved(rc),
                                 bus=bus, run_id="r1")
    assert result.output == r"D:\dsh-work\a.txt"
    assert len(inner.prompts) == 1
    assert _events(bus) == []


def test_soft_rule_warns_and_does_not_block():
    bus = EventBus()
    inner = Fake(["this is too long"])
    rc = RuleCheck("短", "soft", CheckSpec("max_chars", 3))
    result = execute_with_checks(inner, "a1", "BRIEF + task", {}, _resolved(rc),
                                 bus=bus, run_id="r1")
    assert result.output == "this is too long"
    assert len(inner.prompts) == 1
    assert [e.payload["status"] for e in _events(bus)] == ["warn"]


def test_required_tool_call_fails_after_retry():
    bus = EventBus()
    inner = Fake(["no tools used", "still no tools"])
    rc = RuleCheck("检索", "hard", CheckSpec("required_tool_call", "search_local"))
    with pytest.raises(ProfileViolation):
        execute_with_checks(inner, "a1", "BRIEF + task", {}, _resolved(rc),
                            bus=bus, run_id="r1")
    assert len(inner.prompts) == 2


def test_brief_must_be_delivered_before_execute():
    inner = Fake(["ok"])
    with pytest.raises(ProfileViolation):
        execute_with_checks(inner, "a1", "task without brief", {}, _resolved(None))
    assert inner.prompts == []
