"""W3-4: turn a probe evidence JSON into a PENDING proposal (never applies it).

    .venv\\Scripts\\python.exe tests\\tools\\propose_evidence.py reports\\probe_evidence_<ts>.json

Writes reports/probes/proposal_<ts>.json with status=pending-approval.
A human approves by filling approved_by + order_ref and recording a row in
docs/evidence_approvals.md; this tool never edits agents/registry.yaml.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from agent_bridge.evidence import propose  # noqa: E402
from agent_bridge.registry import load_registry  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: propose_evidence.py <probe_evidence.json>")
        return 2
    evidence = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    observed = {k: v for k, v in (evidence.get("probes") or {}).items()
                if isinstance(v, dict) and v.get("evidence")}
    registry = load_registry()
    backend = "dsh"
    caps = (registry["backends"][backend].get("capabilities") or {})
    rows = propose(caps, observed)
    proposal = {
        "kind": "capability-evidence-proposal",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "backend": backend,
        "source_evidence": str(Path(argv[1]).as_posix()),
        "status": "pending-approval",
        "rows": rows,
    }
    out_dir = REPO / "reports" / "probes"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"proposal_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    out.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": out.as_posix(),
                      "rows": [(r["capability"], r["action"]) for r in rows]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
