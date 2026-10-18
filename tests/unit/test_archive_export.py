"""Unit tests: H1 archive-export snapshot (V2 section 5 A1-A7 + V2.1 fixes).

Discriminators ("fake fix must go RED", V2 section 5):
- mutation 1: stage-1 masking removed (deep pass => identity) =>
  test_a2b_discriminator_mask_tokens_present and
  test_stage1_bypass_is_rejected go RED (self-check rejects, no file).
  Samples therefore live in EVENT payloads / final report (deep-pass-only
  paths), NOT only in the task text (the task is masked by a direct
  redact() call that mutation 1 does not touch).
- mutation 2: error-path hook removed from graphs/runner.py =>
  tests/integration/test_archive_export_runner.py::test_runner_error_path_exports
  goes RED (runner-hook integration lives in the integration layer because
  layercheck's core scope bans graphs imports in tests/unit).

print/assert messages ASCII-only (GBK console rule).
"""

import json
import shutil

import pytest

from core.events import EventBus
from core.paths import run_dir
from core.schemas import Event, HealthReport

RUN_ID = "11111111-2222-3333-4444-555555555555"
SAMPLES = {
    "sk": "sk-abcdefghijklmnopqrstuvwxyz",
    "mail": "admin@corp.example.com",
    "phone": "13812345678",
    "pwd": "password=hunter2secret",
}
TASK = (f"key={SAMPLES['sk']} {SAMPLES['pwd']} mail={SAMPLES['mail']} "
        f"tel={SAMPLES['phone']}")


def _ev(seq, type_, payload=None, agent_id=None):
    return Event(seq=seq, run_id=RUN_ID, type=type_, agent_id=agent_id,
                 payload=payload or {}, ts=1700000000.0 + seq)


def _health():
    h = HealthReport(run_id=RUN_ID, score=90.0, summary="all good")
    h.add("probe_check", "PASS", "fine")
    return h


def _rich_events():
    # review comments + assignment note carry samples => masked ONLY by the
    # stage-1 deep pass (discriminator mechanics, see module docstring).
    return [
        _ev(1, "node_enter", {"node": "plan"}, "supervisor"),
        _ev(2, "plan", {"subtasks": 2}, "supervisor"),
        _ev(3, "node_enter", {"node": "match"}, "supervisor"),
        _ev(4, "assignment", {"subtask_id": "s1", "score": 3,
                              "note": f"key {SAMPLES['sk']}"}),
        _ev(5, "node_enter", {"node": "worker"}, "researcher"),
        _ev(6, "review", {"verdict": "PASS",
                          "comments": f"contact {SAMPLES['mail']} ok"},
            "reviewer"),
        _ev(7, "health", {"score": 90.0}),
        _ev(8, "run_finished", {"status": "done"}),
    ]


@pytest.fixture(autouse=True)
def _clean_shared_run_dir():
    # The suite shares one fixed RUN_ID run dir; a leftover loud-failure
    # marker from an earlier test (Windows file locks can defeat rmtree) must
    # never leak into the next test => wipe before AND after each test.
    shutil.rmtree(run_dir(RUN_ID), ignore_errors=True)
    yield
    shutil.rmtree(run_dir(RUN_ID), ignore_errors=True)


@pytest.fixture
def drop_env(tmp_path, monkeypatch):
    out = tmp_path / "outbox"
    monkeypatch.setenv("ARCHIVE_EXPORT_ENABLED", "1")
    monkeypatch.setenv("ARCHIVE_EXPORT_DIR", str(out))
    return out


def _export(drop_dir, **kw):
    from core.archive_export import export_snapshot
    kw.setdefault("run_id", RUN_ID)
    kw.setdefault("session_id", "sess-1")
    kw.setdefault("mode", "supervisor")
    kw.setdefault("task", TASK)
    kw.setdefault("events", _rich_events())
    kw.setdefault("final", {"status": "done",
                            "final_report": "# conclusion\nstatus ok"})
    kw.setdefault("health", _health())
    kw.setdefault("bus", None)
    kw.setdefault("status", "done")
    kw.setdefault("config", {"nodes": [{"id": "plan", "agent": "supervisor"},
                                       {"id": "worker",
                                        "agent": "researcher"}]})
    return export_snapshot(**kw)


