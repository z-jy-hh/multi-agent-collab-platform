"""Agent runtime protocol layer (C4).

Graphs / inspector / evolver only ever see ``AgentRuntime``. CrewAI is a leaf
adapter: ``CrewAIRuntime`` receives an injected ``crew_factory`` callable
(provided by crews/factory.py at wiring time), so core holds ZERO reference
to crewai — the core layer runs and tests without crewai installed.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from core.errors import ConfigError
from core.schemas import AgentResult


class AgentRuntime(Protocol):
    def execute(self, agent_id: str, prompt: str, context: dict) -> AgentResult:
        ...


class ScriptedRuntime:
    """Canned answers keyed by agent_id — offline topology smoke tests (D8)."""

    def __init__(self, script: dict[str, list[str]]) -> None:
        self._script: dict[str, list[str]] = {k: list(v) for k, v in script.items()}

    def execute(self, agent_id: str, prompt: str, context: dict) -> AgentResult:
        seq = self._script.get(agent_id)
        if not seq:
            raise ConfigError(f"no scripted output registered for agent {agent_id!r}")
        out = seq.pop(0) if len(seq) > 1 else seq[0]
        return AgentResult(
            agent_id=agent_id,
            output=out,
            meta={"runtime": "scripted", "prompt_chars": len(prompt)},
        )


class CrewAIRuntime:
    """Leaf adapter. ``crew_factory(agent_id, role_prompt, task_text)`` returns
    an object with ``.kickoff()`` (a CrewAI Crew built fresh per call — C15:
    never share instances across sessions)."""

    def __init__(self, crew_factory: Callable[[str, str, str], Any]) -> None:
        if not callable(crew_factory):
            raise ConfigError("CrewAIRuntime needs a callable crew_factory")
        self._factory = crew_factory

    def execute(self, agent_id: str, prompt: str, context: dict) -> AgentResult:
        role_prompt = context.get("role_prompt", "")
        crew = self._factory(agent_id, role_prompt, prompt)
        raw = crew.kickoff()
        output = getattr(raw, "raw", None)
        output = str(output) if output is not None else str(raw)
        return AgentResult(
            agent_id=agent_id,
            output=output,
            meta={"runtime": "crewai", "prompt_chars": len(prompt)},
        )
