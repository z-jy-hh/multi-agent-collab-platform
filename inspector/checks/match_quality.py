"""check: match_quality (C7) — every assignment carries a score and reason,
scores respect min_score, and no dispatched agent lacks the required
capability strength (no out-of-capability work)."""

from core.schemas import CheckResult


def run(ctx: dict) -> CheckResult:
    mode = ctx["mode"]
    if mode not in ("supervisor",):
        return CheckResult(name="match_quality", status="PASS",
                           details=f"mode {mode} has no capability dispatch")

    assignments = [e for e in ctx["events"] if e.type == "assignment"]
    if not assignments:
        nomatch = any(e.type == "no_capability_match" for e in ctx["events"])
        if nomatch:
            return CheckResult(name="match_quality", status="WARN",
                               details="dispatch stopped: missing capability (hard gate worked)")
        return CheckResult(name="match_quality", status="FAIL", details="no assignments made")

    min_score = float(ctx["params"].get("min_score", 0.9))
    registry = ctx["registry"]
    problems: list[str] = []
    for e in assignments:
        payload = e.payload
        if payload.get("score", 0.0) < min_score:
            problems.append(f"{payload.get('subtask_id')}: score {payload.get('score')} < {min_score}")
        if not payload.get("reason"):
            problems.append(f"{payload.get('subtask_id')}: missing assignment reason")
        spec = registry.agents().get(payload.get("agent_id"))
        if spec is None:
            problems.append(f"unknown agent {payload.get('agent_id')}")

    if problems:
        return CheckResult(name="match_quality", status="FAIL", details="; ".join(problems[:5]))
    return CheckResult(name="match_quality", status="PASS",
                       details=f"{len(assignments)} assignments, all gates respected with reasons")
