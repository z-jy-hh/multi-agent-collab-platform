"""W3-3b: feed REAL observations into the probe contracts.

Operational tool (not collected by pytest): opens a real dsh session, captures
the usage frame and tries session/list + session/resume, then writes
`reports/probe_evidence_<utc>.json`.

Run with DSH_BIN / DSH_EXPECTED_VERSION set (same as the smoke):
    .venv\\Scripts\\python.exe tests\\tools\\probe_capabilities.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from agent_bridge.backends.dsh import DshBackend  # noqa: E402
from agent_bridge.errors import SpiError  # noqa: E402
from agent_bridge.hetero_dispatch import _default_store  # noqa: E402
from agent_bridge.probes import evaluate_resume, evaluate_usage_shape  # noqa: E402
from agent_bridge.registry import load_registry  # noqa: E402
from agent_bridge.spi import BudgetSpec, RunAuthorization, TaskSpec  # noqa: E402
from agent_bridge.roots import dispatch_root  # noqa: E402
from tests.integration._runs_cleanup import cleanup_run  # noqa: E402


def main() -> int:
    data = load_registry()
    entry = data["backends"]["dsh"]
    rid = f"probe-{int(time.time())}"
    events: list[tuple[str, dict]] = []
    backend = DshBackend(entry, defaults=data["defaults"],
                         store=_default_store(),
                         emit=lambda t, p: events.append((t, p)))
    cwd = dispatch_root() / "dsh_dispatch" / rid
    spec = TaskSpec(run_id=rid, platform_session_id=f"probe-{rid}",
                    task_text="只回答一个数字：2+2=?", cwd=str(cwd))

    result = backend.execute_task(spec, budget=BudgetSpec(turns=1),
                                  authz=RunAuthorization())
    usage = result.usage or {}
    used = usage.get("context_used")
    shape_value, shape_evidence = evaluate_usage_shape(
        {"context_used": used} if used is not None else None)

    session_id = (result.audit_refs or {}).get("backend_session_id")
    resume_value, resume_evidence = False, "assumed-fail-closed"
    resume_error = None
    if session_id:
        try:
            backend.resume_session(rid, session_id,
                                   platform_session_id=f"probe-resume-{rid}")
            resume_value, resume_evidence = True, "measured@session/list+resume"
        except SpiError as exc:
            resume_error = str(exc)
            msg = str(exc).lower()
            if "not found under cwd" in msg:
                # The session exists only while the process is alive; a closed
                # session cannot prove or disprove resume support.
                resume_value, resume_evidence = None, "assumed-fail-closed"
            elif "session/list" in msg:
                resume_value, resume_evidence = evaluate_resume(
                    handshake_ok=True, list_ok=False, resume_ok=False)
            else:
                resume_value, resume_evidence = evaluate_resume(
                    handshake_ok=True, list_ok=True, resume_ok=False)

    report = {
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": rid,
        "session_id": session_id,
        "usage": {k: usage.get(k) for k in
                  ("context_used", "input_tokens", "output_tokens", "money")},
        "probes": {
            "usage_shape": {"value": shape_value, "evidence": shape_evidence},
            "supports_resume": {"value": resume_value, "evidence": resume_evidence},
            "paid": {"value": None, "evidence": "assumed-fail-closed"},
            "egress": {"value": None, "evidence": "assumed-fail-closed"},
        },
        "resume_error": resume_error,
        "events": [t for t, _ in events],
    }
    out = REPO / "reports" / f"probe_evidence_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    cleanup_run(rid)
    print(json.dumps({k: report[k] for k in ("session_id", "probes", "resume_error")},
                     ensure_ascii=False, indent=2))
    print("evidence:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
