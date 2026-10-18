"""R17 P3 trace footprint hygiene criteria (workorder sha256_16 f5e4642b4d5023ba).

T2  the trace writer run_topology creates must be CLOSED after the call
    returns (_fh.closed is True), on BOTH the normal and the exception path.
T4  no process handle growth across N=200 runs. Probe: Win32
    GetProcessHandleCount via ctypes (psutil is NOT installed in the repo
    venv; the workorder T4 allows either probe as long as the口径 is
    declared -- declared here).
T5  tail event must survive: the LAST trace line is run_finished. The
    clone-side dual mutation (drop the finally-close AND drop the per-event
    flush) must turn this red -- with flush intact, dropping close alone
    does NOT lose data (already in the OS file), so single mutation is
    explicitly NOT a discriminator (P2FIX2 lesson).
B-2 late emit on a caller-supplied bus after run_topology returns must be
    inert: the writer is unsubscribed FIRST, then closed, so a late emit can
    never reach a closed file (no ValueError, no trace corruption).

print/assert messages ASCII-only (GBK console rule).

Lives in tests/integration (NOT tests/unit): the pre-existing layer rule
bans `graphs` imports in tests/unit -- layercheck's core scope does not
copy graphs/, so a unit-level import breaks J2 collection (gate regression
caught 2026-10-12; same rule as test_archive_export.py docstring).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import gc
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _runs_cleanup import cleanup_run  # noqa: E402  (P3/B-1 shared helper)

from core.events import EventBus
from core.llm import FakeLLM
from core.paths import run_dir
from core.runtime import ScriptedRuntime
from graphs import runner as runner_mod
from graphs.runner import DEFAULT_SCRIPTS, default_llm_script, run_topology


TASK = "多Agent协同调研任务"
RUNTIME = ScriptedRuntime({**DEFAULT_SCRIPTS, "synth": ["综合:确定性输出。"]})


def _script(explode_at: str | None = None):
    def script(prompt, json_mode=False):
        if explode_at and explode_at in prompt:
            raise RuntimeError("synthetic boom (P3 T2 exception path)")
        if "拆解" in prompt:
            return json.dumps(
                {"subtasks": [{"subtask_id": "s1",
                               "required_capabilities": ["research"]}]},
                ensure_ascii=False)
        if "自荐 agent" in prompt:
            return json.dumps({"nominations": []}, ensure_ascii=False)
        if "审稿人" in prompt:
            return json.dumps({"verdict": "PASS", "comments": "ok"},
                              ensure_ascii=False)
        return default_llm_script(prompt, json_mode)
    return script


def _capture_writers(monkeypatch):
    """Route runner's EventJSONLWriter through a spy that records instances."""
    created = []
    real_cls = runner_mod.EventJSONLWriter

    class SpyWriter(real_cls):
        def __init__(self, path):
            super().__init__(path)
            created.append(self)

    monkeypatch.setattr(runner_mod, "EventJSONLWriter", SpyWriter)
    return created


def _run(rid: str, bus: EventBus | None = None, explode_at: str | None = None):
    return run_topology(
        "supervisor", TASK, run_id=rid, bus=bus or EventBus(),
        llm=FakeLLM(script=_script(explode_at=explode_at)),
        runtime=RUNTIME, params={})


# --- T2: writer lifecycle ------------------------------------------------------

def test_t2_writer_closed_after_normal_return(monkeypatch):
    created = _capture_writers(monkeypatch)
    rid = f"p3t2-{uuid.uuid4().hex[:8]}"
    try:
        final = _run(rid)
        assert final.get("status") == "done"
        assert created, "spy must capture the run's trace writer"
        for w in created:
            assert w._fh.closed is True, \
                "T2: trace writer must be closed after run_topology returns"
    finally:
        cleanup_run(rid)


def test_t2_writer_closed_on_exception_path(monkeypatch):
    created = _capture_writers(monkeypatch)
    rid = f"p3t2x-{uuid.uuid4().hex[:8]}"
    try:
        with pytest.raises(RuntimeError, match="synthetic boom"):
            _run(rid, explode_at="审稿人")
        assert created, "spy must capture the run's trace writer"
        for w in created:
            assert w._fh.closed is True, \
                "T2: trace writer must be closed on the exception path too"
    finally:
        cleanup_run(rid)


# --- T4: no handle growth ------------------------------------------------------
# Probe口径 (workorder T4 allows either probe; psutil is NOT in the repo venv):
# Win32 GetProcessHandleCount via ctypes, declarative argtypes/restype.
# GetCurrentProcess returns a PSEUDO handle (constant -1) which must NOT be
# passed to CloseHandle (that applies only to real handles, e.g. OpenProcess).
# Jitter control (R5 lesson: a criterion must not carry its own jitter):
# gc.collect() before every sample, MEDIAN of 5 samples per side, and a small
# absolute tolerance for interpreter-internal handle churn.

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
_k32.GetCurrentProcess.argtypes = []
_k32.GetProcessHandleCount.restype = ctypes.wintypes.BOOL
_k32.GetProcessHandleCount.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.POINTER(ctypes.wintypes.DWORD)]

