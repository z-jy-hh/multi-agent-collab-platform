"""W4-1 unit tests for ``agent_bridge.runtime`` (no graphs/api imports).

Covers the parts that do not need an ACP process: config parsing/validation,
the queue counter, the fallback policy, the executor projection and the
NativeLLMAdapter shape (interface only — it must NOT be wired into the runner).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.events import EventBus
from core.errors import CancelledRun
from core.runtime import CrewAIRuntime, ScriptedRuntime

from agent_bridge.errors import SpiError
from agent_bridge.runtime import (
    ACPRuntime, NativeConfigError, NativeLLMAdapter, runtime_from_env, runtime_kind)


# -- config ------------------------------------------------------------------
def test_runtime_from_env_rejects_unknown_mode(monkeypatch):
    monkeypatch.setenv("NATIVE_RUNTIME", "bogus")
    with pytest.raises(NativeConfigError) as exc:
        runtime_from_env("r1", "s1")
    assert exc.value.code == "native.bad_env"


def test_runtime_from_env_rejects_unknown_fallback(monkeypatch):
    monkeypatch.setenv("NATIVE_RUNTIME", "dsh")
    monkeypatch.setenv("NATIVE_FALLBACK", "silent")
    with pytest.raises(NativeConfigError):
        runtime_from_env("r1", "s1")


def test_runtime_from_env_parses_agents_and_limits(monkeypatch):
    monkeypatch.setenv("NATIVE_RUNTIME", "dsh")
    monkeypatch.setenv("NATIVE_AGENTS", "researcher, analyst")
    monkeypatch.setenv("NATIVE_QUEUE_MAX", "3")
    monkeypatch.setenv("NATIVE_MAX_TURNS", "5")
    rt = runtime_from_env("r1", "s1")
    assert rt._agents_arg == frozenset({"researcher", "analyst"})
    assert rt._queue_max == 3
    assert rt._max_turns == 5


@pytest.mark.parametrize("queue_max", [0, -1])
def test_constructor_rejects_bad_queue(queue_max):
    with pytest.raises(NativeConfigError):
        ACPRuntime(run_id="r", platform_session_id="s", agents=frozenset({"researcher"}),
                   queue_max=queue_max)


def test_constructor_rejects_bad_permission_mode():
    with pytest.raises(NativeConfigError) as exc:
        ACPRuntime(run_id="r", platform_session_id="s",
                   agents=frozenset({"researcher"}),
                   permission_mode="danger-full-access")
    assert exc.value.code == "native.bad_mode"


# -- runtime kind / executor projection --------------------------------------
def test_runtime_kind_matches_the_three_known_substrates():
    assert runtime_kind(ScriptedRuntime({})) == "scripted"
    assert runtime_kind(CrewAIRuntime(lambda *a: None)) == "crewai"
    assert runtime_kind(object()) == "object"
    assert runtime_kind(None) == "none"


def test_non_native_agent_projects_the_inner_kind():
    rt = ACPRuntime(run_id="r", platform_session_id="s", inner=ScriptedRuntime({}))
    assert rt.executor_for("writer") == "scripted"
    assert rt.describe("writer") == {}
    assert rt.native_agents == frozenset()


# -- queue -------------------------------------------------------------------
def test_queue_counter_rejects_overflow_and_releases():
    rt = ACPRuntime(run_id="r", platform_session_id="s", queue_max=1)
    rt._enter()
    try:
        with pytest.raises(SpiError) as exc:
            rt._enter()
        assert exc.value.code == "native.queue_full"
        assert exc.value.kind == "budget"
    finally:
        rt._leave(True)
    # a released counter can be taken again
    rt._enter()
    rt._leave(True)


def test_queue_wait_observes_cancel():
    import threading

    class _Ev:
        def __init__(self):
            self._ev = threading.Event()

        def is_set(self):
            return self._ev.is_set()

        def set(self):
            self._ev.set()

    ev = _Ev()
    # queue_max=2 so the second caller actually waits (queue-full is checked
    # before the wait loop by design).
    rt = ACPRuntime(run_id="r", platform_session_id="s", queue_max=2,
                    cancel_event=ev, queue_wait_s=5.0)
    rt._enter()
    ev.set()
    try:
        with pytest.raises(CancelledRun):
            rt._enter()
    finally:
        rt._leave(True)


# -- fallback policy ---------------------------------------------------------
def test_config_errors_never_fall_back():
    rt = ACPRuntime(run_id="r", platform_session_id="s", fallback="crewai",
                    inner=ScriptedRuntime({}))
    cfg = SpiError("bad", kind="config", backend="dsh", code="native.registry_gate")
    assert rt._can_fallback("researcher", cfg) is False
    assert rt._can_fallback("researcher",
                            SpiError("boom", kind="process-died", backend="dsh")) is True


def test_fallback_runs_once_per_agent():
    rt = ACPRuntime(run_id="r", platform_session_id="s", fallback="crewai",
                    inner=ScriptedRuntime({"researcher": ["x"]}))
    rt._fallback_done.add("researcher")
    assert rt._can_fallback("researcher",
                            SpiError("boom", kind="timeout", backend="dsh")) is False


# -- NativeLLMAdapter --------------------------------------------------------
def test_native_llm_adapter_calls_native_turn_without_routing():
    class _Rt:
        def __init__(self):
            self.calls = []

        def native_turn(self, text, *, agent_id="platform-llm"):
            self.calls.append((text, agent_id))
            return "native-text"

    rt = _Rt()
    adapter = NativeLLMAdapter(rt)
    assert adapter.complete("q", system="sys") == "native-text"
    assert rt.calls == [("sys\nq", "platform-llm")]


def test_native_llm_adapter_is_not_wired_into_the_runner():
    # the interface exists but the runner must not construct it in W4-1
    pytest.importorskip("graphs.runner", reason="graph layer absent in this scope")
    import inspect

    from graphs import runner

    src = inspect.getsource(runner)
    assert "NativeLLMAdapter" not in src


# -- O6: context-pressure session rotation -----------------------------------
def test_usage_update_above_threshold_requests_rotation():
    rt = ACPRuntime(run_id="r", platform_session_id="s", rotate_at=0.7,
                    bus=EventBus())
    rt._active_turn_seq = 1
    rt._sink(SimpleNamespace(type="usage_update",
                             payload={"raw": {"used": 800, "size": 1000}}))
    assert rt._rotate_requested is True


def test_usage_update_below_threshold_or_disabled_does_not_rotate():
    for used, rotate_at in ((500, 0.7), (900, 0.0)):
        rt = ACPRuntime(run_id="r", platform_session_id="s", rotate_at=rotate_at,
                        bus=EventBus())
        rt._active_turn_seq = 1
        rt._sink(SimpleNamespace(type="usage_update",
                                 payload={"raw": {"used": used, "size": 1000}}))
        assert rt._rotate_requested is False


def test_maybe_rotate_closes_the_old_handle():
    class Handle:
        def __init__(self):
            self.closed = None

        def close(self, *, reason):
            self.closed = reason

    rt = ACPRuntime(run_id="r", platform_session_id="s", bus=EventBus())
    handle = Handle()
    rt._handle = handle
    rt._rotate_requested = True
    rt._maybe_rotate()
    assert handle.closed == "context rotation"
    assert rt._handle is None
    assert rt._rotate_requested is False
