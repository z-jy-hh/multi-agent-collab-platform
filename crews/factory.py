"""CrewAI leaf adapter factory (C15).

Rules enforced here:
- memory=False on EVERY Crew (its built-in Chroma store would cross sessions
  and violate C10 — the engine's own memory layer is the only memory);
- telemetry off via env BEFORE crewai is imported;
- a fresh Crew instance per call — never reuse across sessions;
- the LLM comes from core.llm config (env > .env > defaults).

CrewAI storage redirect (round-6 D1): implemented ONCE in
core/crewai_storage.py; the entry points main.py / server.py /
tests/conftest.py call it BEFORE any crewai import, and this module calls it
again here (idempotent) so the leaf adapter is safe standalone.
"""

from __future__ import annotations

import os

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from core.crewai_storage import redirect_crewai_storage  # noqa: E402

redirect_crewai_storage()  # D1: single implementation; applied before crewai import

import yaml  # noqa: E402

from core.errors import ConfigError  # noqa: E402
from core.llm import _load_env_file  # noqa: E402
from core.paths import configs_dir  # noqa: E402

_TOOL_FACTORIES: dict[str, callable] = {}


def _tool_factories() -> dict:
    global _TOOL_FACTORIES
    if not _TOOL_FACTORIES:
        from crews.tools.calc import calc
        from crews.tools.files import read_file
        from crews.tools.search_local import search_local

        _TOOL_FACTORIES.update({"calc": calc, "files": read_file, "search_local": search_local})
    return _TOOL_FACTORIES


def load_profiles() -> dict[str, dict]:
    path = configs_dir() / "agents.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {a["agent_id"]: a for a in data.get("agents", [])}


def worker_model_name(model: str | None = None) -> str:
    """The model the crew worker will actually use, WITHOUT CrewAI's ``openai/``
    prefix. This is the honest source for ``agent_card.model`` — the platform
    judge LLM (``core/llm.py``) is a different object and must not be reported
    as the worker's model.
    """
    _load_env_file()
    return model or os.getenv("MODEL") or "deepseek-chat"


def crewai_llm(base_url: str | None = None, api_key: str | None = None, model: str | None = None):
    """Build a CrewAI LLM from the C9 config chain. Requires a key — callers
    without one must use ScriptedRuntime/FakeLLM instead."""
    _load_env_file()
    api_key = api_key or os.getenv("OPENAI_API_KEY") or ""
    base_url = base_url or os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com/v1"
    model = worker_model_name(model)
    if not api_key:
        raise ConfigError("no OPENAI_API_KEY — offline mode must use ScriptedRuntime")
    from crewai import LLM

    return LLM(model=f"openai/{model}", base_url=base_url, api_key=api_key)


def make_crew(agent_id: str, role_prompt: str, task_text: str):
    """Fresh Crew for one execution (C15). crew_factory target for
    core.runtime.CrewAIRuntime."""
    import crewai
    from crewai.tools import tool as crewai_tool

    profiles = load_profiles()
    profile = profiles.get(agent_id)
    if profile is None:
        raise ConfigError(f"unknown agent_id: {agent_id}")

    tools = []
    for name in profile.get("tools", []):
        fn = _tool_factories().get(name)
        if fn is not None:
            # crewai 1.x decorator: bare call uses the function name + docstring
            tools.append(crewai_tool(fn))

    llm = crewai_llm()
    agent = crewai.Agent(
        role=profile["role"],
        goal=f"以 {profile['role']} 的职责完成任务,输出可直接使用的结果",
        backstory=role_prompt or profile["role"],
        llm=llm,
        tools=tools,
        verbose=False,
        memory=False,
    )
    task = crewai.Task(
        description=task_text,
        expected_output="结构化、可直接使用的文本结果",
        agent=agent,
    )
    # C15: memory=False — engine-level memory is the single source of truth
    return crewai.Crew(agents=[agent], tasks=[task], verbose=False, memory=False)
