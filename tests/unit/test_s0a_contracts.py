"""S0-a contract tests (M4): machine-readable error codes.

NEGATIVE-first: the point is that a consumer can decide "which refusal"
WITHOUT parsing a human-readable message.
"""
from __future__ import annotations

import pytest

from agent_bridge.errors import SpiError


def test_spi_error_code_defaults_to_kind_error():
    err = SpiError("boom", kind="dispatch", backend="dsh")
    assert err.code == "dispatch.error"


def test_spi_error_code_explicit_is_preserved():
    err = SpiError("boom", kind="dispatch", backend="dsh", code="backend.unreachable")
    assert err.code == "backend.unreachable"
    assert err.kind == "dispatch"


def test_agent_card_carries_executor_and_platform_model():
    # layercheck runs core tests from a temp copy that omits the graph layer.
    pytest.importorskip("graphs.builder", reason="graph layer absent in this scope")
    from graphs.builder import RunContext

    class _Spec:
        role, domains, strength, cost = "writer", ["writing"], {"writing": 9}, "mid"

    class _Registry:
        def get_agent(self, agent_id):
            return _Spec()

    class _LLM:
        model = "deepseek-chat"

    class _ScriptedRuntime:
        pass

    ctx = RunContext(run_id="r1", session_id="s1", task="t", mode="supervisor",
                     runtime=_ScriptedRuntime(), llm=_LLM(), registry=_Registry(),
                     memory=object(), bus=object(), config={})
    card = ctx.agent_card("writer")
    # B1: the substrate really used, not a hardcoded "crewai"
    assert card["executor"] == "scripted"
    # B2: the worker model is unknown at this layer; never claim the judge's model
    assert card["model"] is None
    assert card["platform_model"] == "deepseek-chat"
    assert "backend_session_id" not in card, "agent_card must not invent a session id"


def test_agent_card_reports_the_worker_model_when_known():
    pytest.importorskip("graphs.builder", reason="graph layer absent in this scope")
    from graphs.builder import RunContext

    class _Spec:
        role, domains, strength, cost = "writer", ["writing"], {"writing": 9}, "mid"

    class _Registry:
        def get_agent(self, agent_id):
            return _Spec()

    class _LLM:
        model = "judge-model"

    ctx = RunContext(run_id="r1", session_id="s1", task="t", mode="supervisor",
                     runtime=object(), llm=_LLM(), registry=_Registry(),
                     memory=object(), bus=object(), config={},
                     worker_model="deepseek-chat")
    card = ctx.agent_card("writer")
    # W0-1: model = worker's real model; platform_model = judge's model
    assert card["model"] == "deepseek-chat"
    assert card["platform_model"] == "judge-model"
