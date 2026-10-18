"""P2: pinned baseline for PROFILE_IDENTITY=1 (fixed fixture, deterministic).

The fixture library is local to the test, so the baseline is independent of the
real memory library's future edits. Regenerate with:
    set P2_WRITE_BASELINE=1 && pytest ... -k profile_baseline
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from core.events import EventBus
from core.llm import FakeLLM
from core.paths import run_dir
from graphs import profiles
from graphs.runner import default_llm_script, run_topology

REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "reports" / "p2_profile_baseline.json"

PROFILE_TEXT = """---
version: 7
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


def _normalize(events) -> list[dict]:
    out = []
    for ev in events:
        d = ev.model_dump() if hasattr(ev, "model_dump") else dict(ev)
        d.pop("ts", None)
        d.pop("seq", None)
        d.pop("run_id", None)
        out.append(d)
    return out


def _run(tmp_path, monkeypatch, mode: str, task: str, run_id: str) -> list[dict]:
    root = tmp_path / "lib"
    if not root.exists():
        (root / "20-insights").mkdir(parents=True)
        (root / "20-insights" / "user-profile.md").write_text(PROFILE_TEXT, encoding="utf-8")
        (root / "20-insights" / "tag-vocabulary.md").write_text(TAG_VOCAB, encoding="utf-8")
    monkeypatch.setenv("MEMORY_LIBRARY", str(root))
    monkeypatch.setenv("PROFILE_IDENTITY", "1")
    vocab = profiles.load_tag_vocabulary(root / "20-insights" / "tag-vocabulary.md")
    entries, _ = profiles.parse_user_profile(
        root / "20-insights" / "user-profile.md", tag_vocab=vocab)
    rule_id = entries[0].id
    default_llm_script._review_state = None
    shutil.rmtree(run_dir(run_id), ignore_errors=True)
    bus = EventBus()
    final = run_topology(
        mode, task, run_id=run_id, session_id="p2base-session",
        llm=FakeLLM(script=default_llm_script), bus=bus, persistent=False,
        params={"profiles": {"researcher": {"profile": "researcher",
                                            "rules": [rule_id]}},
                "recursion_limit": 48})
    assert final["status"] == "done"
    return _normalize(bus.replay(0))


def test_profile_baseline(tmp_path, monkeypatch):
    got = {
        "single": _run(tmp_path, monkeypatch, "single",
                       "P2 基线任务：1+1 等于几？只回答数字。", "p2base-single"),
        "supervisor": _run(tmp_path, monkeypatch, "supervisor",
                           "P2 基线任务：用一句话说明多 Agent 协同的代价。",
                           "p2base-supervisor"),
    }
    # supervisor fan-out writes in nondeterministic order; pin the multiset
    got["supervisor"].sort(key=lambda e: json.dumps(e, sort_keys=True, ensure_ascii=False))
    if os.environ.get("P2_WRITE_BASELINE") == "1":
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(got, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        return
    assert BASELINE.exists(), "run with P2_WRITE_BASELINE=1 once to pin the baseline"
    pinned = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert got == pinned, "flag=1 profile baseline drifted"
