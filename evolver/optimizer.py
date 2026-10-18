"""Evolver optimizer: weaknesses → candidate file refinements.

Deterministic rule-based edits (offline, no LLM) — each candidate carries the
full new content, the reason, and a unified diff. Nothing is applied here:
application goes through evolver.guard (whitelist) + evolver.versions
(snapshot) and only happens in ``--evolve auto`` mode (C8).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from evolver.analyzer import Weakness

# weakness kind → (target file, refinement line appended to the prompt)
RULES: dict[str, tuple[str, str]] = {
    "schema": ("prompts/supervisor/supervisor.v1.md",
               "\n补充约束(v2):输出必须是严格合法 JSON,不允许 markdown 代码块或解释文字。"),
    "rework": ("prompts/worker/writer.v1.md",
               "\n补充约束(v2):只组织黑板上已发布的结论,压缩为三段,不复述评审原文。"),
    "match": ("configs/agents.yaml",
              "# 补充(v2):评审发现派活失败——检查 strength 是否低估了相关领域。\n"),
    "arbitration": ("prompts/supervisor/supervisor.v1.md",
                    "\n补充约束(v2):子任务切分必须互斥,避免两个执行者写同一分片。"),
    "memory": ("configs/workflow.yaml",
               "# 补充(v2):检查会话命名空间校验配置。\n"),
}


@dataclass
class Candidate:
    file: str                # whitelist-relative path
    new_content: str
    reason: str
    diff: str
    weakness_kinds: list[str] = field(default_factory=list)


def propose_changes(weaknesses: list[Weakness], project_root: Path) -> list[Candidate]:
    """One candidate per distinct target file, merging all applicable rules."""
    by_file: dict[str, Candidate] = {}
    root = Path(project_root)
    for w in weaknesses:
        rule = RULES.get(w.kind)
        if rule is None:
            continue
        rel, refinement = rule
        cand = by_file.get(rel)
        if cand is None:
            path = root / rel
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            new = old + refinement
            cand = Candidate(file=rel, new_content=new, reason="",
                             diff=_diff(old, new, rel))
            by_file[rel] = cand
        cand.weakness_kinds.append(w.kind)
        cand.reason = f"refine {rel} due to: {', '.join(by_file[rel].weakness_kinds)}"
    return list(by_file.values())


def _diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{name}", tofile=f"b/{name}"))
