"""Evolver guard — CODE-LEVEL enforcement of the C8 whitelist.

Not prompt self-discipline: apply_candidate refuses (raises GuardViolation)
any write outside ``prompts/`` and ``configs/``, and validates edited YAML
against the agent-profile schema (strength 0-10, cost enum, referenced
agents/topologies must exist). The evolver can never touch code files.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from core.errors import GuardViolation
from core.paths import PROJECT_ROOT

WHITELIST = ("prompts", "configs")


def _resolved(relative: str) -> Path:
    candidate = (PROJECT_ROOT / relative).resolve()
    root = PROJECT_ROOT.resolve()
    if root not in candidate.parents:
        raise GuardViolation(f"path escapes project root: {relative!r}")
    if candidate.relative_to(root).parts[0] not in WHITELIST:
        raise GuardViolation(
            f"evolver may only write under {WHITELIST}; refused: {relative!r}")
    return candidate


def validate_candidate(relative: str, new_content: str) -> Path:
    """Whitelist + content checks. Returns the resolved target path."""
    target = _resolved(relative)
    if relative.endswith((".yaml", ".yml")):
        _validate_yaml(new_content, target)
    return target


def apply_candidate(relative: str, new_content: str) -> Path:
    """Atomic write (temp file + rename) after validation (C8). Binary write
    so the content's line endings are preserved exactly."""
    target = validate_candidate(relative, new_content)
    import tempfile

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=str(target.parent), delete=False) as tmp:
        tmp.write(new_content.encode("utf-8"))
        tmp_path = Path(tmp.name)
    tmp_path.replace(target)
    return target


def _validate_yaml(content: str, target: Path) -> None:
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise GuardViolation(f"invalid YAML in {target.name}: {exc}") from exc

    if isinstance(data, dict) and "agents" in data:  # agents.yaml profile rules
        known_ids = set()
        for agent in data.get("agents", []):
            aid = agent.get("agent_id", "?")
            known_ids.add(aid)
            for domain, strength in (agent.get("strength") or {}).items():
                if not 0.0 <= float(strength) <= 10.0:
                    raise GuardViolation(
                        f"{target.name}: strength[{domain}]={strength} outside 0-10")
            if agent.get("cost") not in ("low", "mid", "high"):
                raise GuardViolation(f"{target.name}: cost={agent.get('cost')!r} not in enum")
            ref = agent.get("prompt_ref", "")
            if ref and not (PROJECT_ROOT / ref).exists():
                raise GuardViolation(f"{target.name}: prompt_ref {ref!r} does not exist")

    if isinstance(data, dict) and "nodes" in data:  # topology file rules
        for node in data.get("nodes", []):
            agent = node.get("agent")
            if agent:
                agents_file = PROJECT_ROOT / "configs" / "agents.yaml"
                profiles = yaml.safe_load(agents_file.read_text(encoding="utf-8")) or {}
                ids = {a.get("agent_id") for a in profiles.get("agents", [])}
                if agent not in ids:
                    raise GuardViolation(
                        f"{target.name}: node references unknown agent {agent!r}")
