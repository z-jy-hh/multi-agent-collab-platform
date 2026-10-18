#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Mutation probe for the real-browser interaction suite.

For each seeded defect:
  1. copy api/static into a temp dir (never mutate the repo);
  2. assert the injection anchor exists (ABORT loudly otherwise);
  3. run ONLY the case(s) that must catch it, against the mutated copy;
  4. require every expected case to be RED.
Finally run the pristine copy and require all cases GREEN.

Exit codes: 0 all expected RED + pristine GREEN; 1 mismatch; 2 environment/anchor.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "api" / "static"
RUNNER = REPO / "tests" / "integration" / "ui_browser" / "runner.cjs"
NODE = shutil.which("node")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MUTATIONS = [
    dict(id="M1", file="index.html", cases=["D1"],
         old="const x = ev.clientX - c.getBoundingClientRect().left + c.scrollLeft - dx;",
         new="const x = ev.clientX - el.getBoundingClientRect().left + c.scrollLeft - dx;"),
    dict(id="M2", file="index.html", cases=["D3"],
         old="const x = ev.clientX - c.getBoundingClientRect().left + c.scrollLeft - dx;",
         new="const x = ev.clientX - c.getBoundingClientRect().left - dx;"),
    dict(id="M3", file="index.html", cases=["D2"],
         old=('    const pos = clampPosition(x, y, b.maxX, b.maxY);\n'
              '    el.style.left = pos.x + "px";\n'
              '    el.style.top = pos.y + "px";'),
         new='    el.style.left = x + "px";\n    el.style.top = y + "px";'),
    dict(id="M4", file="index.html", cases=["D4"],
         old='                     el.removeEventListener("pointermove", move);\n',
         new=''),
    dict(id="M5", file="index.html", cases=["D5"],
         old="if (e.target.dataset.a) return;",
         new="if (false) return;"),
    dict(id="M6", file="index.html", cases=["R1:n", "R1:s", "R1:e", "R1:w",
                                            "R1:ne", "R1:nw", "R1:sw"],
         old="h.addEventListener(\"pointerdown\", e => startResize(e, el, h.dataset.dir)));",
         new="h.addEventListener(\"pointerdown\", e => startResize(e, el, \"se\")));"),
    dict(id="M7", file="ui/geometry.js", cases=["R1:w", "R1:nw", "R1:sw"],
         old="w = r0.x + w0 - x;",
         new="w = w0;"),
    dict(id="M8", file="ui/geometry.js", cases=["R2:w", "R2:n"],
         old=("  const mw = minW === undefined ? 220 : minW;\n"
              "  const mh = minH === undefined ? 120 : minH;"),
         new="  const mw = 0;\n  const mh = 0;"),
    dict(id="M9", file="index.html", cases=["F1"],
         old="function focusWin(el) {\n  el.style.zIndex = ++state.winZ;",
         new="function focusWin(el) {\n  el.style.zIndex = state.winZ;"),
    dict(id="M10", file="index.html", cases=["F1"],
         old='document.querySelectorAll(".win").forEach(w => w.classList.toggle("focused", w === el));',
         new='document.querySelectorAll(".win").forEach(w => w.classList.remove("focused"));'),
    dict(id="M11", file="index.html", cases=["P1"],
         old='const agentKey = p.agent_id || "__flow__";',
         new='const agentKey = "__flow__";'),
    dict(id="M12", file="index.html", cases=["P2"],
         old='fetchJSON(`/api/runs/${runId}/permission`, {',
         new='fetchJSON(`/api/runs/${runId}/permissions`, {'),
    dict(id="M13", file="index.html", cases=["P2"],
         old=('    body: JSON.stringify(toolCallId\n'
              '      ? {option_id: optionId, tool_call_id: toolCallId}\n'
              '      : {option_id: optionId})'),
         new='    body: JSON.stringify({})'),
    dict(id="M15", file="index.html", cases=["P4"],
         old='  hit.dataset.expired = "1";',
         new='  clearPerms();'),
    dict(id="M16", file="index.html", cases=["DC1"],
         old='onclick="dcConnect(dispatchRunId())"',
         new='onclick="dcConnect(document.getElementById(\'dc-run-id\').value || latestRunId)"'),
    dict(id="M17", file="index.html", cases=["P5", "P6"],
         old=('    case "spi_permission_request": {\n'
              '      // B6/W1a-2: the live run SSE delivers this NAMED event; without this\n'
              '      // branch the human never sees a card (the event fell through to default).\n'
              '      mountPerm(p, ev.run_id || state.selectedRunId || "");\n'
              '      break;\n'
              '    }'),
         new='    case "spi_permission_request": break;'),
    dict(id="M18", file="index.html", cases=["P6"],
         old='      clearPerm(p.tool_call_id || p.toolCallId || ev.tool_call_id);',
         new='      clearPerms();'),
    dict(id="M19", file="index.html", cases=["L1", "L2"],
         old=("    maxX: Math.max(0, c.scrollLeft + c.clientWidth - 80),\n"
              "    maxY: Math.max(0, c.scrollTop + c.clientHeight - 40),"),
         new=("    maxX: Math.max(0, c.scrollWidth - 80),\n"
              "    maxY: Math.max(0, c.scrollHeight - 40),")),
    dict(id="M20", file="index.html", cases=["P3"],
         old='      showPermError(toolCallId, `未接受（HTTP ${status || "?"}）：${e.message}`);',
         new='      clearPerm(toolCallId);'),
    dict(id="M21", file="index.html", cases=["P7"],
         old=('    if (status === 404 || status === 409) {\n'
              '      if (toolCallId) clearPerm(toolCallId); else clearPerms();'),
         new=('    if (status === 404 || status === 409) {\n'
              '      clearPerms();')),
    dict(id="M22", file="index.html", cases=["C1"],
         old='  el.addEventListener("pointercancel", up);\n',
         new=''),
    dict(id="M23", file="index.html", cases=["P8"],
         old='  if (!hit) { return; }',
         new='  if (!hit) { clearPerms(); return; }'),
    dict(id="M24", file="index.html", cases=["F2"],
         old=('function startResize(e, el, dir) {\n'
              '  e.preventDefault();\n'
              '  focusWin(el);\n'
              '  e.stopPropagation();'),
         new=('function startResize(e, el, dir) {\n'
              '  e.preventDefault();\n'
              '  e.stopPropagation();')),
    dict(id="M25", file="index.html", cases=["P2"],
         old=('    body: JSON.stringify(toolCallId\n'
              '      ? {option_id: optionId, tool_call_id: toolCallId}\n'
              '      : {option_id: optionId})'),
         new='    body: JSON.stringify({option_id: optionId})'),
    dict(id="M26", file="index.html", cases=["F2"],
         old=('  e.preventDefault();\n'
              '  focusWin(el);\n'
              '  e.stopPropagation();'),
         new=('  e.preventDefault();\n'
              '  focusWin(el);')),
    dict(id="M27", file="index.html", cases=["C2"], occurrence=1,
         old='  el.addEventListener("pointercancel", up);\n',
         new=''),
    dict(id="M28", file="index.html", cases=["C3"],
         old='    sb.addEventListener("pointercancel", up);\n',
         new=''),
]

