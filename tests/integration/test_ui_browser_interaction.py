"""Real-browser interaction tests for the console (playwright-core).

One node runner drives a real Chromium/Edge once and executes all scenarios;
each pytest case reads its own result key, so a failure stays attributable.

Environment contract (loud, never a silent skip):
  - node must exist;
  - `npm ci` must have installed playwright-core;
  - a system Edge/Chrome (or UI_BROWSER_PATH) must be launchable.
Missing environment => pytest.fail with the install instruction. This is deliberate:
a skip would hide 20 ledger-registered cases on a clean clone.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).resolve().parent / "ui_browser" / "runner.cjs"
NODE = shutil.which("node")
PLAYWRIGHT = REPO / "node_modules" / "playwright-core" / "package.json"


def _run_all() -> dict:
    if NODE is None:
        pytest.fail("node is required for the browser interaction suite")
    if not PLAYWRIGHT.exists():
        pytest.fail(
            "playwright-core is missing: run `npm ci` in the repo root "
            "(package-lock.json pins it)")
    with tempfile.TemporaryDirectory(prefix="ui_browser_") as td:
        out = Path(td) / "results.json"
        proc = subprocess.run(
            [NODE, str(RUNNER), "--json", str(out)],
            cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300)
        tail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-2000:]
        if proc.returncode == 2:
            pytest.fail("browser runner environment error:\n" + tail)
        if not out.exists() or out.stat().st_size == 0:
            pytest.fail(
                "browser runner produced no result (exit %d):\n%s"
                % (proc.returncode, tail))
        return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def results() -> dict:
    return _run_all()


def _assert_case(results: dict, key: str) -> None:
    case = (results.get("cases") or {}).get(key)
    assert case, "runner did not report case %r" % key
    if not case.get("ok"):
        pytest.fail("%s failed: %s" % (key, case.get("detail", "")))


def test_drag_uses_canvas_origin(results):
    _assert_case(results, "D1")


def test_drag_clamps_at_origin(results):
    _assert_case(results, "D2")


def test_drag_adds_canvas_scroll(results):
    _assert_case(results, "D3")


def test_drag_releases_on_pointerup(results):
    _assert_case(results, "D4")


def test_window_buttons_do_not_drag(results):
    _assert_case(results, "D5")


@pytest.mark.parametrize("direction", ["n", "s", "e", "w", "ne", "nw", "se", "sw"])
def test_resize_keeps_opposite_edge(results, direction):
    _assert_case(results, "R1:" + direction)


@pytest.mark.parametrize("axis", ["w", "n"])
def test_resize_min_on_w_n_axes(results, axis):
    _assert_case(results, "R2:" + axis)


def test_focus_raises_z_and_follows(results):
    _assert_case(results, "F1")


def test_resize_raises_the_window(results):
    _assert_case(results, "F2")


def test_card_mounts_in_chat_pane(results):
    _assert_case(results, "P1")


def test_click_posts_exactly_one_answer(results):
    _assert_case(results, "P2")


def test_rejection_keeps_the_card_and_shows_reason(results):
    _assert_case(results, "P3")


def test_stale_answer_clears_only_the_matching_card(results):
    _assert_case(results, "P7")


def test_unknown_expiry_keeps_live_cards(results):
    _assert_case(results, "P8")


def test_timeout_marks_only_the_matching_card(results):
    _assert_case(results, "P4")


def test_dispatch_connect_without_run_id_is_loud(results):
    _assert_case(results, "DC1")


def test_live_permission_request_mounts_a_card(results):
    _assert_case(results, "P5")


def test_live_permission_answered_clears_only_the_matching_card(results):
    _assert_case(results, "P6")


def test_drag_keeps_horizontal_80px_visible(results):
    _assert_case(results, "L1")


def test_drag_keeps_vertical_40px_visible(results):
    _assert_case(results, "L2")


def test_pointercancel_ends_the_drag(results):
    _assert_case(results, "C1")


def test_pointercancel_ends_the_resize(results):
    _assert_case(results, "C2")


def test_pointercancel_ends_the_splitter_drag(results):
    _assert_case(results, "C3")
