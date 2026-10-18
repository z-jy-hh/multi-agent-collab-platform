"""W4-1 Q1 probe: end-to-end wall time, native role vs CrewAI inner role.

Same topology (single), same prompt, same model, N runs each, alternating order.
This is a *directional* measurement: n is small, the model is non-deterministic,
and the native side's model is backend-managed (not observable by the platform),
so token cost is NOT measured here.

Usage (repo root; real key in .env or env; DSH_BIN/DSH_EXPECTED_VERSION for native):
    .venv\\Scripts\\python.exe tests\\tools\\bench_native_vs_inner.py --runs 3
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core.events import EventBus                       # noqa: E402
from core.llm import FakeLLM, llm_from_env             # noqa: E402
from core.paths import storage_path                    # noqa: E402
from core.runtime import CrewAIRuntime                 # noqa: E402
from core.storage import SQLiteOpStore                 # noqa: E402

from agent_bridge.runtime import ACPRuntime            # noqa: E402
from crews.factory import make_crew                    # noqa: E402
from graphs.runner import run_topology                 # noqa: E402


def _one(mode: str, prompt: str, i: int, llm) -> dict:
    run_id = f"w4-bench-{mode}-{i}"
    bus = EventBus()
    runtime = None
    rt = None
    if mode == "native":
        os.environ["AGENT_REGISTRY"] = "1"
        rt = ACPRuntime(run_id=run_id, platform_session_id=f"s-{run_id}",
                        agents=frozenset({"researcher"}),
                        inner=CrewAIRuntime(make_crew), fallback="off", bus=bus,
                        store=SQLiteOpStore(storage_path()), max_turns=4,
                        wall_s=600)
        rt.preflight()
        runtime = rt
    started = time.perf_counter()
    try:
        final = run_topology("single", prompt, run_id=run_id,
                             session_id=f"s-{run_id}", llm=llm,
                             runtime=runtime, bus=bus, persistent=False)
        wall = time.perf_counter() - started
        report = final.get("final_report") or ""
        events = bus.replay(0)
        health = next((e.payload.get("score") for e in events
                       if e.type == "health"), None)
        native_session = any(e.type == "dsh_session"
                             and e.payload.get("action") == "new" for e in events)
        status = final.get("status")
    finally:
        if rt is not None:
            rt.close(reason="bench finished")
    return {"mode": mode, "i": i, "wall_s": round(wall, 2), "status": status,
            "chars": len(report), "answer": (report or "")[:80],
            "health": health, "native_session": native_session}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--prompt", default="只输出：W4-BENCH-OK")
    args = ap.parse_args()

    llm = llm_from_env()
    if isinstance(llm, FakeLLM):
        print("NO_KEY: llm_from_env() returned FakeLLM; set a real key", file=sys.stderr)
        return 2
    # the comparison must be the platform runtime, never a native wrapper
    os.environ.pop("NATIVE_RUNTIME", None)
    os.environ.pop("DISPATCH_HETERO", None)
    rows = []
    for i in range(1, args.runs + 1):
        for mode in ("native", "inner"):
            row = _one(mode, args.prompt, i, llm)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    print("--- summary ---")
    for mode in ("native", "inner"):
        walls = [r["wall_s"] for r in rows if r["mode"] == mode]
        chars = [r["chars"] for r in rows if r["mode"] == mode]
        print(json.dumps({
            "mode": mode, "n": len(walls),
            "wall_mean_s": round(statistics.mean(walls), 2),
            "wall_median_s": round(statistics.median(walls), 2),
            "wall_min_s": min(walls), "wall_max_s": max(walls),
            "chars_mean": round(statistics.mean(chars), 1),
            "ok": sum(1 for r in rows if r["mode"] == mode and r["status"] == "done"),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
