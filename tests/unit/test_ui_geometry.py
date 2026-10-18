"""W1a-4: drag/resize rules are pure functions, tested without a browser."""
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
    NODE is None or not (STATIC / "ui" / "geometry.js").exists(),
    reason="node or the ui assets are unavailable in this scope")


def _run(expr: str):
    src = (STATIC / "ui" / "geometry.js").read_text(encoding="utf-8")
    src += f"\nconsole.log(JSON.stringify(({expr})));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(src)
        path = fh.name
    proc = subprocess.run([NODE, path], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_position_is_clamped_to_the_canvas():
    assert _run("clampPosition(-5, -5, 1000, 500)") == {"x": 0, "y": 0}
    assert _run("clampPosition(5000, 5000, 1000, 500)") == {"x": 1000, "y": 500}


def test_east_and_south_grow():
    box = _run("resizeBox({x:10,y:20,w:300,h:200}, 50, 30, 'se')")
    assert box == {"x": 10, "y": 20, "w": 350, "h": 230}


def test_minimum_size_is_enforced():
    box = _run("resizeBox({x:10,y:20,w:300,h:200}, -500, -500, 'se')")
    assert box["w"] == 220 and box["h"] == 120


def test_west_edge_stops_at_zero_and_width_follows():
    box = _run("resizeBox({x:50,y:10,w:300,h:100}, -200, 0, 'w')")
    assert box["x"] == 0
    assert box["x"] + box["w"] == 350, "the right edge must stay put"


def test_north_edge_stops_at_zero():
    box = _run("resizeBox({x:10,y:40,w:300,h:200}, 0, -300, 'n')")
    assert box["y"] == 0
    assert box["y"] + box["h"] == 240


def test_inline_uses_the_pure_geometry():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    inline = html[html.find("<script>"):]
    assert "clampPosition(" in inline
    assert "resizeBox(" in inline
    assert "Math.max(0, Math.min(c.scrollWidth - 80" not in inline


def test_explicit_min_size_parameters_are_honoured():
    box = _run("resizeBox({x:10,y:10,w:500,h:400}, -1000, 0, 'e', 50, 30)")
    assert box["w"] == 50, "the explicit minW must be used, not the default"
    box = _run("resizeBox({x:10,y:10,w:500,h:400}, 0, -1000, 's', 50, 30)")
    assert box["h"] == 30


def test_diagonal_resize_keeps_the_opposite_corner():
    box = _run("resizeBox({x:100,y:100,w:300,h:200}, -50, -50, 'nw')")
    assert box["x"] + box["w"] == 400, "right edge must stay put (nw)"
    assert box["y"] + box["h"] == 300, "bottom edge must stay put (nw)"
    assert box["x"] == 50 and box["y"] == 50
