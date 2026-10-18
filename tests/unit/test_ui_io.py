"""W1a B2 renderer: the agent turn block must be honest and escaped.

Runs the pure renderer under node; no browser needed.
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
    NODE is None or not (STATIC / "ui" / "io.js").exists(),
    reason="node or the ui assets are unavailable in this scope")


def _run(expr: str):
    src = (STATIC / "ui" / "util.js").read_text(encoding="utf-8") + "\n" + \
          (STATIC / "ui" / "io.js").read_text(encoding="utf-8")
    src += f"\nconsole.log(JSON.stringify(({expr})));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(src)
        path = fh.name
    proc = subprocess.run([NODE, path], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_api_turn_says_prompt_is_not_carried():
    html = _run("ioBlockHTML({executor:'scripted',chars:42,response:'hello'})")
    assert "API" in html
    assert "未在下发事件中携带" in html
    assert "hello" in html


def test_native_turn_shows_the_session_id():
    html = _run("ioBlockHTML({executor:'crewai',sessionId:'dsh-sess-1',response:'ok'})")
    assert "原生" in html
    assert "dsh-sess-1" in html


def test_turn_block_escapes_untrusted_output():
    html = _run("ioBlockHTML({executor:'scripted',response:'<img src=x onerror=alert(1)>'})")
    assert "<img" not in html
    assert "&lt;img" in html


def test_missing_response_is_flagged_not_faked():
    html = _run("ioBlockHTML({executor:'scripted',chars:7})")
    assert "产出未取到" in html
    assert "7" in html
