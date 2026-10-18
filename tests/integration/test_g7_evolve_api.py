"""Rectification C3 / A2: the evolution-history endpoint is read-only,
returns records in ascending version order, and every record carries either
a non-empty diff or an explicit diff_unavailable_reason (K4)."""

import threading
import time

import httpx
import pytest
import uvicorn

from api.routes import get_app


@pytest.fixture(scope="module")
def server():
    config = uvicorn.Config(get_app(), host="127.0.0.1", port=8145, log_level="critical")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = "http://127.0.0.1:8145"
    for _ in range(50):
        try:
            httpx.get(base + "/api/modes", timeout=1.0)
            break
        except Exception:
            time.sleep(0.2)
    yield base
    srv.should_exit = True
    thread.join(timeout=5)


def test_evolve_versions_endpoint(server):
    versions = httpx.get(server + "/api/evolve/versions", timeout=10).json()
    assert isinstance(versions, list) and len(versions) >= 5  # v001–v005

    nums = [v["version"] for v in versions]
    assert nums == sorted(nums), "must be ascending by version"
    # Round-8 D: this used to assert the EXACT list [1,2,3,4,5], which is not
    # an invariant — the evolver legitimately appends a record on every auto
    # run (round-8's real-LLM evolve added v006 and turned this red while the
    # endpoint was perfectly correct). The invariants are: the history starts
    # at v001, is CONTIGUOUS (records are append-only evidence — a gap means a
    # record was lost) and still contains the v001–v005 anchors asserted below.
    assert nums[0] == 1 and nums[:5] == [1, 2, 3, 4, 5]
    assert nums == list(range(1, len(nums) + 1)), f"history must be contiguous, got {nums}"

    for v in versions:
        assert "diff" in v, "every record must carry the diff key"
        assert v["diff"] or v.get("diff_unavailable_reason"), \
            f"v{v['version']}: empty diff without a reason violates K4"
    # v003 (KEEP, real anchor) must have a real unified diff
    v3 = next(v for v in versions if v["version"] == 3)
    assert v3["diff"] and "@@" in v3["diff"]
    assert v3["decision"] == "KEEP"

    # read-only (K5): the endpoint must not add versions
    again = httpx.get(server + "/api/evolve/versions", timeout=10).json()
    assert [v["version"] for v in again] == nums


# --- R9-2: version enumeration is the canonical-vNNN helper only -----------
def test_noncanonical_version_dir_does_not_change_nums(server):
    """R9-D2: a rogue v-prefixed directory (even one carrying a plausible
    record.json) must NOT leak into /api/evolve/versions. The rogue lives in
    the REAL prompts/.versions/ for the duration of the test only — untracked
    and removed in finally, so the porcelain is restored afterwards."""
    import json as _json
    import shutil
    import tempfile
    from pathlib import Path

    from core.paths import prompt_versions_dir

    base = prompt_versions_dir()
    rogue = base / "vX92_tmp"
    before = [v["version"] for v in
              httpx.get(server + "/api/evolve/versions", timeout=10).json()]
    # staging MUST be on the SAME drive as the repo (os.rename cannot cross
    # drives) and OUTSIDE the repo (so the move restores the porcelain even
    # if the later delete fails). The drive root of the repo is both.
    staging = Path(tempfile.mkdtemp(prefix="g7_rogue_", dir=base.anchor))
    try:
        if rogue.exists():  # leftover from an aborted earlier run
            rogue.rename(staging / "vX92_tmp_stale")
        rogue.mkdir()
        (rogue / "record.json").write_text(
            _json.dumps({"version": 92, "reason": "inject"}), encoding="utf-8")
        after = [v["version"] for v in
                 httpx.get(server + "/api/evolve/versions", timeout=10).json()]
    finally:
        # 1) move the rogue OUT of the repo first: a rename restores the
        #    porcelain even if the later delete fails (the host safe-delete
        #    shim wraps remove/rmdir/rmtree with a recycle-bin call that is
        #    intermittently fail-closed here). 2) best-effort delete outside.
        if rogue.exists():
            rogue.rename(staging / "vX92_tmp")
        try:
            shutil.rmtree(staging)
        except OSError:
            pass  # staging is outside the repo; porcelain already restored
    assert not rogue.exists(), "rogue dir must leave the repo (porcelain restored)"
    assert after == before, f"rogue dir leaked into nums: {before} -> {after}"
    assert 92 not in after
