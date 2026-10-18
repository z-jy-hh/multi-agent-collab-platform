"""W1a-3 (dsh only): the window badge is driven by the endpoint + real session.

Precedence under test:
  real session -> 原生 ; disabled -> 未接通 ; needs auth -> 需授权 ; else API.
wb is intentionally NOT exercised here (it is not being wired up).
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


def test_session_evidence_is_the_only_way_to_say_native():
    b = {"backend_id": "dsh", "enabled": True}
    assert _run(f"backendBadge({json.dumps(b)}, true)") == {"kind": "native", "label": "原生"}
    assert _run(f"backendBadge({json.dumps(b)}, false)") == {"kind": "api", "label": "API"}


def test_disabled_backend_is_unconnected():
    b = {"backend_id": "dsh", "enabled": False}
    assert _run(f"backendBadge({json.dumps(b)}, false)")["kind"] == "unconnected"


def test_unreachable_is_observed_not_declared():
    b = {"backend_id": "dsh", "enabled": True}
    assert _run(f"backendBadge({json.dumps(b)}, false, true)")["label"] == "不可达"
    # a real session still wins over a stale error flag
    assert _run(f"backendBadge({json.dumps(b)}, true, true)")["kind"] == "native"


def test_authorization_needed_is_reported_before_api():
    b = {"backend_id": "dsh", "enabled": True, "requires_authorization": True}
    assert _run(f"backendBadge({json.dumps(b)}, false)")["kind"] == "unauthorized"


def test_detail_comes_from_the_endpoint():
    b = {"adapter": "acp", "transport": "stdio", "protocol_version": 1}
    assert _run(f"backendDetail({json.dumps(b)})") == "acp · stdio · v1"
    assert _run('backendDetail({adapter:"acp"})') == "acp"


def test_inline_requires_the_shared_badge_function():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    inline = html[html.find("<script>"):]
    assert "applyBackendBadge(" in inline
    assert "backendBadge(" in inline
    assert "backendDetail(" in inline
    # "native" must not be asserted unconditionally anywhere
    assert 'setWinBadge(w, "native")' not in inline


def test_dispatch_result_records_the_session_id():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    inline = html[html.find("<script>"):]
    assert "dataset.sessionId = p.audit_refs.backend_session_id" in inline
    assert 'delete w.dataset.sessionId' in inline, \
        "an error must clear the session that proved native"
