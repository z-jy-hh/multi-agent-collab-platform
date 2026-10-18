"""Runner-hook integration tests for the H1 archive-export (V2 section 5).

These two cases exercise graphs/runner.py's export hooks end-to-end, so they
live in the integration layer: layercheck's core scope (tests/unit) only
allows ``core`` imports and bans ``graphs`` (that is the layer under test).

Discriminator: deleting the error-path hook in graphs/runner.py makes
test_runner_error_path_exports RED (no error snapshot at all).

print/assert messages ASCII-only (GBK console rule).
"""

import json
import shutil
import threading

import pytest

from core.events import EventBus
from core.paths import run_dir

RUN_ID_PREFIX = "archive-export-it"
SAMPLES = {
    "sk": "sk-abcdefghijklmnopqrstuvwxyz",
    "mail": "admin@corp.example.com",
    "phone": "13812345678",
    "pwd": "password=hunter2secret",
}
TASK = (f"key={SAMPLES['sk']} {SAMPLES['pwd']} mail={SAMPLES['mail']} "
        f"tel={SAMPLES['phone']}")


@pytest.fixture
def drop_env(tmp_path, monkeypatch):
    out = tmp_path / "outbox"
    monkeypatch.setenv("ARCHIVE_EXPORT_ENABLED", "1")
    monkeypatch.setenv("ARCHIVE_EXPORT_DIR", str(out))
    return out


def _json_block(text):
    return json.loads(text.split("```json\n", 1)[1].rsplit("```", 1)[0])


def test_runner_error_path_exports(drop_env):
    # mutation-2 discriminator: deleting the error-path hook in
    # graphs/runner.py makes this test RED (no error snapshot at all).
    from core.errors import CancelledRun
    from core.llm import FakeLLM
    from graphs.runner import default_llm_script, run_topology

    class BoomRuntime:
        def execute(self, agent_id, prompt, context):
            raise RuntimeError("boom-injected-failure")

    bus = EventBus()
    with pytest.raises(Exception) as ei:
        run_topology("supervisor", TASK, bus=bus, session_id="sess-err",
                     llm=FakeLLM(script=default_llm_script),
                     runtime=BoomRuntime())
    assert not isinstance(ei.value, CancelledRun), \
        "expected a genuine failure, not a cancellation"
    files = sorted(drop_env.glob("*.md"))
    assert len(files) == 1, \
        f"error run must export exactly 1 snapshot, got {len(files)}"
    snap = _json_block(files[0].read_text(encoding="utf-8"))
    assert snap["status"] == "error"
    assert snap["error"]["type"], "error.type must be recorded (V2.1)"
    assert snap["error"]["detail"], "error.detail must be recorded (V2.1)"
    shutil.rmtree(run_dir(snap["run_id"]), ignore_errors=True)


def test_runner_cancel_path_exports(drop_env):
    from core.llm import FakeLLM
    from graphs.runner import default_llm_script, run_topology

    bus = EventBus()
    cancel = threading.Event()
    bus.subscribe(lambda e: cancel.set() if e.type == "node_enter" else None)
    final = run_topology("supervisor", TASK, bus=bus,
                         session_id="sess-cancel",
                         llm=FakeLLM(script=default_llm_script),
                         cancel_event=cancel)
    assert final["status"] == "cancelled"
    files = sorted(drop_env.glob("*.md"))
    assert len(files) == 1, "cancelled run must export a snapshot"
    snap = _json_block(files[0].read_text(encoding="utf-8"))
    assert snap["status"] == "cancelled"
    shutil.rmtree(run_dir(snap["run_id"]), ignore_errors=True)
