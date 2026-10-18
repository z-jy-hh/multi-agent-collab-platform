"""R15 M1: GET /api/doctor — the web console's system doctor endpoint.

Machine-checks what the R15 task book demands of the endpoint:
- it exists, returns 200 and carries {score, summary, checks[]} with the
  doctor's check names (8 without an API key, 9 with — connectivity is the
  key-conditional one; llm itself is unconditional);
- every check status is PASS/FAIL/WARN, every detail a non-empty string, and
  the score equals the measured pass ratio (K2: computed, not asserted);
- calling it leaves the repository UNPOLLUTED (read-only contract, K5): the
  only tolerated residue in `git status` is pre-existing untracked
  reports/layercheck/ logs — the same criterion as J2(b).
HTTP from the test side uses httpx — the Python stack (R26).
Runs offline: no key is required; with no key the doctor takes its
no-connectivity path (8 checks) and stays green."""

import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from api.routes import get_app

ROOT = Path(__file__).resolve().parents[2]

DOCTOR_CORE_NAMES = {"python", "dependencies", "configs", "storage",
                     "offline_smoke", "llm", "crewai_storage",
                     "crewai_mkdir_trace"}


@pytest.fixture(scope="module")
def server():
    config = uvicorn.Config(get_app(), host="127.0.0.1", port=8124,
                            log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = "http://127.0.0.1:8124"
    for _ in range(50):
        try:
            httpx.get(base + "/api/modes", timeout=1.0)
            break
        except Exception:
            time.sleep(0.2)
    yield base
    srv.should_exit = True
    thread.join(timeout=5)


def test_doctor_endpoint_returns_checks_and_score(server):
    resp = httpx.get(server + "/api/doctor", timeout=120)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["score"], float) and 0.0 <= data["score"] <= 100.0
    assert isinstance(data["summary"], str) and data["summary"]
    names = [c["name"] for c in data["checks"]]
    # llm is unconditional; connectivity is appended only when a key exists
    assert DOCTOR_CORE_NAMES <= set(names)
    assert len(names) in (8, 9)
    assert {c["status"] for c in data["checks"]} <= {"PASS", "FAIL", "WARN"}
    passed = sum(1 for c in data["checks"] if c["status"] == "PASS")
    assert data["score"] == round(100.0 * passed / len(data["checks"]), 1)
    for c in data["checks"]:
        assert isinstance(c["details"], str) and c["details"]


def test_doctor_endpoint_leaves_repo_unpolluted(server):
    httpx.get(server + "/api/doctor", timeout=120)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                            capture_output=True, text=True).stdout
    dirty = [line for line in status.splitlines()
             if "reports/layercheck/" not in line
             and "reports\\layercheck\\" not in line]
    assert dirty == [], f"doctor endpoint dirtied the tree: {dirty}"
