"""P0 identity: read-only rule catalog + platform profile presets.

This module is deliberately pure: no LLM, no network, no writes. It reads the
memory library's *Confirmed* rules (the only human-approved section) and the
platform's ``profiles/`` presets, and validates them. Runtime composition lives
in ``graphs/identity.py``.

Design constraints (final plan v1):
- rule id is content-derived (``up-conf-<sha8(title)>``) because the library has
  no stable ids; the rule **text** is pinned with sha256.
- only the ``Confirmed`` section is exposed; Hypothesis/rejected entries must
  never reach an agent.
- a rule can only be ``hard`` if it carries a ``check``; otherwise it is soft.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_LIBRARY = Path(r"D:\ai-memory-skill")
ALLOWED_OUTPUT_SCHEMAS = {
    "worker_output", "review_verdict", "plan", "debate_argument", "final_report",
}
ALLOWED_CHECK_TYPES = {
    "path_under", "required_section", "forbidden_phrase", "max_chars",
    "required_tool_call", "schema", "regex",
}
ENFORCEMENT = {"hard", "soft"}
PLATFORM_TOOL_ALLOWLIST = {"search_local", "calc", "files", "tavily"}


class ProfileConfigError(ValueError):
    """Loud config error: bad profile/rule/check. Never silently degrade."""


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def library_root() -> Path:
    """Memory library root; env override for tests/other machines."""
    return Path(os.environ.get("MEMORY_LIBRARY") or DEFAULT_LIBRARY)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha8(text: str) -> str:
    return _sha256(text)[:8]


def _norm_title(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class RuleEntry:
    id: str
    title: str
    text: str
    tags: tuple[str, ...]
    unknown_tags: tuple[str, ...]
    text_sha256: str
    source_version: str

    def as_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "text": self.text,
            "tags": list(self.tags), "unknown_tags": list(self.unknown_tags),
            "text_sha256": self.text_sha256, "source_version": self.source_version,
        }


@dataclass(frozen=True)
class CheckSpec:
    type: str
    value: Any = None

    def __post_init__(self) -> None:
        if self.type not in ALLOWED_CHECK_TYPES:
            raise ProfileConfigError(
                f"unknown check type {self.type!r}; allowed={sorted(ALLOWED_CHECK_TYPES)}")


@dataclass(frozen=True)
class RuleCheck:
    title_contains: str
    enforcement: str
    check: CheckSpec
    applies_to: tuple[str, ...] = ("all",)

    def __post_init__(self) -> None:
        if self.enforcement not in ENFORCEMENT:
            raise ProfileConfigError(
                f"enforcement must be hard|soft, got {self.enforcement!r}")
        if not self.title_contains.strip():
            raise ProfileConfigError("rule check needs a non-empty title_contains")


@dataclass
class Preset:
    id: str
    version: int
    status: str
    name: str
    description: str
    cost: str
    capabilities: dict
    output_schema: str
    tools: list[str]
    budget: dict
    behavior: dict
    rules: list[str]
    skills: list[str]
    path: str = ""
    sha256: str = ""

    @property
    def domains(self) -> list[str]:
        caps = self.capabilities or {}
        return list(caps.get("tags") or [])

    def as_dict(self) -> dict:
        return {
            "id": self.id, "version": self.version, "status": self.status,
            "name": self.name, "description": self.description, "cost": self.cost,
            "capabilities": self.capabilities, "output_schema": self.output_schema,
            "tools": list(self.tools), "budget": dict(self.budget),
            "behavior": dict(self.behavior), "rules": list(self.rules),
            "skills": list(self.skills), "path": self.path, "sha256": self.sha256,
        }


def load_tag_vocabulary(path: Path | None = None) -> set[str]:
    path = path or (library_root() / "20-insights" / "tag-vocabulary.md")
    if not path.exists():
        raise ProfileConfigError(f"tag vocabulary not found: {path}")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    tags: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\|\s*([^|]+?)\s*\|", line)
        if m and m.group(1) not in ("标签", "---"):
            tags.add(m.group(1).strip())
    return tags


def _clean_body(raw: str) -> str:
    """Strip tag lines and the prose '依据' clauses; keep the rule itself."""
    lines: list[str] = []
    for line in raw.splitlines():
        if re.match(r"^\s*-\s*标签[:：]", line):
            continue
        if re.match(r"^\s*-\s*(补充)?依据[:：]", line):
            continue
        line = re.sub(r"（依据[:：][^）]*）", "", line)
        line = re.sub(r"\((依据[:：][^)]*)\)", "", line)
        if line.strip():
            lines.append(line.strip())
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def parse_user_profile(profile_path: Path | None = None,
                       tag_vocab: set[str] | None = None) -> tuple[list[RuleEntry], str]:
    """Parse ONLY the ``Confirmed`` section. Returns (entries, version)."""
    path = profile_path or (library_root() / "20-insights" / "user-profile.md")
    if not path.exists():
        raise ProfileConfigError(f"user profile not found: {path}")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    vm = re.search(r"^version:\s*(\d+)", text, re.M)
    version = vm.group(1) if vm else "?"
    vocab = tag_vocab if tag_vocab is not None else load_tag_vocabulary()

    headings = list(re.finditer(r"^##\s+(.+)$", text, re.M))
    block = ""
    for i, m in enumerate(headings):
        if m.group(1).strip().lower().startswith("confirmed"):
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            block = text[m.end():end]
            break
    if not block:
        raise ProfileConfigError(f"no '## Confirmed' section in {path}")

    items = list(re.finditer(r"^\s*(\d+)\.\s+\*\*(.+?)\*\*(.*)$", block, re.M))
    out: list[RuleEntry] = []
    for i, m in enumerate(items):
        nxt = items[i + 1].start() if i + 1 < len(items) else len(block)
        body = block[m.end():nxt]
        title = _norm_title(m.group(2))
        raw_tags: list[str] = []
        for tm in re.finditer(r"^\s*-\s*标签[:：]\s*(.+)$", body, re.M):
            tag_line = re.sub(r"[（(].*?[）)]", "", tm.group(1))
            for part in re.split(r"[、,，]", tag_line):
                part = part.strip()
                if part:
                    raw_tags.append(part)
        known = tuple(t for t in raw_tags if t in vocab)
        unknown = tuple(t for t in raw_tags if t not in vocab)
        text_body = _clean_body(body)
        full_text = f"{title}：{text_body}" if text_body else title
        out.append(RuleEntry(
            id=f"up-conf-{_sha8(_norm_title(title))}",
            title=title, text=full_text, tags=known, unknown_tags=unknown,
            text_sha256=_sha256(full_text), source_version=version,
        ))
    return out, version


def load_rule_checks(path: Path | None = None) -> list[RuleCheck]:
    path = path or (repository_root() / "profiles" / "rule_checks.yaml")
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    checks: list[RuleCheck] = []
    for row in data.get("checks") or []:
        c = row.get("check") or {}
        checks.append(RuleCheck(
            title_contains=str(row.get("title_contains") or ""),
            enforcement=str(row.get("enforcement") or "soft"),
            check=CheckSpec(type=str(c.get("type") or ""), value=c.get("value")),
            applies_to=tuple(row.get("applies_to") or ("all",)),
        ))
    return checks


def match_check(rule: RuleEntry, checks: list[RuleCheck]) -> RuleCheck | None:
    for c in checks:
        if c.title_contains and c.title_contains in rule.title:
            return c
    return None


def load_presets(registry_path: Path | None = None) -> dict[str, Preset]:
    registry_path = registry_path or (repository_root() / "profiles" / "registry.json")
    if not registry_path.exists():
        raise ProfileConfigError(f"profile registry not found: {registry_path}")
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    rows = data.get("profiles") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        raise ProfileConfigError(f"profile registry is empty: {registry_path}")
    out: dict[str, Preset] = {}
    for row in rows:
        p = Path(row["path"])
        if not p.is_absolute():
            p = registry_path.parent.parent / p
        if not p.exists():
            raise ProfileConfigError(f"preset file missing: {p}")
        raw = p.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if row.get("sha256") and row["sha256"] != sha:
            raise ProfileConfigError(
                f"preset sha drift for {row.get('id')}: registry={row['sha256'][:12]} file={sha[:12]}")
        doc = yaml.safe_load(raw.decode("utf-8")) or {}
        for key in ("id", "version", "status", "name", "capabilities",
                    "output_schema", "tools", "budget", "behavior"):
            if key not in doc:
                raise ProfileConfigError(f"preset {p.name} missing field {key!r}")
        preset = Preset(
            id=str(doc["id"]), version=int(doc["version"]), status=str(doc["status"]),
            name=str(doc["name"]), description=str(doc.get("description") or ""),
            cost=str(doc.get("cost") or "mid"),
            capabilities=dict(doc["capabilities"] or {}),
            output_schema=str(doc["output_schema"]),
            tools=list(doc.get("tools") or []),
            budget=dict(doc.get("budget") or {}),
            behavior=dict(doc.get("behavior") or {}),
            rules=list(doc.get("rules") or []),
            skills=list(doc.get("skills") or []),
            path=str(p), sha256=sha,
        )
        if preset.id in out:
            raise ProfileConfigError(f"duplicate preset id {preset.id!r}")
        out[preset.id] = preset
    return out


def validate_preset(preset: Preset, *, allowed_tools: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    if preset.status not in ("active", "draft", "archived"):
        errors.append(f"{preset.id}: bad status {preset.status!r}")
    if not preset.capabilities.get("domains"):
        errors.append(f"{preset.id}: capabilities.domains is required for routing")
    if preset.output_schema not in ALLOWED_OUTPUT_SCHEMAS:
        errors.append(f"{preset.id}: output_schema {preset.output_schema!r} not allowed")
    if allowed_tools is not None:
        extra = sorted(set(preset.tools) - allowed_tools)
        if extra:
            errors.append(f"{preset.id}: tools outside allowlist: {extra}")
    if not isinstance(preset.budget, dict):
        errors.append(f"{preset.id}: budget must be a mapping")
    return errors


def skills_catalog(library: Path | None = None,
                   host_skills: Path | None = None) -> list[dict]:
    """Read-only merge of memory-library skill assets and host-installed skills."""
    lib = library or library_root()
    host = host_skills or (Path.home() / ".dsh" / "skills")
    found: dict[str, dict] = {}
    for root, source in ((lib / "40-skills", "memory"), (host, "host")):
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if not p.is_dir():
                continue
            row = found.setdefault(p.name, {"id": p.name, "installed": False,
                                           "sources": []})
            if source not in row["sources"]:
                row["sources"].append(source)
            if source == "host":
                row["installed"] = True
    return [found[k] for k in sorted(found)]

