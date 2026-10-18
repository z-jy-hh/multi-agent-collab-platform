"""W1a-2: the permission card must live in the ASKING agent's window.

The card itself was already pure (server options only); this guards WHERE it is
mounted, because a card in a side panel is a different product surface.
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
    NODE is None or not (STATIC / "ui" / "card-permission.js").exists(),
    reason="node or the ui assets are unavailable in this scope")


def _run(expr: str):
    src = (STATIC / "ui" / "util.js").read_text(encoding="utf-8") + "\n" + \
          (STATIC / "ui" / "card-permission.js").read_text(encoding="utf-8")
    src += f"\nconsole.log(JSON.stringify(({expr})));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(src)
        path = fh.name
    proc = subprocess.run([NODE, path], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_queue_label_singular_and_plural():
    assert _run("permQueueLabel(1)") == "待处理 1 项"
    assert _run("permQueueLabel(3)") == "待处理 3 项"
    assert _run("permQueueLabel(0)") == "无待处理"


def test_card_still_only_offers_server_options():
    html = _run("permCardHTML({tool_call_id:'t1',options:['allow-once']},'run-1')")
    assert "allow-once" in html
    assert "allow-all" not in html and "remember" not in html


def test_inline_mounts_into_the_agent_window():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    inline = html[html.find("<script>"):]
    assert "mountPerm(" in inline
    assert ".perm-host" in inline
    assert 'querySelector(".perm-host")' in inline, \
        "the host must be found inside a window body"
    # the old side-panel mount must be gone
    assert "dcPermCard" not in inline
    assert 'id="dc-perm-card"' not in html


def test_delegated_listener_is_document_level():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    inline = html[html.find("<script>"):]
    assert 'document.addEventListener("click"' in inline
    assert "[data-perm-opt]" in inline
