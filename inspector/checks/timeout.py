"""check: timeout — cooperative loop bounds (C18): node executions and
worker dispatches must stay within the topology's recursion budget."""

from core.schemas import CheckResult


def run(ctx: dict) -> CheckResult:
    limit = int(ctx["params"].get("recursion_limit", 48))
    node_enters = sum(1 for e in ctx["events"] if e.type == "node_enter")
    if node_enters > limit:
        return CheckResult(name="timeout", status="FAIL",
                           details=f"{node_enters} node executions exceed recursion budget {limit}")
    if node_enters == 0:
        return CheckResult(name="timeout", status="FAIL", details="no nodes executed")
    return CheckResult(name="timeout", status="PASS",
                       details=f"{node_enters} node executions within budget {limit}")
