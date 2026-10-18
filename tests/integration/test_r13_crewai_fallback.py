"""R13-2: deterministic integration — the crewai config fallback is DISCLOSED
by exact equality, and the real user home / real %TEMP% gain NOTHING.

Turns the R12 intermittent false-red into a deterministic scenario WITHOUT
waiting for the host safe-delete shim to misfire: pointing ``USERPROFILE``
at an existing FILE makes crewai's ``get_writable_config_path``
(crewai_core/settings.py:76 chain) fall back to
``<temp>/crewai_settings.json``, whose parent mkdir is the process temp
root. The tracer records that attempt; ``measure_crewai_mkdir_trace`` must
disclose it by EXACT equality against the child's own
``tempfile.gettempdir()`` and emit a visible NOTE — while every other
out-of-project creation stays refused and the real locations stay untouched.

Technique note (R13-2.3): the TEMP redirect works because the tracer runs in
a FRESH child process that inherits the monkeypatched environment —
``tempfile.gettempdir()`` there has no cache yet and resolves TMPDIR/TEMP/TMP
to the test's own ``tmp_path``. The PARENT pytest process captured the real
temp/home BEFORE the env change (and its own gettempdir cache is already
pinned), so the zero-addition assertions below run against the real
locations.
"""

import os
import tempfile
from pathlib import Path

from inspector.runner import measure_crewai_mkdir_trace


def test_config_fallback_deterministically_disclosed(tmp_path, monkeypatch):
    # ---- snapshot the REAL locations BEFORE any env change ----------------
    real_home = Path.home().resolve()
    real_temp = Path(tempfile.gettempdir()).resolve()
    home_before = set(os.listdir(real_home))
    temp_before = set(os.listdir(real_temp))

    # ---- deterministic fallback setup --------------------------------------
    # an existing FILE as the home directory: mkdir under it must fail, so
    # the settings fallback chain advances to candidate 2 (<temp>/…)
    fake_home = tmp_path / "home_is_a_file.txt"
    fake_home.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    # redirect the CHILD's temp root to the test's own tmp (fresh child
    # process -> no tempfile cache; all three variables set so gettempdir()
    # deterministically resolves to tmp_path)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))

    # ---- the REAL check: real child process, no injected payloads ---------
    result = measure_crewai_mkdir_trace()

    assert result.status == "PASS", result.details
    assert "NOTE: crewai config fallback wrote through" in result.details, result.details
    assert "disclosed, not a leak" in result.details, result.details
    assert str(tmp_path) in result.details, result.details
    assert "settings.py:76" in result.details, result.details
    assert "inside_project=True" in result.details, result.details

    # ---- zero additions to the real home and the real %TEMP% --------------
    assert set(os.listdir(real_home)) == home_before, (
        "the real user home must gain nothing from this test")
    assert set(os.listdir(real_temp)) == temp_before, (
        "the real %TEMP% must gain nothing at TOP level from this test "
        "(the child's fallback lands in the test's own tmp_path)")
