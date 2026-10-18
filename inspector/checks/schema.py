"""check: schema — did any node output violate its declared output_schema?"""

from core.schemas import CheckResult


def run(ctx: dict) -> CheckResult:
    violations = [e for e in ctx["events"] if e.type == "schema_violation"]
    if violations:
        details = "; ".join(
            f"{v.payload.get('node')}: {v.payload.get('problems')}" for v in violations[:5]
        )
        return CheckResult(name="schema", status="FAIL",
                           details=f"{len(violations)} violations — {details}")
    return CheckResult(name="schema", status="PASS", details="all node outputs matched their output_schema")
