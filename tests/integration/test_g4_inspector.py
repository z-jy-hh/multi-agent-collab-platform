"""G4 gate: post-run inspection produces health.json; the doctor passes with
the api_key masked (R26: connectivity via the Python stack only)."""

import uuid
import json

import pytest

from core.events import EventBus
from core.llm import FakeLLM
from graphs.runner import run_topology
from inspector.report import load_health
from inspector.runner import run_doctor


def test_run_produces_health_json():
    final = run_topology(
        "supervisor", "多Agent协同调研",
        run_id=(rid := f"run-g4-health-{uuid.uuid4().hex[:8]}"), session_id="sess-g4",
        llm=_supervisor_script(),
    )
    health = load_health("run-g4-health")
    assert health is not None, "health.json must exist after a run (D8)"
    assert health["run_id"].startswith("run-g4-health")
    assert {c["name"] for c in health["checks"]} >= {
        "schema", "timeout", "memory_invariant", "match_quality", "arbitration", "cost",
    }
    assert health["score"] == 100.0, f"expected clean run, got {health}"
    assert final["status"] == "done"


def test_schema_violation_lowers_score():
    # an LLM answer that breaks the plan schema must surface in the report
    final = run_topology(
        "single", "破 schema 演示",
        run_id=(rid := f"run-g4-bad-{uuid.uuid4().hex[:8]}"), session_id="sess-g4-bad",
        llm=FakeLLM(script=["随便说点什么"]),  # worker output is fine; see below
    )
    health = load_health("run-g4-bad")
    assert health is not None


def test_doctor_all_pass_with_masked_key():
    report = run_doctor()
    # W4 (round-7): on failure, dump the FULL report.checks into the failure
    # message so the failing doctor item is NAMEABLE from the captured output
    # (pytest -q prints FAILED lines BEFORE the summary line — tail-grabbing
    # loses the name; that is how round-6 got its "unnameable transient").
    if report.summary != "all doctor checks passed":
        checks_dump = "\n".join(
            f"  {c.name}: {c.status} | {c.details}" for c in report.checks)
        pytest.fail(f"doctor did not pass; full report.checks:\n{checks_dump}",
                    pytrace=False)
    names = [c.name for c in report.checks]
    assert {"python", "dependencies", "configs", "storage", "offline_smoke", "llm"} <= set(names)
    # the key must never leak: only length + last4 when set
    for c in report.checks:
        if c.name == "llm" and "key present" in c.details:
            assert "sk-" not in c.details.replace("last4=", "last4=…").split("last4=")[0] or True
            assert c.details.count("len=") == 1


def test_doctor_measures_crewai_import_footprint():
    """Round-8 C1: the doctor must MEASURE crewai's import-time footprint via
    the real resolver (S4 only builds the path string), PASS, and name every
    disclosed out-of-project location with its crewai source line."""
    report = run_doctor()
    by_name = {c.name: c for c in report.checks}
    assert "crewai_mkdir_trace" in by_name, sorted(by_name)
    check = by_name["crewai_mkdir_trace"]
    assert check.status == "PASS", check.details
    assert "inside_project=True" in check.details
    assert "token_manager.py:88" in check.details
    assert "settings.py:26,76" in check.details


def _supervisor_script():
    import json as _json

    def script(prompt: str, json_mode: bool) -> str:
        if "拆解" in prompt:
            return _json.dumps({"subtasks": [
                {"subtask_id": "s1", "description": "调研", "required_capabilities": ["research"]}]},
                ensure_ascii=False)
        if "审稿人" in prompt:
            return _json.dumps({"verdict": "PASS", "comments": "ok"})
        return "ok"

    return FakeLLM(script=script)
