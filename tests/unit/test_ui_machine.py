"""W1a: the seven-state machine is derived, not guessed (node execution)."""
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
    NODE is None or not (STATIC / "ui" / "machine.js").exists(),
    reason="node or the ui assets are unavailable in this scope")


def _run(expr: str):
    src = (STATIC / "ui" / "enums.js").read_text(encoding="utf-8") + "\n" + \
          (STATIC / "ui" / "machine.js").read_text(encoding="utf-8")
    src += f"\nconsole.log(JSON.stringify(({expr})));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(src)
        path = fh.name
    proc = subprocess.run([NODE, path], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_no_run_is_empty():
    assert _run("deriveState({hasRun:false, status:'running'})") == "empty"


def test_live_paused_done_failed():
    assert _run("deriveState({hasRun:true, status:'running'})") == "live"
    assert _run("deriveState({hasRun:true, status:'running', paused:true})") == "paused"
    assert _run("deriveState({hasRun:true, status:'done'})") == "done"
    assert _run("deriveState({hasRun:true, status:'cancelled'})") == "failed"
    assert _run("deriveState({hasRun:true, status:'failed'})") == "failed"


def test_replay_wins_over_terminal_states():
    assert _run("deriveState({hasRun:true, status:'done', replay:true})") == "replay"


def test_offline_is_reported():
    assert _run("deriveState({hasRun:true, status:'running', offline:true})") == "offline"


def test_unknown_status_falls_back_to_live_not_a_new_state():
    # A new backend status must never invent an eighth state.
    assert _run("deriveState({hasRun:true, status:'weird-new-status'})") == "live"


def test_labels_cover_every_state():
    for state in ("empty", "live", "paused", "done", "failed", "replay", "offline"):
        assert _run(f"stateLabel('{state}')") != state, f"{state} needs a label"


def test_machine_state_matches_the_enum_file():
    # The two sources must not drift: STATE values == STATE_LABEL keys.
    enums = _run("Object.values(STATE)")
    labels = _run("Object.keys(STATE_LABEL)")
    assert sorted(enums) == sorted(labels)


def test_every_label_is_exact():
    # "not equal to its id" is too weak: a typo'd label would still pass.
    expected = {
        "empty": "空", "live": "实时", "paused": "暂停", "done": "完成",
        "failed": "失败", "replay": "回放", "offline": "断线",
    }
    for state, label in expected.items():
        assert _run(f"stateLabel('{state}')") == label
