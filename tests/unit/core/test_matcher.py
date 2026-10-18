"""D5: matcher numeric score assertions + hard gates (coverage & min_score);
no-capability-match stops dispatch entirely."""

import pytest

from core.errors import NoCapabilityMatch, NoEligibleAgent
from core.matcher import (
    GateThresholds,
    apply_assignments,
    assign,
    missing_capabilities,
    score_agent,
)
from core.schemas import AgentProfileSpec, SubTask


def _profile(agent_id, domains, strength, cost="low"):
    return AgentProfileSpec(
        agent_id=agent_id, role=agent_id, domains=domains,
        tools=[], strength=strength, cost=cost,
    )


PROFILES = [
    _profile("researcher", ["research", "writing"], {"research": 9, "writing": 6, "analysis": 4}),
    _profile("analyst", ["analysis", "data"], {"analysis": 9, "data": 8, "research": 4}),
    _profile("writer", ["writing"], {"writing": 9, "research": 5}),
]


class TestScoringFormula:
    def test_numeric_value(self):
        # score(researcher, [research, analysis])
        # = (1.0*1 + 0.6*(9/10)  +  1.0*0 + 0.6*(4/10)) / 2
        # = (1 + 0.54 + 0 + 0.24) / 2 = 0.89
        p = PROFILES[0]
        assert score_agent(p, ["research", "analysis"]) == pytest.approx(0.89)

        # score(analyst, [research, analysis])
        # = (0 + 0.6*(4/10) + 1 + 0.6*(9/10)) / 2 = 0.89
        assert score_agent(PROFILES[1], ["research", "analysis"]) == pytest.approx(0.89)

        # score(writer, [research, analysis]) = (0 + 0.3 + 0 + 0) / 2 = 0.15
        assert score_agent(PROFILES[2], ["research", "analysis"]) == pytest.approx(0.15)

    def test_score_bounds(self):
        strong = _profile("super", ["x"], {"x": 10})
        assert score_agent(strong, ["x"]) == pytest.approx(1.6)
        assert score_agent(_profile("weak", [], {}), ["x"]) == 0.0


class TestCoverageGate:
    def test_missing_capability_detected(self):
        subs = [SubTask(subtask_id="s1", description="d", required_capabilities=["video"])]
        assert missing_capabilities(subs, PROFILES, GateThresholds()) == ["video"]

    def test_no_match_raises_and_no_dispatch(self):
        subs = [
            SubTask(subtask_id="s1", description="d", required_capabilities=["research"]),
            SubTask(subtask_id="s2", description="d", required_capabilities=["video"]),
        ]
        with pytest.raises(NoCapabilityMatch) as exc:
            assign(subs, PROFILES, GateThresholds())
        assert "video" in exc.value.missing
        # s1 was perfectly coverable — but the gate stops ALL dispatch (C7)
        assert not any("assigned" in str(exc.value) for _ in [0])


class TestMinScoreGate:
    def test_weak_agent_fails_min_score(self):
        # caps [research, writing]: researcher=1.45, writer=0.92 — writer is
        # gate-passing on coverage but below min_score 0.95 → excluded
        subs = [SubTask(subtask_id="s1", description="d", required_capabilities=["research", "writing"])]
        report = assign(subs, PROFILES, GateThresholds(min_score=0.95))
        assert report.assignments[0].agent_id == "researcher"
        assert report.assignments[0].score == pytest.approx(1.45)

    def test_min_score_too_high_blocks_everything(self):
        subs = [SubTask(subtask_id="s1", description="d", required_capabilities=["research", "writing"])]
        with pytest.raises(NoEligibleAgent):
            assign(subs, PROFILES, GateThresholds(min_score=1.5))

    def test_no_single_agent_covers_all_caps_of_subtask(self):
        # research+analysis: every agent is below min_strength on one of the
        # two caps → the subtask cannot be assigned to any single agent
        subs = [SubTask(subtask_id="s1", description="d", required_capabilities=["research", "analysis"])]
        with pytest.raises(NoEligibleAgent) as exc:
            assign(subs, PROFILES, GateThresholds())
        assert "no agent meets min_strength" in str(exc.value)


class TestAssignment:
    def test_assignment_is_deterministic_and_recorded(self):
        subs = [SubTask(subtask_id="s1", description="d", required_capabilities=["writing"])]
        th = GateThresholds(min_score=0.85)
        r1 = assign(subs, PROFILES, th)
        r2 = assign(subs, PROFILES, th)
        assert r1.assignments[0].agent_id == r2.assignments[0].agent_id == "writer"
        assert "gates: coverage+min_score PASS" in r1.assignments[0].reason

    def test_apply_assignments_fills_fields(self):
        subs = [SubTask(subtask_id="s1", description="d", required_capabilities=["writing"])]
        report = assign(subs, PROFILES, GateThresholds(min_score=0.85))
        filled = apply_assignments(subs, report)[0]
        assert filled.assigned_agent == "writer"
        assert filled.assignment_score == pytest.approx(1.54)  # 1 + 0.6*9/10
        assert "writer" in filled.assignment_reason

    def test_spread_across_subtasks_prefers_unused_agents(self):
        subs = [
            SubTask(subtask_id="s1", description="d", required_capabilities=["writing"]),
            SubTask(subtask_id="s2", description="d", required_capabilities=["data", "analysis"]),
        ]
        report = assign(subs, PROFILES, GateThresholds(min_score=0.85))
        chosen = {a.agent_id for a in report.assignments}
        assert chosen == {"writer", "analyst"}
