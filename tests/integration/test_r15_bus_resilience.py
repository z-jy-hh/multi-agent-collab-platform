"""R15-M6 regression: a closed event loop left attached to the shared bus
must not silently kill later runs.

Pre-fix defect (pre-existing, first exposed by the R15 API tests): module A
opens an SSE stream (attach_loop) and shuts its uvicorn server down; the bus
keeps the CLOSED loop; the next run's first emit from a worker thread hit
``loop.call_soon_threadsafe`` -> RuntimeError BEFORE any subscriber ran, so
the run left no trace, no state.json, no DB row — and every /state poll 404s
forever. Reproduced standalone and in the full suite (g6 + replay order).

This test replays exactly that sequence offline: SSE -> server shutdown ->
fresh server -> run -> state must reach 200."""

import threading
import time

import httpx
import uvicorn

from api.routes import get_app


def _start(port):
    config = uvicorn.Config(get_app(), host="127.0.0.1", port=port,
                            log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(base + "/api/modes", timeout=1.0)
            return srv, thread, base
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"server on {port} never came up")


def _stop(srv, thread):
    srv.should_exit = True
    thread.join(timeout=5)


def test_run_survives_closed_loop_left_by_previous_server():
    # phase 1: attach a loop via SSE, then close it with the server
    srv1, th1, base1 = _start(8126)
    try:
        resp = httpx.post(base1 + "/api/runs",
                          json={"mode": "supervisor", "task": "投毒步", "llm": "fake"},
                          timeout=10)
        run1 = resp.json()["run_id"]
        with httpx.stream("GET", f"{base1}/api/runs/{run1}/events",
                          timeout=30) as stream:
            for line in stream.iter_lines():
                if line.startswith("data:") and "run_finished" in line:
                    break
    finally:
        _stop(srv1, th1)

    # phase 2: a fresh server must still run tasks end-to-end
    srv2, th2, base2 = _start(8127)
    try:
        resp = httpx.post(base2 + "/api/runs",
                          json={"mode": "supervisor", "task": "M6 回归", "llm": "fake"},
                          timeout=10)
        run2 = resp.json()["run_id"]
        deadline = time.time() + 60
        status = None
        while time.time() < deadline:
            state = httpx.get(f"{base2}/api/runs/{run2}/state", timeout=5)
            status = state.status_code
            if status == 200:
                break
            time.sleep(0.5)
        assert status == 200, (
            "a closed loop left on the shared bus still kills later runs — "
            "the emit fallback in core/events.py regressed")
        assert httpx.get(f"{base2}/api/runs/{run2}/state").json()["final_report"]
    finally:
        _stop(srv2, th2)
