"""F2 (round-4): a trace is unique per run — an existing one is ROTATED
(byte-exact under ``.N``), never overwritten and never blocking."""

import json

from core.events import Event, EventJSONLWriter


def test_writer_records_events(tmp_path):
    path = tmp_path / "trace.jsonl"
    writer = EventJSONLWriter(path)
    writer(Event(run_id="r1", type="agent_output", payload={"n": 1}))
    writer.close()
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l]
    assert lines and lines[0]["type"] == "agent_output"


def test_existing_trace_rotates_not_overwrites(tmp_path):
    """Same run_id re-run: the old evidence survives as trace.jsonl.1 and the
    new run starts fresh — nothing is destroyed, nothing blocks."""
    path = tmp_path / "trace.jsonl"
    w1 = EventJSONLWriter(path)
    w1(Event(run_id="r1", type="run_finished", payload={"old": True}))
    w1.close()

    w2 = EventJSONLWriter(path)
    w2(Event(run_id="r1", type="agent_output", payload={"new": True}))
    w2.close()

    rotated = tmp_path / "trace.jsonl.1"
    assert rotated.exists(), "old evidence must be preserved under .1"
    old = json.loads(rotated.read_text(encoding="utf-8").splitlines()[0])
    assert old["payload"] == {"old": True}

    fresh = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l]
    assert len(fresh) == 1 and fresh[0]["payload"] == {"new": True}


def test_rotation_never_destroys_older_generations(tmp_path):
    path = tmp_path / "trace.jsonl"
    for i in range(3):
        w = EventJSONLWriter(path)
        w(Event(run_id="r", type="gen", payload={"gen": i}))
        w.close()
    assert (tmp_path / "trace.jsonl").exists()
    assert (tmp_path / "trace.jsonl.1").exists()
    assert (tmp_path / "trace.jsonl.2").exists()
    gens = [json.loads((tmp_path / f"trace.jsonl.{n}").read_text(encoding="utf-8"))["payload"]["gen"]
            for n in (1, 2)]
    assert gens == [0, 1]  # .N = 第 N 代历史: .1 = gen 0 (oldest), .2 = gen 1; live = gen 2
