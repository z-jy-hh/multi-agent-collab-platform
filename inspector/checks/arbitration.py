"""check: arbitration — every detected conflict must have a resolution
(action + value), and conflict-free rounds must say so explicitly."""

from core.schemas import CheckResult


def run(ctx: dict) -> CheckResult:
    mode = ctx["mode"]
    if mode != "supervisor":
        return CheckResult(name="arbitration", status="PASS",
                           details=f"mode {mode} has no arbitration stage")

    arbitration = [e for e in ctx["events"] if e.type == "arbitration"]
    if not arbitration:
        return CheckResult(name="arbitration", status="FAIL",
                           details="supervisor run without an arbitration decision")

    conflicts = set()
    for e in ctx["events"]:
        if e.type == "node_enter":
            continue
    # conflicts are reported by the arbitrate node itself; each conflict key
    # must have a resolution with action + value
    unresolved: list[str] = []
    for e in arbitration:
        key = e.payload.get("key")
        if key is None:
            continue  # the explicit "no conflict" report
        if not e.payload.get("action") or "value" not in e.payload:
            unresolved.append(str(key))
    if unresolved:
        return CheckResult(name="arbitration", status="FAIL",
                           details=f"unresolved conflicts: {unresolved}")
    resolved = [e for e in arbitration if e.payload.get("key") is not None]
    detail = (f"{len(resolved)} conflict(s) resolved"
              if resolved else "no concurrent writes — explicitly reported")
    return CheckResult(name="arbitration", status="PASS", details=detail)
