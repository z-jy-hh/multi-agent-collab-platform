"""MEM1 (workorder 93fef01e3a1761a1): data-driven failure-mode guardrails.

All guard CONTENT lives in configs/guardrails/failure_modes.yaml — this
module carries only the block FRAME (G3: entry texts must never be inlined
in Python source; keys/labels don't count). Default OFF via
``params.get("guardrails_enabled", False)`` (no runner.py/yaml change needed;
the three-level params merge already lands here). The off-state prefix is ""
so ``prompt_for`` returns the original text byte-identical (G1, both
supervisor and debate topologies).

Semantics (B-1, mirrored in the yaml header):
- ``global`` entries apply to every agent; ``by_agent.<agent_id>`` overrides
  PER ID (same id wins by by_agent, other global ids still apply) and
  appends by_agent-only ids after the global declaration order.
- Entry order == yaml declaration order; the block template is identical for
  every agent (same keys, same order, same format) — content varies only by
  configuration.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from core.paths import PROJECT_ROOT

DEFAULT_YAML = PROJECT_ROOT / "configs" / "guardrails" / "failure_modes.yaml"

# Block frame only — every entry's content comes from the yaml file.
_HEADER = "【失败模式护栏】以下条目摘自 failure_modes.yaml，逐条对照规避："
_FIELDS = ("id", "symptom", "guard", "judge_hint")


def _entry_lines(entry: dict) -> list[str]:
    """Fixed-shape lines: same keys, same order, same format for every
    agent and every entry (B-1-3)."""
    lines = []
    for i, field in enumerate(_FIELDS):
        value = str(entry.get(field, ""))
        if i == 0:
            lines.append("- %s: %s" % (field, value))
        else:
            lines.append("  %s: %s" % (field, value))
    lines.append("")
    return lines


def prefix_for(agent_id: str, params: dict, yaml_path: str | Path | None = None) -> str:
    """Return the guardrail block to PREPEND to this agent's prompt.

    Returns "" when disabled (default) — callers must treat "" as a no-op so
    the off-state prompt stays byte-identical. Fail-loud: a configured yaml
    path that does not exist raises (no silent empty injection).
    """
    if not params.get("guardrails_enabled", False):
        return ""
    path = Path(yaml_path or params.get("guardrails_path") or DEFAULT_YAML)
    if not path.exists():
        raise FileNotFoundError(f"guardrails yaml not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    overrides = {str(e.get("id")): e
                 for e in ((data.get("by_agent") or {}).get(agent_id) or [])}
    merged: list[dict] = []
    for entry in (data.get("global") or []):        # global declaration order
        merged.append(overrides.pop(str(entry.get("id")), entry))
    merged.extend(overrides.values())               # by_agent-only, decl order
    if not merged:
        return ""
    lines = [_HEADER, ""]
    for entry in merged:
        lines.extend(_entry_lines(entry))
    # End with a blank line so the original prompt text starts on its own
    # line; the ORIGINAL text itself is never modified (only prepended to).
    return "\n".join(lines) + "\n"
