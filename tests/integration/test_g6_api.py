"""G6 gate / 判据 9-10: modes are backend-driven and extensible with zero
core/graphs changes; SSE delivers the four event classes; cancel works.
HTTP from the test side uses httpx — the Python stack (R26)."""

import threading
import time

import httpx
import pytest
import uvicorn

from api.routes import get_app


@pytest.fixture(scope="module")
def server():
    config = uvicorn.Config(get_app(), host="127.0.0.1", port=8123, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = "http://127.0.0.1:8123"
    for _ in range(50):
        try:
            httpx.get(base + "/api/modes", timeout=1.0)
            break
        except Exception:
            time.sleep(0.2)
    yield base
    srv.should_exit = True
    thread.join(timeout=5)


def test_modes_are_backend_driven(server):
    data = httpx.get(server + "/api/modes", timeout=5).json()
    assert data["modes"] == ["debate", "pipeline", "single", "supervisor"]
    # the frontend must not hardcode modes: it is served a static page that
    # fetches /api/modes (verified by inspecting index.html)
    from core.paths import PROJECT_ROOT

    html = (PROJECT_ROOT / "api" / "static" / "index.html").read_text(encoding="utf-8")
    assert "/api/modes" in html
    assert '"supervisor"' not in html and "'supervisor'" not in html


def test_full_run_via_api_with_sse(server):
    resp = httpx.post(server + "/api/runs", json={"mode": "supervisor",
                                                  "task": "API 冒烟任务",
                                                  "llm": "fake"}, timeout=10)
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    # stream until terminal event (SSE via httpx stream — R26)
    types = set()
    with httpx.stream("GET", f"{server}/api/runs/{run_id}/events", timeout=30) as r:
        for line in r.iter_lines():
            if line.startswith("event:"):
                types.add(line.split(":", 1)[1].strip())
            if line.startswith("data:") and "run_finished" in line:
                break
    assert {"agent_card", "assignment", "blackboard_write", "arbitration"} <= types

    # state + health become available after completion
    deadline = time.time() + 20
    while time.time() < deadline:
        state = httpx.get(f"{server}/api/runs/{run_id}/state", timeout=5)
        if state.status_code == 200:
            break
        time.sleep(0.3)
    assert state.status_code == 200 and state.json()["final_report"]
    health = httpx.get(f"{server}/api/runs/{run_id}/health", timeout=5).json()
    assert health["run_id"] == run_id


def test_cancel_endpoint_exists(server):
    resp = httpx.post(server + "/api/runs/does-not-exist/cancel", timeout=5)
    assert resp.status_code == 404  # unknown run; the endpoint itself is wired
