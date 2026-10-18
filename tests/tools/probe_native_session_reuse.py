"""W4-1 C2d: real-machine probe — two sequential turns on ONE dsh session.

This is the only criterion that proves the "same session is reusable" claim on
a real dsh (the offline suite only proves the code path through the fake ACP
server). It is deliberately a standalone tool, not part of pytest, because it
needs a real model turn and the operator's dsh login.

Usage (env must be peeled/pointed first; see the W4 report):
    .venv\\Scripts\\python.exe tests\\tools\\probe_native_session_reuse.py

Exit 0 = same session id for both turns; 1 = mismatch / failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core.events import EventBus                       # noqa: E402
from core.paths import storage_path                    # noqa: E402
from core.storage import SQLiteOpStore                 # noqa: E402

from agent_bridge.runtime import ACPRuntime            # noqa: E402


def main() -> int:
    run_id = "w4-probe-session-reuse"
    bus = EventBus()
    rt = ACPRuntime(
        run_id=run_id, platform_session_id="w4-probe-session",
        agents=frozenset({"researcher"}), fallback="off", bus=bus,
        store=SQLiteOpStore(storage_path()), max_turns=4, wall_s=900,
    )
    try:
        rt.preflight()
        first = rt.execute("researcher", "只回复：第一轮", {})
        second = rt.execute("researcher", "只回复：第二轮", {})
        sid1 = first.meta.get("backend_session_id")
        sid2 = second.meta.get("backend_session_id")
        # close BEFORE snapshotting the events, so the close evidence is counted
        rt.close(reason="probe finished")
        new_events = [e for e in bus.replay(0)
                      if e.type == "dsh_session" and e.payload.get("action") == "new"]
        close_events = [e for e in bus.replay(0)
                        if e.type == "dsh_session" and e.payload.get("action") == "close"]
        turns = [e for e in bus.replay(0) if e.type == "dsh_turn_event"]
        out = {
            "run_id": run_id,
            "session_id_1": sid1,
            "session_id_2": sid2,
            "same_session": bool(sid1) and sid1 == sid2,
            "new_events": len(new_events),
            "close_events": len(close_events),
            "turn_events": len(turns),
            "output_1_chars": len(first.output or ""),
            "output_2_chars": len(second.output or ""),
            "turn_seqs": sorted({e.payload.get("turn_seq") for e in turns}),
        }
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if out["same_session"] and out["new_events"] == 1 else 1
    finally:
        rt.close(reason="probe finished")


if __name__ == "__main__":
    raise SystemExit(main())
