"""P0 identity: bind slot/agent/profile, validate compositions, compile briefs.

The runtime never "runs a profile": it compiles a short prompt (brief) + a
selection id + pinned rule hashes, and the guard (P1) checks the checkable
rules afterwards. This module is pure and unit-testable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from core.schemas import AgentProfileSpec

from graphs.profiles import (
    ALLOWED_OUTPUT_SCHEMAS, CheckSpec, Preset, ProfileConfigError, RuleCheck,
    RuleEntry, match_check, validate_preset,
)

MAX_TEMP_RULES = 3
MAX_TEMP_CHARS = 500
MAX_BRIEF_CHARS = 6000
PRIORITY_LINE = "与平台约束冲突时以平台约束为准；规则之间以靠前者为准。"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Binding:
    slot_id: str
    agent_id: str
    profile_id: str


@dataclass
class Composition:
    agent_id: str
    profile_id: str
    rule_ids: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    temp_rules: list[str] = field(default_factory=list)


@dataclass
class ResolvedProfile:
    binding: Binding
    preset: Preset
    rules: list[RuleEntry]
    checks: dict[str, RuleCheck]
    skills: list[str]
    temp_rules: list[str]
    brief: str
    brief_sha256: str
    selection_id: str
    prompt_sha256: str

    def as_dict(self) -> dict:
        return {
            "slot": self.binding.slot_id,
            "agent_id": self.binding.agent_id,
            "profile_id": f"{self.preset.id}@{self.preset.version}",
            "profile_sha256": self.preset.sha256,
            "prompt_sha256": self.prompt_sha256,
            "selection_id": self.selection_id,
            "rule_ids": [r.id for r in self.rules],
            "rule_sha256s": {r.id: r.text_sha256 for r in self.rules},
            "skills": list(self.skills),
            "temp": list(self.temp_rules),
        }


def adapt(preset: Preset, *, slot_id: str, agent_id: str) -> AgentProfileSpec:
    """profile → AgentProfileSpec (core.matcher's input; matcher unchanged)."""
    domains = preset.capabilities.get("domains") or {}
    if not domains:
        raise ProfileConfigError(
            f"profile {preset.id!r} has no capabilities.domains; cannot be routed")
    return AgentProfileSpec(
        agent_id=agent_id,
        role=preset.name,
        domains=preset.domains,
        tools=list(preset.tools),
        strength={k: float(v) for k, v in domains.items()},
        cost=preset.cost,  # type: ignore[arg-type]
        prompt_ref=preset.path,
    )


def validate_composition(
    comp: Composition,
    presets: dict[str, Preset],
    rules: dict[str, RuleEntry],
    checks: list[RuleCheck],
    *,
    available_skills: set[str] | None = None,
    allowed_tools: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    preset = presets.get(comp.profile_id)
    if preset is None:
        return [f"{comp.agent_id}: unknown profile {comp.profile_id!r}"]
    if preset.status != "active":
        errors.append(f"{comp.agent_id}: profile {preset.id} is {preset.status}, not active")
    errors.extend(validate_preset(preset, allowed_tools=allowed_tools))
    if not comp.rule_ids and not comp.temp_rules and not comp.skills:
        # off is legal; nothing else to validate
        return errors
    seen: set[str] = set()
    for rid in comp.rule_ids:
        if rid in seen:
            errors.append(f"{comp.agent_id}: duplicate rule {rid}")
        seen.add(rid)
        rule = rules.get(rid)
        if rule is None:
            errors.append(f"{comp.agent_id}: unknown rule {rid}")
            continue
        check = match_check(rule, checks)
        if check is not None and check.enforcement == "hard":
            if check.check.type not in {
                "path_under", "required_section", "forbidden_phrase",
                "max_chars", "required_tool_call", "schema", "regex",
            }:
                errors.append(f"{comp.agent_id}: rule {rid} has unsupported check")
    for skill in comp.skills:
        if available_skills is not None and skill not in available_skills:
            errors.append(f"{comp.agent_id}: skill {skill!r} is not registered/installed")
    if len(comp.temp_rules) > MAX_TEMP_RULES:
        errors.append(f"{comp.agent_id}: at most {MAX_TEMP_RULES} temp rules")
    for t in comp.temp_rules:
        if not t.strip():
            errors.append(f"{comp.agent_id}: empty temp rule")
        if len(t) > MAX_TEMP_CHARS:
            errors.append(f"{comp.agent_id}: temp rule over {MAX_TEMP_CHARS} chars")
    return errors


def detect_conflicts(rule_checks: dict[str, RuleCheck]) -> list[str]:
    """Cheap, deterministic conflicts (no LLM): same check type, different value."""
    conflicts: list[str] = []
    by_type: dict[str, list[tuple[str, Any]]] = {}
    for rid, rc in rule_checks.items():
        by_type.setdefault(rc.check.type, []).append((rid, rc.check.value))
    for typ, rows in by_type.items():
        if typ not in {"max_chars", "path_under"}:
            continue
        values = {json.dumps(v, ensure_ascii=False, sort_keys=True) for _, v in rows}
        if len(values) > 1:
            conflicts.append(
                f"{typ} conflict: " + ", ".join(f"{rid}={v!r}" for rid, v in rows))
    return conflicts


def compose(
    comp: Composition,
    *,
    binding: Binding,
    presets: dict[str, Preset],
    rules: dict[str, RuleEntry],
    checks: list[RuleCheck],
    available_skills: set[str] | None = None,
    allowed_tools: set[str] | None = None,
) -> ResolvedProfile:
    errors = validate_composition(comp, presets, rules, checks,
                                  available_skills=available_skills,
                                  allowed_tools=allowed_tools)
    if errors:
        raise ProfileConfigError("; ".join(errors))
    preset = presets[comp.profile_id]
    selected: list[RuleEntry] = [rules[r] for r in comp.rule_ids]
    if not selected and not comp.temp_rules and not comp.skills:
        # Selecting a profile without rules/temp/skills must not change the
        # prompt; the behavior prompt is applied by the builder separately.
        return ResolvedProfile(
            binding=binding, preset=preset, rules=[], checks={}, skills=[],
            temp_rules=[], brief="", brief_sha256=_sha256(""),
            selection_id="sel-off", prompt_sha256=_sha256(""))
    pinned: dict[str, RuleCheck] = {}
    for rule in selected:
        rc = match_check(rule, checks)
        if rc is not None:
            pinned[rule.id] = rc
    conflicts = detect_conflicts(pinned)
    if conflicts:
        raise ProfileConfigError("; ".join(conflicts))

    lines = [f"【画像】{preset.name}（{preset.id}@{preset.version}）"]
    if selected or comp.temp_rules:
        lines.append("【绑定规则】" + PRIORITY_LINE)
        for i, rule in enumerate(selected, 1):
            rc = pinned.get(rule.id)
            tag = f"［{rc.enforcement}］" if rc else ""
            lines.append(f"{i}. {rule.text}{tag}")
        for i, t in enumerate(comp.temp_rules, 1):
            lines.append(f"T{i}. {t}［soft·只本次］")
    if comp.skills:
        lines.append("【技能（仅引用，不产生工具能力）】" + ", ".join(comp.skills))
    brief = "\n".join(lines)
    if len(brief) > MAX_BRIEF_CHARS:
        raise ProfileConfigError(
            f"{comp.agent_id}: profile brief is {len(brief)} chars "
            f"(max {MAX_BRIEF_CHARS}); select fewer rules or split the task")
    canonical = {
        "profile": f"{preset.id}@{preset.version}",
        "rules": [{"id": r.id, "sha256": r.text_sha256} for r in selected],
        "temp": [{"text": t, "sha256": _sha256(t)} for t in comp.temp_rules],
        "skills": list(comp.skills),
    }
    selection = "sel-" + _sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True))[:12]
    return ResolvedProfile(
        binding=binding, preset=preset, rules=selected, checks=pinned,
        skills=list(comp.skills), temp_rules=list(comp.temp_rules),
        brief=brief, brief_sha256=_sha256(brief),
        selection_id=selection, prompt_sha256=_sha256(brief),
    )


