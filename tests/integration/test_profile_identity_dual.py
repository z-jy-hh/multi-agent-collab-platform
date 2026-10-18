"""P1: PROFILE_IDENTITY=0/1 — delivery, evidence, and shape equivalence."""
from __future__ import annotations

import uuid

from core.events import EventBus
from core.llm import FakeLLM
from core.schemas import AgentResult
from graphs import profiles
from graphs.runner import default_llm_script, run_topology


PROFILE_TEXT = """---
version: 3
---

## Confirmed（已确认）

1. **写盘落点：优先 D 盘**
   只写 D:\\dsh-work。
   - 标签：治理偏好

## Hypothesis（待验证）

1. **猜测不要用**
   - 标签：治理偏好
"""

TAG_VOCAB = """| 标签 | 含义 |
|---|---|
| 治理偏好 | x |
| 决策习惯 | x |
"""


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def execute(self, agent_id, prompt, context):
        self.calls.append((agent_id, prompt))
        return AgentResult(agent_id=agent_id, output="ok", meta={})


def _library(tmp_path):
    root = tmp_path / "lib"
    (root / "20-insights").mkdir(parents=True, exist_ok=True)
    (root / "20-insights" / "user-profile.md").write_text(PROFILE_TEXT, encoding="utf-8")
    (root / "20-insights" / "tag-vocabulary.md").write_text(TAG_VOCAB, encoding="utf-8")
    return root


def _rule_id(root) -> str:
    vocab = profiles.load_tag_vocabulary(root / "20-insights" / "tag-vocabulary.md")
    entries, _ = profiles.parse_user_profile(
        root / "20-insights" / "user-profile.md", tag_vocab=vocab)
    return entries[0].id


def _run(tmp_path, monkeypatch, *, flag: str, with_profiles: bool):
    root = _library(tmp_path)
    monkeypatch.setenv("MEMORY_LIBRARY", str(root))
    monkeypatch.setenv("PROFILE_IDENTITY", flag)
    rec = Recorder()
    bus = EventBus()
    params = {}
    if with_profiles:
        params = {"profiles": {"researcher": {
            "profile": "researcher", "rules": [_rule_id(root)]}}}
    final = run_topology(
        "single", "写一句话", run_id=f"p1-{flag}-{int(with_profiles)}-{uuid.uuid4().hex[:8]}",
        session_id="p1-s", llm=FakeLLM(script=default_llm_script),
        runtime=rec, bus=bus, persistent=False, params=params)
    return final, rec, bus


def test_flag_on_injects_brief_and_emits_evidence(tmp_path, monkeypatch):
    final, rec, bus = _run(tmp_path, monkeypatch, flag="1", with_profiles=True)
    assert final["status"] == "done"
    assert rec.calls, "researcher was not executed"
    agent_id, prompt = rec.calls[0]
    assert agent_id == "researcher"
    assert prompt.startswith("【画像】"), prompt[:80]
    assert "（researcher@1）" in prompt and "写盘落点" in prompt and "只写" in prompt
    events = bus.replay(0)
    types = [e.type for e in events]
    assert "profile_bound" in types
    outputs = [e for e in events if e.type == "agent_output"
               and e.agent_id == "researcher"]
    assert outputs and outputs[0].payload["profile_selection_id"].startswith("sel-")


def test_flag_off_ignores_profiles_and_keeps_old_prompt(tmp_path, monkeypatch):
    final, rec, bus = _run(tmp_path, monkeypatch, flag="0", with_profiles=True)
    assert final["status"] == "done"
    agent_id, prompt = rec.calls[0]
    assert not prompt.startswith("【画像】")
    assert "写盘落点" not in prompt
    events = bus.replay(0)
    assert "profile_bound" not in [e.type for e in events]
    outputs = [e for e in events if e.type == "agent_output"
               and e.agent_id == "researcher"]
    assert outputs and "profile_selection_id" not in outputs[0].payload


def test_flag_on_without_profiles_is_off(tmp_path, monkeypatch):
    final, rec, bus = _run(tmp_path, monkeypatch, flag="1", with_profiles=False)
    assert final["status"] == "done"
    _, prompt = rec.calls[0]
    assert not prompt.startswith("【画像】")
    assert "profile_bound" not in [e.type for e in bus.replay(0)]


def test_shape_is_equivalent_except_profile_fields(tmp_path, monkeypatch):
    _, _, bus_off = _run(tmp_path, monkeypatch, flag="0", with_profiles=True)
    _, _, bus_on = _run(tmp_path, monkeypatch, flag="1", with_profiles=True)
    types_off = [e.type for e in bus_off.replay(0)]
    types_on = [e.type for e in bus_on.replay(0)]
    assert types_on == [t for t in types_off] + ["profile_bound"] or \
        types_on == ["profile_bound"] + types_off
    off_out = next(e for e in bus_off.replay(0)
                   if e.type == "agent_output" and e.agent_id == "researcher")
    on_out = next(e for e in bus_on.replay(0)
                  if e.type == "agent_output" and e.agent_id == "researcher")
    assert set(on_out.payload) - set(off_out.payload) == {"profile_selection_id"}
