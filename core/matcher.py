"""Capability-based dispatch with hard gates (C7).

Scoring formula (also documented in README):

    score(agent, subtask) =
        ( Σ_{c in required} [ 1.0 * (c in agent.domains)  +  0.6 * strength[c]/10 ] )
        / len(required)

    → range [0, 1.6]; tag hit contributes 1.0, strength contributes up to 0.6.

Two hard gates, BOTH must hold before any assignment is produced:
  1. coverage   — every capability c in a subtask's required_capabilities must
     have at least one agent whose strength[c] >= min_strength;
  2. score      — the chosen agent's score >= min_score.

If coverage fails for any capability, ``assign`` raises ``NoCapabilityMatch``
listing the missing domains and NO dispatch happens — hard-assigning the
top scorer is explicitly forbidden. An LLM may only break near-ties
(score gap < gap_threshold) among gate-passing candidates and can never
overturn a gate; its reason goes into the assignment record / event stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.errors import NoCapabilityMatch, NoEligibleAgent
from core.llm import LLMClient
from core.schemas import AgentProfileSpec, SubTask

W_TAG = 1.0
W_STRENGTH = 0.6

DEFAULT_MIN_STRENGTH = 5.0
DEFAULT_MIN_SCORE = 0.9
DEFAULT_GAP_THRESHOLD = 0.1


@dataclass
class GateThresholds:
    min_strength: float = DEFAULT_MIN_STRENGTH
    min_score: float = DEFAULT_MIN_SCORE
    gap_threshold: float = DEFAULT_GAP_THRESHOLD


@dataclass
class Assignment:
    subtask_id: str
    agent_id: str
    score: float
    reason: str
    runner_up: str | None = None
    llm_tiebreak: bool = False


def score_agent(profile: AgentProfileSpec, required: list[str]) -> float:
    """Documented scoring formula. Pure function."""
    if not required:
        return 0.0
    total = 0.0
    for cap in required:
        tag = W_TAG if cap in profile.domains else 0.0
        strength = W_STRENGTH * (float(profile.strength.get(cap, 0.0)) / 10.0)
        total += tag + strength
    return round(total / len(required), 4)


def missing_capabilities(subtasks: list[SubTask],
                         profiles: list[AgentProfileSpec],
                         thresholds: GateThresholds) -> list[str]:
    """Capabilities no agent can cover at min_strength. Pure function."""
    missing: set[str] = set()
    for st in subtasks:
        for cap in st.required_capabilities:
            best = max((float(p.strength.get(cap, 0.0)) for p in profiles), default=0.0)
            if best < thresholds.min_strength:
                missing.add(cap)
    return sorted(missing)


def eligible_agents(subtask: SubTask,
                    profiles: list[AgentProfileSpec],
                    thresholds: GateThresholds) -> list[tuple[AgentProfileSpec, float]]:
    """Gate-passing candidates for one subtask, sorted by score desc."""
    out: list[tuple[AgentProfileSpec, float]] = []
    for p in profiles:
        covered = all(
            float(p.strength.get(cap, 0.0)) >= thresholds.min_strength
            for cap in subtask.required_capabilities
        )
        if not covered:
            continue
        s = score_agent(p, subtask.required_capabilities)
        if s >= thresholds.min_score:
            out.append((p, s))
    out.sort(key=lambda t: (-t[1], t[0].agent_id))
    return out


@dataclass
class MatchReport:
    assignments: list[Assignment] = field(default_factory=list)


def assign(
    subtasks: list[SubTask],
    profiles: list[AgentProfileSpec],
    thresholds: GateThresholds | None = None,
    llm: LLMClient | None = None,
) -> MatchReport:
    """Assign every subtask to the best gate-passing agent.

    Raises NoCapabilityMatch (no dispatch at all) when coverage fails.
    Deterministic: sort by (order given), tie-break on agent_id.
    """
    th = thresholds or GateThresholds()
    missing = missing_capabilities(subtasks, profiles, th)
    if missing:
        raise NoCapabilityMatch(missing, "hard coverage gate failed; dispatch stopped")

    report = MatchReport()
    taken: set[str] = set()
    for st in subtasks:
        cands = eligible_agents(st, profiles, th)
        if not cands:
            # capabilities exist somewhere, but no single agent covers this
            # subtask — dispatching a partial fit is forbidden (C7)
            raise NoEligibleAgent(
                f"subtask {st.subtask_id}: no agent meets min_strength on all of "
                f"{list(st.required_capabilities)} and min_score")
        # spread work: prefer unused agents among equally gate-passing candidates
        free = [c for c in cands if c[0].agent_id not in taken] or cands
        top_score = cands[0][1]
        near = [c for c in free if (top_score - c[1]) <= th.gap_threshold]
        llm_used = False
        if llm is not None and len(near) >= 2:
            names = ", ".join(c[0].agent_id for c in near)
            answer = llm.complete(
                f"Two agents both pass the capability gates for subtask "
                f"{st.subtask_id!r}: {names}. Reply with exactly one agent_id.",
                system="You are a dispatch tie-breaker. Output one agent_id only.",
            )
            pick = next((c for c in near if c[0].agent_id in answer), near[0])
            llm_used = True
        else:
            pick = free[0]
        taken.add(pick[0].agent_id)
        runner_up = next((c[0].agent_id for c in cands if c[0].agent_id != pick[0].agent_id), None)
        report.assignments.append(
            Assignment(
                subtask_id=st.subtask_id,
                agent_id=pick[0].agent_id,
                score=pick[1],
                reason=(
                    f"agent={pick[0].agent_id} domains={pick[0].domains} "
                    f"strength={dict(pick[0].strength)} cost={pick[0].cost} "
                    f"score={pick[1]} gates: coverage+min_score PASS"
                    + (" + LLM tie-break" if llm_used else "")
                ),
                runner_up=runner_up,
                llm_tiebreak=llm_used,
            )
        )
    return report


def apply_assignments(subtasks: list[SubTask], report: MatchReport) -> list[SubTask]:
    """Return new SubTask objects with assignment fields filled (pure)."""
    by_id = {a.subtask_id: a for a in report.assignments}
    out: list[SubTask] = []
    for st in subtasks:
        a = by_id.get(st.subtask_id)
        if a:
            out.append(st.model_copy(update={
                "assigned_agent": a.agent_id,
                "assignment_score": a.score,
                "assignment_reason": a.reason,
            }))
        else:
            out.append(st.model_copy())
    return out
