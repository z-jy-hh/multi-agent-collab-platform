"""Execute the pure UI renderers under node (no browser needed).

Rationale: the S1 modules are pure string builders, so their behaviour CAN be
tested without a DOM. This closes the "no browser test" gap for the pieces the
review flagged (permission options must come from the server, window chrome
handles, version rows).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "api" / "static"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None or not (STATIC / "ui" / "util.js").exists(),
    reason="node or the ui assets are unavailable in this scope")


def _run(expr: str):
    modules = ["util.js", "window.js", "card-permission.js", "dock.js"]
    src = "\n".join((STATIC / "ui" / m).read_text(encoding="utf-8") for m in modules)
    src += f"\nconsole.log(JSON.stringify(({expr})));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(src)
        path = fh.name
    proc = subprocess.run([NODE, path], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_permission_card_buttons_follow_server_options():
    html = _run("permCardHTML({tool_call_id:'tc_1',options:['allow-once','reject-once']},'run-1')")
    assert 'data-perm-opt="allow-once"' in html
    assert 'data-perm-opt="reject-once"' in html
    assert 'data-perm-run="run-1"' in html
    assert "allow-all" not in html and "remember" not in html


def test_permission_card_fails_closed_without_options():
    html = _run("permCardHTML({},'run-1')")
    assert "无可用选项" in html
    assert "data-perm-opt=" not in html


def test_permission_card_escapes_the_run_id():
    html = _run("permCardHTML({options:['allow-once']},'r\\\" onmouseover=\\\"x')")
    assert 'onmouseover="x"' not in html, "run id must be escaped into the attribute"


def test_window_shell_has_exactly_eight_handles():
    html = _run("winShellHTML('dsh','#a1b2')")
    assert html.count("data-dir=") == 8
    assert "dsh" in html and "#a1b2" in html


def test_version_row_keeps_the_diff_contract():
    html = _run("versionRowHTML({version:3,decision:'KEEP',score_delta:0.125,reason:'ok',diff:'---'})")
    assert "v003" in html
    assert 'data-diff-key="3"' in html
    assert "0.1250" in html
