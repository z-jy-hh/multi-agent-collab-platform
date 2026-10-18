"""F1/F2/F9 guards: the live UI must deliver hetero events, and a failed
backend result must never be rendered as a native success.

These are structural (the console is inline JS), but each assertion fails if the
specific wiring is removed — proven by the mutation audit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "api" / "static"

pytestmark = pytest.mark.skipif(
    not (STATIC / "index.html").exists(),
    reason="api/static is not copied by layercheck (core scope)")


def _inline() -> str:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return html[html.find("<script>"):]


def test_every_routed_case_is_registered_for_live_sse():
    inline = _inline()
    cases = set(re.findall(r'case "([a-z0-9_]+)"', inline))
    assert cases, "no routeEvent cases found — the parser drifted"
    block = inline[inline.find("const EVENT_TYPES"):]
    block = block[:block.find("];")]
    registered = set(re.findall(r'"([a-z0-9_]+)"', block))
    missing = sorted(cases - registered)
    assert not missing, (
        "named SSE events never reach these handlers (live mode is blind): "
        f"{missing}")


def test_failed_result_is_not_rendered_as_native():
    inline = _inline()
    result = inline[inline.find('case "hetero_dispatch_result"'):]
    result = result[:result.find('case "hetero_dispatch_error"')]
    assert "if (p.error)" in result, "a returned error must be handled"
    # the error branch must clear the session and mark unreachable
    assert "delete w.dataset.sessionId" in result
    assert 'w.dataset.unreachable = "1"' in result


def test_expired_marks_only_the_matching_card():
    inline = _inline()
    assert "expirePerm(" in inline
    assert "dataset.toolCall" in inline, "the card must carry its toolCallId"
    expired = inline[inline.find('case "spi_permission_expired"'):]
    expired = expired[:expired.find("break;")]
    assert "expirePerm(p.tool_call_id)" in expired
    assert "clearPerms()" not in expired, "clear-all would wipe another agent's card"
