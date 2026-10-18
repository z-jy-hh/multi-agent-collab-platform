"""check: memory_invariant (C10) — per-agent vector-clock components grow
strictly by one per published op, and every recorded op stays inside the
session it declares (the guard raises NamespaceViolation at runtime; here we
verify the persisted log shows no anomalies).

W4-1: the dsh bridge writes its ``run_id -> sessionId -> cwd`` mapping into the
same op log via ``DshSessionManager._record`` with an empty vector clock. That
is an **audit/mapping record**, not an agent publish, so it is exempt from the
per-agent clock check (otherwise every native/hetero run fails with 83.3).
The exemption is deliberately narrow: agent must be ``dsh_bridge`` AND the key
must be the mapping key ``dsh_session::<run_id>``; anything else is still
checked.
"""

from core.schemas import CheckResult

BRIDGE_AGENT = "dsh_bridge"
BRIDGE_KEY_PREFIX = "dsh_session::"


def _is_bridge_audit_op(op) -> bool:
    return (getattr(op, "agent_id", None) == BRIDGE_AGENT
            and str(getattr(op, "key", "")).startswith(BRIDGE_KEY_PREFIX))


def run(ctx: dict) -> CheckResult:
    memory = ctx["memory"]
    session_id = ctx["session_id"]
    problems: list[str] = []

    ops = memory._store.ops_for_session(session_id)  # noqa: SLF001 - inspector is infra
    checked = [op for op in ops if not _is_bridge_audit_op(op)]
    per_agent: dict[str, int] = {}
    for op in checked:
        expected = per_agent.get(op.agent_id, 0) + 1
        actual = op.clock.get(op.agent_id, 0)
        if actual != expected:
            problems.append(
                f"{op.agent_id} clock component {actual} != {expected} at op {op.op_id}"
            )
        per_agent[op.agent_id] = actual

    foreign = [o.op_id for o in ops if o.session_id != session_id]
    if foreign:
        problems.append(f"ops from other sessions leaked in: {foreign[:3]}")

    if problems:
        return CheckResult(name="memory_invariant", status="FAIL",
                           details="; ".join(problems[:5]))
    return CheckResult(
        name="memory_invariant", status="PASS",
        details=f"{len(checked)} agent ops ({len(ops)} total), "
                f"{len(per_agent)} agents, clocks strictly monotonic")