def _read_md(drop_dir):
    files = sorted(drop_dir.glob("*.md"))
    assert len(files) == 1, f"expected exactly 1 drop file, got {len(files)}"
    return files[0], files[0].read_text(encoding="utf-8")


def _json_block(text):
    return json.loads(text.split("```json\n", 1)[1].rsplit("```", 1)[0])


def test_a1_fields_complete(drop_env):
    path = _export(drop_env)
    assert path is not None and path.exists()
    _, text = _read_md(drop_env)
    snap = _json_block(text)
    required = {"schema_version", "snapshot_id", "run_id", "session_id",
                "exported_at", "platform_commit", "producer", "status",
                "topology", "node_agents", "verdict", "health",
                "audit_five_checks", "trace_digest", "redaction",
                "source_trust", "sha256", "sha16", "task_summary",
                "task_sha16"}
    missing = required - set(snap)
    assert not missing, f"missing fields: {sorted(missing)}"
    assert snap["schema_version"] == 1
    assert snap["producer"] == "multi_agent_collab"
    assert snap["source_trust"] == "platform"
    assert snap["status"] == "done"
    assert snap["audit_five_checks"] is None
    assert set(snap["trace_digest"]) == {"total_events", "kept", "truncated",
                                         "kept_by_reason", "items"}
    assert snap["redaction"]["rules_version"] == "n2-v2"
    assert isinstance(snap["redaction"]["counts_by_kind"], dict)
    assert set(snap["health"]) == {"score", "summary", "checks"}
    assert snap["node_agents"] == {"plan": "supervisor",
                                   "worker": "researcher"}
    assert snap["verdict"] == "PASS"
    fm = text.split("---\n", 2)[1]
    assert f"sha256: {snap['sha256']}" in fm
    assert f"id: {snap['snapshot_id']}" in fm
    assert snap["snapshot_id"] == f"{RUN_ID}-{snap['sha16']}"


def test_a2a_injected_samples_never_plaintext(drop_env):
    path = _export(drop_env)
    assert path is not None
    _, text = _read_md(drop_env)
    for label, sample in SAMPLES.items():
        assert sample not in text, f"plaintext leaked: {label}"
    marker = run_dir(RUN_ID) / "archive_export_error.json"
    assert not marker.exists(), "clean export must not leave a failure marker"
    shutil.rmtree(run_dir(RUN_ID), ignore_errors=True)


def test_a2b_discriminator_mask_tokens_present(drop_env):
    # mutation 1 (stage-1 deep pass removed) => export REJECTS => no file =>
    # this test goes RED. Samples ride event payloads (deep-pass-only).
    _export(drop_env)
    _, text = _read_md(drop_env)
    assert "\u27e8REDACTED:" in text, \
        "mask tokens must be present (mutation 1 makes this RED)"
    snap = _json_block(text)
    cbk = snap["redaction"]["counts_by_kind"]
    for kind in ("openai_sk", "email", "phone_cn", "password"):
        assert cbk.get(kind, 0) >= 1, f"expected masked kind {kind}: {cbk}"


def test_stage1_bypass_is_rejected(drop_env, monkeypatch):
    # direct simulation of mutation 1: stage-1 deep pass => identity.
    # Stage-2 self-check MUST reject and leave a loud marker (V2.1).
    import core.archive_export as ae
    monkeypatch.setattr(ae, "redact_strings_deep", lambda obj: (obj, {}))
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    path = _export(drop_env, bus=bus)
    assert path is None, "export must be rejected when masking is bypassed"
    assert not list(drop_env.glob("*.md")), "no file may land"
    marker = run_dir(RUN_ID) / "archive_export_error.json"
    assert marker.exists(), "loud-failure marker missing"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["suspected_kinds"], "suspected kinds must be recorded"
    assert "email" in payload["suspected_kinds"]
    assert any(e.type == "archive_export_failed" for e in seen)
    shutil.rmtree(run_dir(RUN_ID), ignore_errors=True)