_T4_TOL = 8


def _handle_count() -> int:
    h = _k32.GetCurrentProcess()
    if not h:
        raise AssertionError("GetCurrentProcess returned NULL")
    count = ctypes.wintypes.DWORD()
    if not _k32.GetProcessHandleCount(h, ctypes.byref(count)):
        raise AssertionError(
            f"GetProcessHandleCount failed: err={ctypes.get_last_error()}")
    return count.value


def _handle_count_median(samples: int = 5) -> int:
    vals = []
    for _ in range(samples):
        gc.collect()
        vals.append(_handle_count())
    return sorted(vals)[len(vals) // 2]


def _settle_runs(max_batches: int = 6) -> int:
    """Run in batches of 10 until the handle count stops moving.

    Empirical (t4_leak_locate.py, 30-run probe): the first ~10 runs do
    ONE-TIME lazy initialization (imports/caches, +~120 handles), then the
    count is FLAT (674 at run 10/20/30, Python-level open files constant
    at 3, threads constant at 1) — there is NO per-run leak. Warming up
    with a fixed small batch counted that one-time init into the measured
    window (R5 lesson: the criterion must not carry its own jitter).

    Runs WITHOUT the spy installed — see test_t4 for why writer retention
    would contaminate the measurement.
    """
    prev = _handle_count_median()
    for _ in range(max_batches):
        for _ in range(10):
            rid = f"p3t4s-{uuid.uuid4().hex[:6]}"
            _run(rid)
            cleanup_run(rid)
        cur = _handle_count_median()
        if abs(cur - prev) <= 2:
            return cur
        prev = cur
    return prev


def test_t4_no_handle_growth_n200(monkeypatch):
    # Harness-artifact note (t4_leak_locate + retained-close probe): on this
    # host a CLOSED TextIOWrapper still holds 1 OS handle until the wrapper
    # object itself is destroyed (100 open/close + retain => +100 handles;
    # del + gc => back to baseline). The production path never retains closed
    # writers (40-run no-spy probe: FLAT at 554), so T4 must assert-and-
    # RELEASE each run's writer instead of accumulating them in a list —
    # otherwise the harness, not the platform, manufactures the growth.
    _settle_runs()
    created = _capture_writers(monkeypatch)
    before = _handle_count_median()
    n_checked = 0
    for i in range(200):
        created.clear()  # release last run's closed wrappers before the next
        rid = f"p3t4-{i:03d}-{uuid.uuid4().hex[:6]}"
        _run(rid)
        cleanup_run(rid)
        assert created, f"run {i}: spy must capture the run's trace writer"
        assert all(w._fh.closed for w in created), \
            f"run {i}: T4: writer must end closed"
        n_checked += 1
    created.clear()
    after = _handle_count_median()
    assert n_checked == 200
    assert after <= before + _T4_TOL, \
        f"T4: handle growth beyond tolerance: median {before} -> " \
        f"{after} (tol {_T4_TOL})"


# --- T5: tail event survives ----------------------------------------------------

def test_t5_tail_event_survives(monkeypatch):
    created = _capture_writers(monkeypatch)
    rid = f"p3t5-{uuid.uuid4().hex[:8]}"
    body_error = None
    try:
        try:
            _run(rid)
            trace = Path(run_dir(rid)) / "trace.jsonl"
            lines = trace.read_text(encoding="utf-8").splitlines()
            assert lines, "trace must not be empty"
            last = json.loads(lines[-1])
            assert last.get("type") == "run_finished", \
                f"T5: tail event lost, last type = {last.get('type')!r}"
            for w in created:
                assert w._fh.closed is True
        except BaseException as exc:
            body_error = exc
            raise
    finally:
        try:
            cleanup_run(rid)
        except BaseException as cleanup_exc:
            # B-1 keeps cleanup loud, but a cleanup side-effect (e.g. the
            # dual-mutation clone leaves the handle open) must never MASK
            # the body verdict — the tail-loss message IS the discriminator.
            if body_error is None:
                raise
            print(f"t5: cleanup also failed (body verdict not masked): "
                  f"{cleanup_exc}", file=sys.stderr)


# --- B-2: late emit after return is inert ----------------------------------------

def test_b2_late_emit_after_return_is_inert(monkeypatch):
    created = _capture_writers(monkeypatch)
    rid = f"p3b2-{uuid.uuid4().hex[:8]}"
    bus = EventBus()
    try:
        _run(rid, bus=bus)
        trace = Path(run_dir(rid)) / "trace.jsonl"
        before = trace.read_bytes()
        assert created
        w = created[-1]
        assert w._fh.closed is True
        assert w not in bus._subs, \
            "B-2: writer still subscribed after return (late emit can hit a " \
            "closed file)"
        ev = bus.emit("p3_late_probe", rid, {"note": "emitted after return"})
        assert ev.type == "p3_late_probe"
        assert trace.read_bytes() == before, \
            "B-2: trace corrupted by the late emit"
    finally:
        cleanup_run(rid)
