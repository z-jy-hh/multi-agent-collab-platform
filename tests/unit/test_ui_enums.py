"""UI enum constants must match the design document (single source of truth).

Implements the rule stated in docs/ui-enums.md §6: the doc wins, and code that
drifts from it fails here instead of silently diverging.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "api" / "static"
DOC = Path(__file__).resolve().parents[2] / "docs" / "ui-enums.md"

pytestmark = pytest.mark.skipif(
    not (STATIC / "ui" / "enums.js").exists() or not DOC.exists(),
    reason="enum assets or the design doc are absent in this scope")


def _enum_values(name: str) -> set[str]:
    js = (STATIC / "ui" / "enums.js").read_text(encoding="utf-8")
    m = re.search(r"const %s = Object\.freeze\(\{(.*?)\}\);" % name, js, re.S)
    assert m, f"{name} must be declared in enums.js"
    return set(re.findall(r'"([a-z-]+)"', m.group(1)))


def test_every_enum_value_is_documented():
    doc = DOC.read_text(encoding="utf-8")
    for name in ("STATE", "PERM", "BACKEND", "EXECUTOR"):
        for value in _enum_values(name):
            assert f"`{value}`" in doc, f"{name}.{value} is not documented in ui-enums.md"


def test_enums_are_loaded_before_the_inline_script():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    at = html.find("/static/ui/enums.js")
    assert at != -1, "enums.js must be linked"
    assert at < html.find("<script>"), "enums.js must load BEFORE the inline script"


def test_enums_are_not_redeclared_inline():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    inline = html[html.find("<script>"):]
    for name in ("STATE", "PERM", "BACKEND", "EXECUTOR"):
        assert not re.search(r"const\s+%s\s*=" % name, inline), \
            f"{name} must live only in ui/enums.js"
