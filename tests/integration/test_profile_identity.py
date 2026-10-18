"""P0 identity-model tests: rule parser, presets, adapter, composition, pinning.

These are the I1/I3/I4 criteria from identity-model-plan.md plus parser negatives
(the failure modes that a naive `session_brief` reuse would introduce).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from core.matcher import score_agent
from graphs import identity, profiles


PROFILE_TEXT = """---
version: 12
---

## Confirmed（已确认）

1. **写盘落点：优先 D 盘**
   正文一。
   - 标签：治理偏好、安全红线
   - 依据：用户 2026-09-16 原话
2. **征询 / 请求授权时先给三件套**
   - 标签：  决策习惯（若你倾向"治理偏好"，不要改标签）

## Hypothesis（待验证）

1. **随便猜的规则**
   - 标签：决策习惯

## 已拒绝提议（不要再提）

1. **被拒绝的规则**
   - 标签：决策习惯
"""

TAG_VOCAB = """# tags
| 标签 | 含义 |
|---|---|
| 决策习惯 | x |
| 协作规范 | x |
| 治理偏好 | x |
| 安全红线 | x |
"""


@pytest.fixture()
def lib(tmp_path):
    (tmp_path / "20-insights").mkdir()
    (tmp_path / "20-insights" / "user-profile.md").write_text(PROFILE_TEXT, encoding="utf-8")
    (tmp_path / "20-insights" / "tag-vocabulary.md").write_text(TAG_VOCAB, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def rules(lib):
    vocab = profiles.load_tag_vocabulary(lib / "20-insights" / "tag-vocabulary.md")
    entries, version = profiles.parse_user_profile(
        lib / "20-insights" / "user-profile.md", tag_vocab=vocab)
    return {r.title: r for r in entries}, version


@pytest.fixture()
def checks(tmp_path):
    doc = {"checks": [
        {"title_contains": "写盘", "enforcement": "hard",
         "check": {"type": "path_under", "value": "D:\\dsh-work"}},
        {"title_contains": "征询", "enforcement": "hard",
         "check": {"type": "required_section", "value": ["做什么", "如何做", "什么效果"]}},
    ]}
    path = tmp_path / "rule_checks.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return profiles.load_rule_checks(path)


def test_parser_only_confirmed_and_id_is_content_derived(lib):
    vocab = profiles.load_tag_vocabulary(lib / "20-insights" / "tag-vocabulary.md")
    entries, version = profiles.parse_user_profile(
        lib / "20-insights" / "user-profile.md", tag_vocab=vocab)
    assert version == "12"
    assert len(entries) == 2, "Hypothesis/rejected must never be exposed"
    titles = [e.title for e in entries]
    assert any("写盘落点" in t for t in titles)
    assert any("征询" in t for t in titles)
    assert all("依据" not in e.text for e in entries), "依据 prose must be stripped"
    write = next(e for e in entries if "写盘" in e.title)
    assert write.tags == ("治理偏好", "安全红线")
    assert write.unknown_tags == ()
    assert write.id.startswith("up-conf-") and len(write.id) == len("up-conf-") + 8

    # same title -> same id; body edit -> same id, new text sha
    entries2, _ = profiles.parse_user_profile(
        lib / "20-insights" / "user-profile.md", tag_vocab=vocab)
    assert [e.id for e in entries] == [e.id for e in entries2]
    first_sha = write.text_sha256
    (lib / "20-insights" / "user-profile.md").write_text(
        PROFILE_TEXT.replace("正文一。", "正文一，改了一个字。"), encoding="utf-8")
    entries3, _ = profiles.parse_user_profile(
        lib / "20-insights" / "user-profile.md", tag_vocab=vocab)
    write3 = next(e for e in entries3 if "写盘" in e.title)
    assert write3.id == write.id
    assert write3.text_sha256 != first_sha


def test_check_attachment_and_enforcement(checks, rules):
    by_title, _ = rules
    write = next(e for t, e in by_title.items() if "写盘" in t)
    ask = next(e for t, e in by_title.items() if "征询" in t)
    assert profiles.match_check(write, checks).enforcement == "hard"
    assert profiles.match_check(write, checks).check.type == "path_under"
    assert profiles.match_check(ask, checks).check.type == "required_section"


def _preset(tmp_path, *, pid="probe", domains=None, tags=None, tools=None,
            schema="worker_output") -> dict:
    doc = {
        "id": pid, "version": 1, "status": "active", "name": pid,
        "description": "", "cost": "low",
        "capabilities": {"domains": domains if domains is not None else {"research": 9},
                         "tags": tags if tags is not None else ["research"]},
        "output_schema": schema, "tools": tools if tools is not None else ["search_local"],
        "budget": {"turns": 4}, "behavior": {"prompt": "p"},
        "rules": [], "skills": [],
    }
    path = tmp_path / "profiles" / "presets" / f"{pid}@v1.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return doc


def _registry(tmp_path, docs) -> Path:
    rows = []
    for doc in docs:
        p = tmp_path / "profiles" / "presets" / f"{doc['id']}@v1.yaml"
        rows.append({"id": doc["id"], "version": 1, "status": "active",
                     "path": f"profiles/presets/{p.name}",
                     "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    reg = tmp_path / "profiles" / "registry.json"
    reg.write_text(__import__("json").dumps({"profiles": rows}), encoding="utf-8")
    return reg


def test_preset_load_and_adapter_routes_by_capability(tmp_path):
    a = _preset(tmp_path, pid="researcher", domains={"research": 9}, tags=["research"])
    b = _preset(tmp_path, pid="writer", domains={"writing": 9}, tags=["writing"])
    presets = profiles.load_presets(_registry(tmp_path, [a, b]))
    assert set(presets) == {"researcher", "writer"}
    spec_a = identity.adapt(presets["researcher"], slot_id="w1", agent_id="a1")
    spec_b = identity.adapt(presets["writer"], slot_id="w2", agent_id="a2")
    assert spec_a.domains == ["research"] and spec_a.strength["research"] == 9.0
    assert score_agent(spec_a, ["research"]) > score_agent(spec_b, ["research"])


def test_preset_without_domains_is_not_routable(tmp_path):
    doc = _preset(tmp_path, pid="broken", domains={}, tags=[])
    presets = profiles.load_presets(_registry(tmp_path, [doc]))
    with pytest.raises(profiles.ProfileConfigError):
        identity.adapt(presets["broken"], slot_id="w1", agent_id="a1")


def test_composition_rejects_expansion_and_unknowns(tmp_path, checks, rules):
    doc = _preset(tmp_path, pid="probe")
    presets = profiles.load_presets(_registry(tmp_path, [doc]))
    by_title, _ = rules
    write = next(e for t, e in by_title.items() if "写盘" in t)
    rules_by_id = {write.id: write}
    comp = identity.Composition(
        agent_id="a1", profile_id="probe", rule_ids=[write.id],
        skills=["not-installed"], temp_rules=["x"] * 4)
    errors = identity.validate_composition(
        comp, presets, rules_by_id, checks,
        available_skills={"ai-memory"}, allowed_tools={"files"})
    text = " | ".join(errors)
    assert "outside allowlist" in text or "outside" in text
    assert "not registered" in text
    assert "at most 3" in text


def test_composition_without_rules_is_off(tmp_path, checks):
    doc = _preset(tmp_path, pid="probe")
    presets = profiles.load_presets(_registry(tmp_path, [doc]))
    resolved = identity.compose(
        identity.Composition(agent_id="a1", profile_id="probe"),
        binding=identity.Binding("w1", "a1", "probe"),
        presets=presets, rules={}, checks=checks)
    assert resolved.brief == "" and resolved.selection_id == "sel-off"


def test_compose_is_deterministic_and_pin_detects_drift(tmp_path, checks, rules):
    doc = _preset(tmp_path, pid="probe")
    presets = profiles.load_presets(_registry(tmp_path, [doc]))
    by_title, _ = rules
    entries = list(by_title.values())
    rules_by_id = {e.id: e for e in entries}
    comp = identity.Composition(agent_id="a1", profile_id="probe",
                                rule_ids=[e.id for e in entries],
                                skills=["ai-memory"], temp_rules=["本次先给结论"])
    r1 = identity.compose(comp, binding=identity.Binding("w1", "a1", "probe"),
                          presets=presets, rules=rules_by_id, checks=checks,
                          available_skills={"ai-memory"})
    r2 = identity.compose(comp, binding=identity.Binding("w1", "a1", "probe"),
                          presets=presets, rules=rules_by_id, checks=checks,
                          available_skills={"ai-memory"})
    assert r1.selection_id == r2.selection_id
    assert r1.brief == r2.brief
    assert all(e.text in r1.brief for e in entries)
    assert "只本次" in r1.brief
    assert identity.verify_pinned(r1, rules_by_id) == []

    changed = {e.id: profiles.RuleEntry(
        id=e.id, title=e.title, text=e.text + "（改）", tags=e.tags,
        unknown_tags=e.unknown_tags,
        text_sha256=hashlib.sha256((e.text + "（改）").encode()).hexdigest(),
        source_version=e.source_version) for e in entries}
    assert identity.verify_pinned(r1, changed)


def test_modes_endpoint_exposes_profile_catalog():
    from api.routes import list_modes

    data = list_modes()
    assert data.get("profile_error") is None, data.get("profile_error")
    assert len(data["agents"]) == 9
    assert {a["id"] for a in data["agents"]} >= {"researcher", "writer"}
    assert len(data["profiles"]) == 9
    assert all("capabilities" in p and "output_schema" in p for p in data["profiles"])
    assert isinstance(data["skills"], list)
    assert len(data["rules"]) == 23, "only the Confirmed section is exposed"
    assert all({"id", "text", "tags", "text_sha256"} <= set(r) for r in data["rules"])
    # existing keys must stay (additive-only widening)
    assert {"modes", "topologies", "protocol_versions"} <= set(data)


def test_profile_drawer_is_wired_in_console():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "api" / "static" / "index.html").read_text(
        encoding="utf-8")
    assert 'id="btn-profile"' in html
    assert 'id="profile-drawer"' in html
    assert "/static/ui/profiles.js" in html
    assert "body.profiles = state.profilePlan" in html


def test_run_request_accepts_optional_profiles():
    from api.routes import RunRequest

    req = RunRequest(mode="single", task="x",
                     profiles={"a1": {"profile": "researcher", "rules": []}})
    assert req.profiles == {"a1": {"profile": "researcher", "rules": []}}
    # omitted => None (old callers unchanged)
    assert RunRequest(mode="single", task="x").profiles is None


def test_conflicting_checks_are_rejected(tmp_path, rules):
    by_title, _ = rules
    entries = list(by_title.values())
    conflicting = {
        entries[0].id: profiles.RuleCheck("x", "hard", profiles.CheckSpec("max_chars", 300)),
        entries[1].id: profiles.RuleCheck("y", "hard", profiles.CheckSpec("max_chars", 800)),
    }
    conflicts = identity.detect_conflicts(conflicting)
    assert conflicts and "max_chars" in conflicts[0]
    # compose must refuse when the pinned checks conflict
    import graphs.identity as ident
    original = ident.detect_conflicts
    try:
        ident.detect_conflicts = lambda _pinned: ["forced conflict"]
        doc = _preset(tmp_path, pid="probe")
        presets = profiles.load_presets(_registry(tmp_path, [doc]))
        rules_by_id = {e.id: e for e in entries}
        with pytest.raises(profiles.ProfileConfigError):
            identity.compose(
                identity.Composition(agent_id="a1", profile_id="probe",
                                     rule_ids=[e.id for e in entries]),
                binding=identity.Binding("w1", "a1", "probe"),
                presets=presets, rules=rules_by_id, checks=[])
    finally:
        ident.detect_conflicts = original
