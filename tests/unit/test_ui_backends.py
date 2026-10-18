"""W1b picker: backend rows come from the order-backed endpoint, and a
disabled backend must be un-openable by construction (no data-open).
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
    NODE is None or not (STATIC / "ui" / "backends.js").exists(),
    reason="node or the ui assets are unavailable in this scope")


def _run(expr: str):
    src = (STATIC / "ui" / "util.js").read_text(encoding="utf-8") + "\n" + \
          (STATIC / "ui" / "backends.js").read_text(encoding="utf-8")
    src += f"\nconsole.log(JSON.stringify(({expr})));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(src)
        path = fh.name
    proc = subprocess.run([NODE, path], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_enabled_backend_is_openable():
    row = _run("backendRowHTML({backend_id:'dsh',enabled:true,"
               "requires_authorization:false,adapter:'acp',transport:'stdio',"
               "protocol_version:1})")
    assert 'data-open="dsh"' in row
    assert "可打开" in row


def test_unauthorized_backend_is_openable_but_marked():
    row = _run("backendRowHTML({backend_id:'paid',enabled:true,"
               "requires_authorization:true,adapter:'acp',transport:'stdio'})")
    assert 'data-open="paid"' in row, "needs consent, but may still be opened to ask"
    assert "需授权" in row


def test_disabled_backend_cannot_be_opened():
    row = _run("backendRowHTML({backend_id:'wb',enabled:false,"
               "requires_authorization:true,adapter:'acp',transport:'stdio'})")
    assert "data-open" not in row
    assert "未接通" in row


def test_backend_state_mapping_is_single_source():
    assert _run("backendState({enabled:false})") == "disabled"
    assert _run("backendState({enabled:true,requires_authorization:true})") == "unauthorized"
    assert _run("backendState({enabled:true})") == "available"


def test_backend_id_is_escaped_in_the_attribute():
    row = _run("backendRowHTML({backend_id:'x\\\" onmouseover=\\\"y',enabled:true})")
    assert 'onmouseover="y"' not in row