def test_a3_no_raw_task_text(drop_env):
    long_task = "F" * 200 + "XYZZY-secret-marker"
    _export(drop_env, task=long_task)
    _, text = _read_md(drop_env)
    assert "XYZZY" not in text, "raw task text must never reach the snapshot"
    snap = _json_block(text)
    assert len(snap["task_summary"]) <= 80
    assert len(snap["task_sha16"]) == 16
    shutil.rmtree(run_dir(RUN_ID), ignore_errors=True)


def test_switch_off_zero_effects(drop_env, monkeypatch):
    monkeypatch.setenv("ARCHIVE_EXPORT_ENABLED", "0")
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    path = _export(drop_env, bus=bus)
    assert path is None
    assert not drop_env.exists(), "switch off must not create the drop dir"
    marker = run_dir(RUN_ID) / "archive_export_error.json"
    assert not marker.exists()
    assert not [e for e in seen if e.type.startswith("archive_export")]


def test_a7_idempotent_reexport_and_conflict(drop_env):
    p1 = _export(drop_env)
    p2 = _export(drop_env)
    files = sorted(drop_env.glob("*.md"))
    assert len(files) == 1, "same-content re-export must not add a file"
    assert p1 == p2 == files[0]
    before = files[0].read_bytes()
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    p3 = _export(drop_env, bus=bus,
                 events=_rich_events()
                 + [_ev(99, "review", {"verdict": "REWORK"})])
    assert p3 is None, "different content under the same name must conflict"
    assert files[0].read_bytes() == before, \
        "original file must be untouched on conflict"
    assert any(e.type == "archive_export_failed" for e in seen)
    shutil.rmtree(run_dir(RUN_ID), ignore_errors=True)


def test_error_snapshot_carries_exc(drop_env):
    _export(drop_env, status="error", final=None, health=None,
            exc=RuntimeError("boom-injected-failure"),
            events=_rich_events()[:3])
    _, text = _read_md(drop_env)
    snap = _json_block(text)
    assert snap["status"] == "error"
    assert snap["error"]["type"] == "RuntimeError"
    assert "boom-injected-failure" in snap["error"]["detail"]
    assert snap["health"] is None
    dig = snap["trace_digest"]
    assert dig["kept_by_reason"].get("synth") == 1
    assert any("synth run_error" in i for i in dig["items"])
    shutil.rmtree(run_dir(RUN_ID), ignore_errors=True)


def test_digest_head_tail_and_keep_types(drop_env):
    evs = []
    for i in range(1, 101):
        t = "plan" if i == 30 else "node_enter"
        evs.append(_ev(i, t, {"i": i}))
    _export(drop_env, events=evs, final=None, health=None)
    _, text = _read_md(drop_env)
    snap = _json_block(text)
    dig = snap["trace_digest"]
    assert dig["total_events"] == 100
    assert dig["kept"] == 81, \
        "head 20 + tail 60 + plan@30 kept by type => 81"
    assert dig["kept_by_reason"] == {"head": 20, "tail": 60, "by_type": 1}
    assert all(len(i) <= 200 for i in dig["items"])
    assert any('"i": 30' in i and "plan" in i for i in dig["items"])
    shutil.rmtree(run_dir(RUN_ID), ignore_errors=True)




def test_a5_loud_fail_when_write_breaks(drop_env, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    monkeypatch.setenv("ARCHIVE_EXPORT_DIR", str(blocker))
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    path = _export(drop_env, bus=bus)
    assert path is None
    marker = run_dir(RUN_ID) / "archive_export_error.json"
    assert marker.exists(), "write failure must leave a loud marker"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["stage"] and payload["error"]
    assert any(e.type == "archive_export_failed" for e in seen)
    shutil.rmtree(run_dir(RUN_ID), ignore_errors=True)