QUICK = {"M3", "M9", "M12", "M15"}


def run(static_dir: Path, only: list[str]) -> tuple[int, dict | None, str]:
    fd, name = tempfile.mkstemp(prefix="ui_mut_", suffix=".json")
    os.close(fd)
    out = Path(name)
    try:
        proc = subprocess.run(
            [NODE, str(RUNNER), "--static", str(static_dir),
             "--only", ",".join(only), "--json", str(out)],
            cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300)
        data = None
        if out.exists() and out.stat().st_size:
            data = json.loads(out.read_text(encoding="utf-8"))
        tail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-1200:]
        return proc.returncode, data, tail
    finally:
        out.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="run 4 representative mutations")
    ap.add_argument("--only", default="", help="comma-separated mutation ids")
    args = ap.parse_args()

    if NODE is None:
        print("ABORT: node not found", file=sys.stderr)
        return 2
    if not (REPO / "node_modules" / "playwright-core" / "package.json").exists():
        print("ABORT: playwright-core missing; run `npm ci` in the repo root", file=sys.stderr)
        return 2

    chosen = [m for m in MUTATIONS if m["id"] in QUICK] if args.quick else list(MUTATIONS)
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        chosen = [m for m in chosen if m["id"] in wanted]

    bad = 0
    with tempfile.TemporaryDirectory(prefix="ui_mut_") as td:
        base = Path(td)
        for m in chosen:
            copy = base / m["id"]
            shutil.copytree(STATIC, copy)
            target = copy / m["file"]
            content = target.read_text(encoding="utf-8")
            start = -1
            for _ in range(m.get("occurrence", 0) + 1):
                start = content.find(m["old"], start + 1)
                if start < 0:
                    break
            if start < 0:
                print("ABORT %s: anchor missing in %s" % (m["id"], m["file"]), file=sys.stderr)
                return 2
            mutated = content[:start] + m["new"] + content[start + len(m["old"]):]
            target.write_text(mutated, encoding="utf-8")
            code, data, tail = run(copy, m["cases"])
            if data is None:
                print("FAIL %s: runner produced no JSON (exit %s)\n%s"
                      % (m["id"], code, tail))
                bad += 1
                continue
            cases = data.get("cases") or {}
            not_red = [c for c in m["cases"] if (cases.get(c) or {}).get("ok", True)]
            if not_red:
                print("FAIL %s: expected RED but still green: %s" % (m["id"], ", ".join(not_red)))
                bad += 1
            else:
                print("RED  %s: %d/%d expected case(s) failed"
                      % (m["id"], len(m["cases"]), len(m["cases"])))

        code, data, tail = run(STATIC, [])
        if data is None or code != 0:
            print("FAIL pristine: exit=%s\n%s" % (code, tail))
            bad += 1
        else:
            print("GREEN pristine: %d/%d PASS" % (data.get("pass"), data.get("of")))

    print("MUTATION PROBE: %s (%d mutation(s), %d bad)"
          % ("PASS" if bad == 0 else "FAIL", len(chosen), bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
