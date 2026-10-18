"""check: cost — outputs and LLM call volume stay within the configured
budget (WARN only; numbers are the deliverable, not a hard gate)."""

from core.schemas import CheckResult


def run(ctx: dict) -> CheckResult:
    outputs = [e for e in ctx["events"] if e.type == "agent_output"]
    total_chars = sum(int(e.payload.get("output_chars", 0)) for e in outputs)
    budget = int(ctx["params"].get("output_char_budget", 100_000))
    llm = ctx.get("llm")
    calls = getattr(llm, "calls", None)
    detail = f"{len(outputs)} agent outputs, {total_chars} chars"
    if calls is not None:
        detail += f", {calls} LLM calls"
    if total_chars > budget:
        return CheckResult(name="cost", status="WARN",
                           details=detail + f" over budget {budget}")
    return CheckResult(name="cost", status="PASS", details=detail + f" (budget {budget})")
