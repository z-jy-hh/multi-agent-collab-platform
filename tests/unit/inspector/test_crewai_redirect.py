"""Round-5 S4: the CrewAI redirect self-check is MEASURED (path-string
comparison), with both directions — active redirect → PASS; ineffective
redirect → FAIL (not WARN). appdirs is monkeypatched, so no real
%LOCALAPPDATA% access happens in tests."""

import os
from pathlib import Path

import pytest

from core.paths import PROJECT_ROOT
from inspector.runner import measure_crewai_redirect


@pytest.fixture
def fake_appdirs(monkeypatch):
    import appdirs

    holder = {}
    monkeypatch.setattr(appdirs, "user_data_dir",
                        lambda *a, **k: holder.setdefault("fn", appdirs.user_data_dir)(  # keep orig
                            *a, **k) if False else holder["fn"](*a, **k))
    return holder


def test_measured_pass_when_redirect_inside_project(monkeypatch):
    import appdirs

    monkeypatch.setattr(
        appdirs, "user_data_dir",
        lambda *a, **k: str(PROJECT_ROOT / "runs" / "crewai_storage"))
    result = measure_crewai_redirect()
    assert result.status == "PASS"
    assert "inside_project=True" in result.details


def test_measured_fail_when_redirect_ineffective(monkeypatch):
    import appdirs

    outside = Path(os.environ["LOCALAPPDATA"]) / "CrewAI"
    monkeypatch.setattr(appdirs, "user_data_dir", lambda *a, **k: str(outside))
    result = measure_crewai_redirect()
    assert result.status == "FAIL"
    assert "inside_project=False" in result.details


def test_unverifiable_is_warn_not_fail(monkeypatch):
    import appdirs

    def _boom(*a, **k):
        raise RuntimeError("appdirs exploded")

    monkeypatch.setattr(appdirs, "user_data_dir", _boom)
    result = measure_crewai_redirect()
    assert result.status == "WARN"
    assert "UNVERIFIABLE" in result.details
