"""Pydantic contracts shared across every layer (appendix A: core/schemas.py)."""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

CostTier = Literal["low", "mid", "high"]
OpScope = Literal["blackboard", "private"]
CheckStatus = Literal["PASS", "FAIL", "WARN"]
EvolveDecision = Literal["KEEP", "ROLLBACK"]


class AgentProfileSpec(BaseModel):
    """Capability profile declared in configs/agents.yaml (C7)."""

    agent_id: str
    role: str
    domains: list[str]
    tools: list[str] = Field(default_factory=list)
    strength: dict[str, float]
    cost: CostTier
    prompt_ref: str = ""

    @field_validator("strength")
    @classmethod
    def _strength_range(cls, v: dict[str, float]) -> dict[str, float]:
        for k, s in v.items():
            if not 0.0 <= float(s) <= 10.0:
                raise ValueError(f"strength[{k}]={s} outside 0-10")
        return v


class SubTask(BaseModel):
    subtask_id: str
    description: str
    required_capabilities: list[str] = Field(default_factory=list)
    assigned_agent: str | None = None
    assignment_score: float | None = None
    assignment_reason: str = ""


class BlackboardOp(BaseModel):
    """One recorded write (op log entry). Append-only; never mutated (C13)."""

    op_id: str
    session_id: str
    agent_id: str
    key: str
    value: Any
    clock: dict[str, int]
    hlc: tuple[int, int, str]
    ts: float = Field(default_factory=time.time)
    scope: OpScope = "blackboard"


class Event(BaseModel):
    seq: int = 0
    run_id: str
    type: str
    agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)


class CheckResult(BaseModel):
    name: str
    status: CheckStatus
    details: str = ""


class HealthReport(BaseModel):
    run_id: str
    checks: list[CheckResult] = Field(default_factory=list)
    score: float = 0.0
    summary: str = ""

    def add(self, name: str, status: CheckStatus, details: str = "") -> None:
        self.checks.append(CheckResult(name=name, status=status, details=details))

    @property
    def all_passed(self) -> bool:
        return all(c.status == "PASS" for c in self.checks)


class AgentResult(BaseModel):
    agent_id: str
    output: str
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Minimal JSON-schema subset validator — the runtime guard used by the graph
# builder to check node outputs against the topology YAML's output_schema.
# Pure function; supports type/required/properties/enum/items (enough for the
# topology YAMLs in configs/topologies/).
# ---------------------------------------------------------------------------

def validate_against_schema(data: Any, schema: dict) -> list[str]:
    """Return a list of violations (empty = valid). Never raises."""
    errors: list[str] = []
    _check(data, schema, "$", errors)
    return errors


def _check(data: Any, schema: dict, path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        return
    expected = schema.get("type")
    if expected is not None:
        type_ok = {
            "object": isinstance(data, dict),
            "array": isinstance(data, list),
            "string": isinstance(data, str),
            "number": isinstance(data, (int, float)) and not isinstance(data, bool),
            "boolean": isinstance(data, bool),
            "null": data is None,
        }.get(expected, True)
        if not type_ok:
            errors.append(f"{path}: expected type {expected}, got {type(data).__name__}")
            return
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: {data!r} not in enum {schema['enum']}")
    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                errors.append(f"{path}: missing required key {key!r}")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in data:
                _check(data[key], sub, f"{path}.{key}", errors)
    if isinstance(data, list) and "items" in schema:
        for i, item in enumerate(data):
            _check(item, schema["items"], f"{path}[{i}]", errors)
