"""S1 asset-split guards: helpers live in ui/*.js, not duplicated inline.

Negative-first: a duplicate inline definition would silently shadow the module
and defeat the split, so it must fail here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "api" / "static"

pytestmark = pytest.mark.skipif(
    not (STATIC / "index.html").exists(),
    reason="ui assets are not part of this test scope (layercheck temp copy)")


def _html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def test_util_js_is_loaded_before_the_inline_script():
    html = _html()
    ext = html.find('/static/ui/util.js')
    inline = html.find("<script>")
    assert ext != -1, "util.js must be linked"
    assert inline != -1, "the inline app script must still exist"
    assert ext < inline, "util.js must load BEFORE the inline script (classic scripts run in order)"


def test_util_functions_are_not_redefined_inline():
    html = _html()
    inline_start = html.find("<script>")
    inline = html[inline_start:]
    for fn in ("esc", "fmtTime"):
        assert not re.search(r"function\s+%s\s*\(" % fn, inline), \
            f"{fn}() must not be defined inline anymore (S1 split)"


def test_util_js_exposes_the_pure_helpers():
    util = (STATIC / "ui" / "util.js").read_text(encoding="utf-8")
    assert "function esc(" in util
    assert "function escAttr(" in util, "attribute values need quote escaping"
    assert "function fmtTime(" in util
    # purity guard: helpers must not touch DOM/network/state
    body = util.split("*/", 1)[-1]
    for forbidden in ("document.", "fetch(", "localStorage", "state."):
        assert forbidden not in body, f"util.js must stay pure (found {forbidden})"


def test_api_js_is_loaded_before_the_inline_script():
    html = _html()
    ext = html.find('/static/ui/api.js')
    inline = html.find("<script>")
    assert ext != -1, "api.js must be linked"
    assert ext < inline, "api.js must load BEFORE the inline script"


def test_fetchjson_is_not_redefined_inline():
    html = _html()
    inline = html[html.find("<script>"):]
    assert not re.search(r"function\s+fetchJSON\s*\(", inline), \
        "fetchJSON() must not be defined inline anymore (S1 split)"


def test_api_js_has_no_dom_or_state_access():
    api = (STATIC / "ui" / "api.js").read_text(encoding="utf-8")
    body = api.split("*/", 1)[-1]
    for forbidden in ("document.", "localStorage", "state."):
        assert forbidden not in body, f"api.js must stay transport-only (found {forbidden})"


def test_state_js_is_loaded_before_the_inline_script():
    html = _html()
    ext = html.find('/static/ui/state.js')
    inline = html.find("<script>")
    assert ext != -1, "state.js must be linked"
    assert ext < inline, "state.js must load BEFORE the inline script"


def test_state_is_not_redeclared_inline():
    html = _html()
    inline = html[html.find("<script>"):]
    assert not re.search(r"^\s*(?:const|let|var)\s+state\s*=", inline, re.M), \
        "state must be declared once (ui/state.js), not inline"


def test_state_js_is_data_only():
    st = (STATIC / "ui" / "state.js").read_text(encoding="utf-8")
    body = st.split("*/", 1)[-1]
    for forbidden in ("document.", "fetch(", "localStorage"):
        assert forbidden not in body, f"state.js must stay data-only (found {forbidden})"


def test_window_js_is_loaded_and_pure():
    html = _html()
    assert html.find('/static/ui/window.js') != -1, "window.js must be linked"
    assert html.find('/static/ui/window.js') < html.find("<script>"), \
        "window.js must load BEFORE the inline script"
    win = (STATIC / "ui" / "window.js").read_text(encoding="utf-8")
    body = win.split("*/", 1)[-1]
    for forbidden in ("document.", "fetch(", "localStorage", "state."):
        assert forbidden not in body, f"window.js must stay pure (found {forbidden})"
    assert body.count('data-dir=') == 1, "resize handles must come from one template, not eight copies"
    # the inline code must actually USE the extracted builder (not keep its own copy)
    inline = _html()[_html().find("<script>"):]
    assert "winShellHTML(" in inline, "inline code must call winShellHTML()"
    assert "data-dir=" not in inline, "the old inline resize-handle template must be gone"


def test_permission_card_js_is_loaded_and_pure():
    html = _html()
    assert html.find('/static/ui/card-permission.js') != -1
    assert html.find('/static/ui/card-permission.js') < html.find("<script>")
    card = (STATIC / "ui" / "card-permission.js").read_text(encoding="utf-8")
    body = card.split("*/", 1)[-1]
    for forbidden in ("document.", "fetch(", "localStorage", "state."):
        assert forbidden not in body, f"card-permission.js must stay pure (found {forbidden})"


def test_inline_no_longer_hardcodes_permission_options():
    html = _html()
    inline = html[html.find("<script>"):]
    assert "permCardHTML(" in inline, "the card must be rendered by permCardHTML()"
    for invented in ("allow-once", "reject-once", "Allow</button>", "Reject</button>"):
        assert invented not in inline, \
            f"permission options must come from the server frame (found {invented!r})"


def test_permission_card_buttons_come_from_options():
    card = (STATIC / "ui" / "card-permission.js").read_text(encoding="utf-8")
    assert "data-perm-opt" in card, "buttons must carry the option id"
    assert "onclick=" not in card, "no inline handlers (use delegation)"
    assert "options.map(" in card, "one button per server-offered option"


def test_dock_js_is_loaded_and_pure():
    html = _html()
    assert html.find('/static/ui/dock.js') != -1, "dock.js must be linked"
    assert html.find('/static/ui/dock.js') < html.find("<script>")
    dock = (STATIC / "ui" / "dock.js").read_text(encoding="utf-8")
    body = dock.split("*/", 1)[-1]
    for forbidden in ("document.", "fetch(", "localStorage", "state."):
        assert forbidden not in body, f"dock.js must stay pure (found {forbidden})"


def test_version_rows_are_built_by_dock_js():
    html = _html()
    inline = html[html.find("<script>"):]
    assert "versionRowHTML" in inline, "the dock must render through versionRowHTML()"
    assert "evolution-item" not in inline, \
        "the version-row template must live in dock.js, not inline"


def test_io_js_is_loaded_and_pure():
    html = _html()
    assert html.find('/static/ui/io.js') != -1, "io.js must be linked"
    assert html.find('/static/ui/io.js') < html.find("<script>")
    io = (STATIC / "ui" / "io.js").read_text(encoding="utf-8")
    body = io.split("*/", 1)[-1]
    for forbidden in ("document.", "fetch(", "localStorage", "state."):
        assert forbidden not in body, f"io.js must stay pure (found {forbidden})"


def test_inline_renders_turns_through_io_js():
    inline = _html()
    inline = inline[inline.find("<script>"):]
    assert "ioBlockHTML(" in inline, "the turn block must be rendered by io.js"
    assert "class=\"io-block\"" not in inline, "the block template must not live inline"


def test_backends_js_is_loaded_and_pure():
    html = _html()
    assert html.find('/static/ui/backends.js') != -1, "backends.js must be linked"
    assert html.find('/static/ui/backends.js') < html.find("<script>")
    b = (STATIC / "ui" / "backends.js").read_text(encoding="utf-8")
    body = b.split("*/", 1)[-1]
    for forbidden in ("document.", "fetch(", "localStorage", "state."):
        assert forbidden not in body, f"backends.js must stay pure (found {forbidden})"


def test_add_agent_uses_the_order_backed_endpoint():
    html = _html()
    inline = html[html.find("<script>"):]
    assert "/api/backends" in inline, "the picker must read the registered endpoint"
    assert "backendRowHTML" in inline, "rows must be rendered by backends.js"
    assert 'id="add-agent-btn"' in html, "the entry point must exist"


def test_machine_js_is_loaded_and_pure():
    html = _html()
    assert html.find('/static/ui/machine.js') != -1, "machine.js must be linked"
    assert html.find('/static/ui/machine.js') < html.find("<script>")
    m = (STATIC / "ui" / "machine.js").read_text(encoding="utf-8")
    body = m.split("*/", 1)[-1]
    for forbidden in ("document.", "fetch(", "localStorage", "state."):
        assert forbidden not in body, f"machine.js must stay pure (found {forbidden})"


def test_ui_state_is_derived_not_assigned_by_hand():
    html = _html()
    inline = html[html.find("<script>"):]
    assert "applyUiState()" in inline, "the console must refresh the derived state"
    assert "deriveState(" in inline, "the state must come from machine.js"
    # no hand-written state strings in the handler surface
    for invented in ('uiState = "live"', 'uiState = "done"', 'uiState = "failed"'):
        assert invented not in inline, f"state must not be assigned by hand ({invented})"








