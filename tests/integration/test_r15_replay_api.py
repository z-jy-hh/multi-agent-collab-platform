"""R15-M4 (owner-directed UI redesign): read-only per-run replay endpoints.

The redesigned console rebuilds a FINISHED run's agent tree from persisted
evidence, so the API must expose it:
- GET /api/runs/{id}/trace    → parsed trace.jsonl (roles/cards/assignments);
- GET /api/runs/{id}/artifacts → the session's blackboard ops (the agents'
  published outputs, i.e. the "产出" the sidebar file viewer opens).

Both are READ-ONLY (contract, K5): calling them must leave the tree clean —
the only tolerated porcelain residue is untracked reports/layercheck/ logs.
Offline end to end: the run under test is driven with llm:"fake"."""

import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from api.routes import get_app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def server():
    config = uvicorn.Config(get_app(), host="127.0.0.1", port=8125,
                            log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = "http://127.0.0.1:8125"
    for _ in range(50):
        try:
            httpx.get(base + "/api/modes", timeout=1.0)
            break
        except Exception:
            time.sleep(0.2)
    yield base
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def finished_run_id(server):
    resp = httpx.post(server + "/api/runs", json={"mode": "supervisor",
                                                  "task": "M4 产物回放冒烟",
                                                  "llm": "fake"}, timeout=10)
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    # 全量套件同进程高负载时,一个 supervisor FakeLLM run 曾超过 30s —
    # 离线免费,放宽到 120s 只影响等待,不影响任何断言
    deadline = time.time() + 120
    while time.time() < deadline:
        state = httpx.get(f"{server}/api/runs/{run_id}/state", timeout=5)
        if state.status_code == 200:
            break
        time.sleep(0.3)
    assert state.status_code == 200, "run did not finish in time"
    return run_id


def test_trace_endpoint_replays_persisted_events(server, finished_run_id):
    resp = httpx.get(f"{server}/api/runs/{finished_run_id}/trace", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == finished_run_id
    assert data["total"] == len(data["events"]) and data["total"] > 0
    assert data["truncated"] is False
    types = {e["type"] for e in data["events"]}
    assert {"agent_card", "assignment", "agent_output", "run_finished"} <= types
    for e in data["events"]:
        assert set(e) >= {"seq", "run_id", "type", "payload"}


def test_trace_endpoint_404_on_unknown_run(server):
    resp = httpx.get(server + "/api/runs/no-such-run/trace", timeout=10)
    assert resp.status_code == 404


def test_artifacts_endpoint_returns_agent_outputs(server, finished_run_id):
    resp = httpx.get(f"{server}/api/runs/{finished_run_id}/artifacts",
                     timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == finished_run_id
    assert data["session_id"]
    arts = data["artifacts"]
    assert arts, "a finished supervisor run must have published blackboard ops"
    keys = {a["key"] for a in arts}
    assert "final_report" in keys
    for a in arts:
        assert set(a) >= {"op_id", "agent_id", "key", "value", "value_chars",
                          "truncated", "scope"}
        assert isinstance(a["value"], str)
        assert a["truncated"] == (a["value_chars"] > 100_000)


def test_artifacts_endpoint_404_on_unknown_run(server):
    resp = httpx.get(server + "/api/runs/no-such-run/artifacts", timeout=10)
    assert resp.status_code == 404


def test_replay_endpoints_leave_repo_unpolluted(server, finished_run_id):
    httpx.get(f"{server}/api/runs/{finished_run_id}/trace", timeout=10)
    httpx.get(f"{server}/api/runs/{finished_run_id}/artifacts", timeout=10)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                            capture_output=True, text=True).stdout
    dirty = [line for line in status.splitlines()
             if "reports/layercheck/" not in line
             and "reports\\layercheck\\" not in line]
    assert dirty == [], f"replay endpoints dirtied the tree: {dirty}"


def test_run_registry_records_topology_name_not_journal_mode(server,
                                                              finished_run_id):
    """R15-M5 regression: _record_run used to overwrite the `mode` parameter
    with the measured journal mode, so every API-created run carried
    mode='wal' in the runs table (M3's history display needs the real name)."""
    resp = httpx.get(server + "/api/runs", timeout=10)
    row = next(r for r in resp.json()["runs"] if r["run_id"] == finished_run_id)
    assert row["mode"] == "supervisor", (
        f"runs.mode must be the requested topology, got {row['mode']!r} — "
        "the journal-mode measurement leaked into the registry again")
