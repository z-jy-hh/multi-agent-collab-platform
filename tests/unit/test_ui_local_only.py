"""M10 guard: pin/archive must stay LOCAL-ONLY (no server round-trip).

The feature exists so a user can tidy the sidebar without mutating run state;
a future refactor that "helpfully" persists it server-side must fail here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[2] / "api" / "static" / "index.html"

# layercheck runs core tests from an isolated temp copy that does not include
# the web asset; skipping there is correct (nothing to assert about).
pytestmark = pytest.mark.skipif(
    not INDEX.exists(),
    reason="api/static/index.html is not part of this test scope (layercheck temp copy)")


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_pin_archive_are_stored_in_localstorage():
    html = _html()
    # reads go through the guarded helper (I1: private mode / corrupt JSON)
    assert 'readLocalSet("ui.pins")' in html
    assert 'readLocalSet("ui.archived")' in html
    assert 'localStorage.setItem("ui.pins"' in html
    assert 'localStorage.setItem("ui.archived"' in html
    # and the helper itself must never run a bare unguarded parse at top level
    assert "JSON.parse(localStorage.getItem" not in html, \
        "top-level localStorage parse can kill the whole app (I1)"


def test_pin_archive_never_write_to_the_server():
    html = _html()
    for fn in ("togglePin", "toggleArchive"):
        m = re.search(r"function %s\(\)\s*\{(.*?)\n\}" % fn, html, re.S)
        assert m, f"{fn} must exist"
        body = m.group(1)
        # a refresh via loadRuns() is a GET; what must never happen is a write
        for forbidden in ("fetch(", "fetchJSON(", "method:"):
            assert forbidden not in body, f"{fn} must not write to the server (M10)"