def resolve(
    plan: dict[str, dict],
    *,
    slots: dict[str, str],
    presets: dict[str, Preset],
    rules: dict[str, RuleEntry],
    checks: list[RuleCheck],
    available_skills: set[str] | None = None,
    allowed_tools: set[str] | None = None,
) -> dict[str, ResolvedProfile]:
    """plan: agent_id -> {profile, rules, skills, temp}; slots: agent_id -> slot_id."""
    out: dict[str, ResolvedProfile] = {}
    for agent_id, row in plan.items():
        comp = Composition(
            agent_id=agent_id,
            profile_id=str(row.get("profile") or ""),
            rule_ids=list(row.get("rules") or []),
            skills=list(row.get("skills") or []),
            temp_rules=list(row.get("temp") or []),
        )
        binding = Binding(slot_id=slots.get(agent_id, agent_id),
                          agent_id=agent_id, profile_id=comp.profile_id)
        out[agent_id] = compose(
            comp, binding=binding, presets=presets, rules=rules, checks=checks,
            available_skills=available_skills, allowed_tools=allowed_tools)
    return out


def verify_pinned(resolved: ResolvedProfile, rules: dict[str, RuleEntry]) -> list[str]:
    """A rule edited after the run was pinned must fail loudly, not be re-read."""
    errors: list[str] = []
    for rule in resolved.rules:
        current = rules.get(rule.id)
        if current is None:
            errors.append(f"rule {rule.id} disappeared")
        elif current.text_sha256 != rule.text_sha256:
            errors.append(f"rule {rule.id} changed (text sha mismatch)")
    return errors


def run_selection_id(resolved: dict[str, ResolvedProfile]) -> str:
    """Stable id for the whole run's binding table (order-independent)."""
    parts = [f"{aid}:{r.selection_id}" for aid, r in sorted(resolved.items())]
    return "sel-" + _sha256("\n".join(parts))[:12]


def short_brief(resolved: ResolvedProfile) -> str:
    """O4: after the first full brief, remind only the hard rules (one line each)."""
    text = {r.id: r.text for r in resolved.rules}
    lines = [f"【画像提醒】{text.get(rid, rid)}"
             for rid, rc in resolved.checks.items() if rc.enforcement == "hard"]
    return "\n".join(lines)


