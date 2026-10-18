"""P0 shell guards (post-audit): each assertion fails if the fix is removed.

These are structural because the console is inline JS; the mutation probe is the
companion that proves they bite.
"""
from __future__ import annotations

from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "api" / "static"

pytestmark = pytest.mark.skipif(
    not (STATIC / "index.html").exists(),
    reason="api/static is not copied by layercheck (core scope)")


def _inline() -> str:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return html[html.find("<script>"):]


def _css() -> str:
    return (STATIC / "ui" / "theme-p0.css").read_text(encoding="utf-8")


def _window_js() -> str:
    return (STATIC / "ui" / "window.js").read_text(encoding="utf-8")


def test_run_history_dock_does_not_invent_version_numbers():
    inline = _inline()
    start = inline.find("async function loadVersionDock")
    dock = inline[start:start + 2000]
    assert "运行历史" in (STATIC / "index.html").read_text(encoding="utf-8")
    assert "padStart(2, \"0\")" not in dock, \
        "the dock must not fabricate vNN version numbers from array index"
    assert "r.run_id" in dock and "status" in dock


def test_rearrange_uses_the_same_slot_geometry_as_ensure_window():
    inline = _inline()
    start = inline.find("function rearrange")
    body = inline[start:start + 600]
    assert "col * 360" in body and "row * 400" in body, \
        "rearrange must match the 340x380 window grid or it overlaps by construction"
    assert "col * 250" not in body, "the old 5-column geometry must be gone"


def test_window_slots_are_monotonic():
    inline = _inline()
    assert "state.winSeq" in inline, \
        "closing a window must not make the next one land on an existing slot"
    assert "const n = state.wins.size" not in inline


def test_canvas_top_follows_the_wrapping_toolbar():
    css = _css()
    inline = _inline()
    assert "var(--tools-h" in css, "canvas inset must not be a hardcoded 44px"
    assert "syncCanvasTop" in inline
    assert 'setProperty("--tools-h"' in inline


def test_hidden_trace_tab_gets_an_unread_marker():
    inline = _inline()
    win = _window_js()
    # Chat stays the default view (browser tests depend on .win-body), but a
    # hidden trace pane must signal activity instead of looking empty.
    assert 'class="win-tab on" data-tab="chat"' in win
    assert 'classList.add("unread")' in inline
    assert 'classList.remove("unread")' in inline


def test_status_bar_updates_on_every_event():
    inline = _inline()
    start = inline.find("function routeEvent")
    end = inline.find("// ---- 任务选择", start)
    body = inline[start:end]
    assert "updateStatusbar({" in body, "status bar must refresh from routeEvent"
    assert "backendSummary()" in body


def test_permission_chip_and_window_state_are_wired():
    inline = _inline()
    assert "setPermChip(true)" in inline
    assert "setPermChip(false)" in inline
    assert "setPermState(" in inline
    assert 'win.querySelector(".win-perm")' in inline or 'querySelector(".win-perm")' in inline


def test_backends_are_loaded_for_the_status_bar():
    inline = _inline()
    assert "ensureBackends().then(" in inline
