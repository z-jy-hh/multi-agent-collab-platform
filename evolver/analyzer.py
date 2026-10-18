"""Evolver analyzer: turn inspection results into concrete weaknesses.

Input: HealthReports (and optionally run events). Output: a list of
Weakness(kind, target_file, detail) — the optimizer maps each to a candidate
prompt/config refinement. No LLM required (deterministic, offline).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.schemas import HealthReport


@dataclass
class Weakness:
    kind: str            # schema | rework | match | arbitration | memory | cost
    target_file: str     # whitelist-relative path the optimizer may refine
    detail: str


def analyze(reports: list[HealthReport], events_by_run: dict[str, list] | None = None) -> list[Weakness]:
    events_by_run = events_by_run or {}
    weaknesses: list[Weakness] = []
    for report in reports:
        for check in report.checks:
            if check.status == "FAIL" and check.name == "schema":
                weaknesses.append(Weakness("schema", "prompts/supervisor/supervisor.v1.md",
                                           check.details))
            if check.status == "FAIL" and check.name == "match_quality":
                weaknesses.append(Weakness("match", "configs/agents.yaml", check.details))
            if check.status == "FAIL" and check.name == "arbitration":
                weaknesses.append(Weakness("arbitration",
                                           "prompts/supervisor/supervisor.v1.md",
                                           check.details))
            if check.status == "FAIL" and check.name == "memory_invariant":
                weaknesses.append(Weakness("memory", "configs/workflow.yaml", check.details))

    # rework loops: reviewer kept sending the draft back → refine the writer
    for events in (events_by_run or {}).values():
        reworks = [e for e in events if e.type == "review"
                   and e.payload.get("verdict") == "REWORK"]
        if reworks:
            weaknesses.append(Weakness(
                "rework", "prompts/worker/writer.v1.md",
                f"{len(reworks)} rework round(s): {reworks[-1].payload.get('comments', '')}"))
    return weaknesses
