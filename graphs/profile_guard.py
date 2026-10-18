"""P1: profile guard — the only place that turns "rules" into "must".

Delivery/pinning are done by the runner/builder; this module checks the
*observable* output and tool calls after each execute. Hard failures get ONE
corrective retry, then a loud ProfileViolation. Soft failures are recorded and
never block. Checks are code-only (no LLM judge).
"""

from __future__ import annotations

import re
from typing import Any

from graphs.identity import ResolvedProfile
from graphs.profiles import CheckSpec, RuleCheck

WINDOWS_PATH = re.compile(r"[A-Za-z]:\\[^\s\"'<>|,;]+")


class ProfileViolation(RuntimeError):
    """A hard profile rule failed after the single retry."""


def _paths(text: str) -> list[str]:
    return WINDOWS_PATH.findall(text or "")


def evaluate(rc: RuleCheck, output: str, meta: dict) -> tuple[str, str]:
    """Return (status, evidence) where status is pass|warn|fail."""
    spec: CheckSpec = rc.check
    value = spec.value
    out = output or ""
    if spec.type == "path_under":
        root = str(value or "").lower()
        bad = [p for p in _paths(out) if not p.lower().startswith(root)]
        if bad:
            return "fail", f"paths outside {value}: {bad[:3]}"
        return "pass", ""
    if spec.type == "required_section":
        need = value if isinstance(value, (list, tuple)) else [value]
        missing = [s for s in need if str(s) and str(s) not in out]
        return ("fail", f"missing sections: {missing}") if missing else ("pass", "")
    if spec.type == "forbidden_phrase":
        bad = [s for s in (value if isinstance(value, (list, tuple)) else [value])
               if str(s) and str(s) in out]
        return ("fail", f"forbidden phrases: {bad}") if bad else ("pass", "")
    if spec.type == "max_chars":
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return "fail", f"bad max_chars value {value!r}"
        return ("fail", f"{len(out)} > {limit}") if len(out) > limit else ("pass", "")
    if spec.type == "required_tool_call":
        calls = list(meta.get("tool_calls") or [])
        if value:
            need = value if isinstance(value, (list, tuple)) else [value]
            missing = [v for v in need if v not in calls]
            return ("fail", f"missing tool calls: {missing}; got {calls}") if missing else ("pass", "")
        return ("pass", "") if calls else ("fail", "no tool_call recorded")
    if spec.type == "regex":
        if not re.search(str(value or ""), out):
            return "fail", f"regex did not match: {value!r}"
        return "pass", ""
    # schema is enforced by the builder's output validation, not here.
    return "pass", ""


def _emit(bus, run_id: str, agent_id: str, payload: dict) -> None:
    if bus is None:
        return
    try:
        bus.emit("profile_check", run_id, payload, agent_id)
    except Exception:
        pass


def execute_with_checks(inner, agent_id: str, prompt: str, context: dict,
                        resolved: ResolvedProfile, *, bus=None, run_id: str = "") -> Any:
    """Run ``inner.execute`` then enforce the resolved profile's checks."""
    if resolved.brief and resolved.brief not in prompt:
        raise ProfileViolation(
            f"{agent_id}: profile brief was not delivered to the prompt")
    result = inner.execute(agent_id, prompt, context)
    hard_failed: list[str] = []
    for attempt in (1, 2):
        if attempt == 2:
            corrective = prompt + (
                "\n\n【纠正】上一次输出未通过以下画像规则：" + "；".join(hard_failed)
                + "。请严格修正后重新输出。")
            result = inner.execute(agent_id, corrective, context)
        hard_failed = []
        for rule_id, rc in resolved.checks.items():
            status, evidence = evaluate(rc, getattr(result, "output", "") or "",
                                        dict(getattr(result, "meta", {}) or {}))
            if status == "fail" and rc.enforcement == "hard":
                hard_failed.append(f"{rule_id}: {evidence}")
                _emit(bus, run_id, agent_id, {
                    "rule_id": rule_id, "status": "fail", "attempt": attempt,
                    "evidence": evidence, "selection_id": resolved.selection_id})
            elif status == "fail":
                _emit(bus, run_id, agent_id, {
                    "rule_id": rule_id, "status": "warn", "attempt": attempt,
                    "evidence": evidence, "selection_id": resolved.selection_id})
        if not hard_failed:
            return result
    raise ProfileViolation(
        f"{agent_id}: hard profile rules failed after retry: {hard_failed}")
