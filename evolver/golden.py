"""Golden-set runner: K runs per version over fixed tasks → aggregated
Metrics for the decide() comparison (C8: never 1-run-vs-1-run).

Necessary addition beyond the task book's file tree (logged in the G5 report
未决清单): the golden tasks live in tests/golden/tasks.json, the runner lives
here because it shares the evolver's Metrics type.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.llm import FakeLLM
from evolver.versions import Metrics
from graphs.runner import run_topology
from inspector.report import load_health


def load_tasks(path: Path | None = None) -> list[str]:
    p = Path(path) if path else Path(__file__).resolve().parents[1] / "tests" / "golden" / "tasks.json"
    return json.loads(p.read_text(encoding="utf-8"))["tasks"]


def run_golden(K: int = 3, tasks: list[str] | None = None,
               run_prefix: str = "golden") -> Metrics:
    """Run every task K times (offline FakeLLM by default) across the two
    representative modes (single + supervisor), aggregate:
    quality = mean health score, cost = mean output chars, latency = mean s."""
    tasks = tasks if tasks is not None else load_tasks()
    scores: list[float] = []
    costs: list[float] = []
    latencies: list[float] = []
    for i in range(K):
        for j, task in enumerate(tasks):
            for mode in ("single", "supervisor"):
                # R17-FLAKY-1: unique run_id per execution (fixed IDs caused
                # every full-suite run to write to the same run directories,
                # leading to rotation conflicts after dozens of runs)
                import uuid
                run_id = f"{run_prefix}-{uuid.uuid4().hex[:8]}-{i}-{j}-{mode}"
                t0 = time.perf_counter()
                # no explicit llm: the runner's offline default (business-aware
                # FakeLLM script) applies — a bare FakeLLM would plan nothing
                run_topology(mode, task, run_id=run_id,
                             session_id=f"{run_prefix}-sess")
                latency = time.perf_counter() - t0
                health = load_health(run_id) or {"score": 0.0}
                outputs = [e for e in _events(run_id) if e.type == "agent_output"]
                scores.append(float(health["score"]))
                costs.append(sum(int(e.payload.get("output_chars", 0)) for e in outputs))
                latencies.append(latency)
    return Metrics(
        quality=sum(scores) / len(scores),
        cost=sum(costs) / len(costs),
        latency_s=sum(latencies) / len(latencies),
    )


def _events(run_id: str) -> list:
    from core.paths import run_dir

    trace = Path(run_dir(run_id)) / "trace.jsonl"
    if not trace.exists():
        return []
    from core.schemas import Event

    return [Event(**json.loads(l)) for l in trace.read_text(encoding="utf-8").splitlines() if l]
