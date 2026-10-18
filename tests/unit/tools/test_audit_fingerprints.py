"""R9-6: the J2(c) fingerprint criterion EXCLUDES crewai's transient
write-test file by NAME (with a visible NOTE), instead of failing with a
false ADDED when a snapshot lands inside crewai's import window.

Unit-level: _fingerprint_tree runs against a tmp_path tree — the real
out-of-project areas are never touched from here.
"""

import hashlib
import importlib.util
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[3] / "tests" / "tools" / "audit_invariants.py"


def _load_tool():
    # audit_invariants.py is a SCRIPT: it imports its sibling mutation_probe
    # via a plain "import", which only resolves when tests/tools is on
    # sys.path (as when run as a subprocess). Mirror that here.
    tool_dir = str(_TOOL.parent)
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)
    spec = importlib.util.spec_from_file_location("audit_invariants_r9", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_transient_write_test_excluded_with_note(tmp_path):
    mod = _load_tool()
    (tmp_path / "real_file.txt").write_bytes(b"payload\n")
    (tmp_path / ".crewai_write_test").write_bytes(b"probe")
    out: dict = {}
    notes: list = []
    mod._fingerprint_tree(tmp_path, "TMP/base", out, [], notes)
    # the transient file must be invisible to the criterion...
    assert not any(k.endswith(".crewai_write_test") for k in out)
    # ...and the sighting must be VISIBLE (never a silent exclusion, K3)
    assert any(".crewai_write_test" in n and "EXCLUDED" in n for n in notes)
    # the real file keeps its content fingerprint: '<size>:<sha256_16>'
    payload = b"payload\n"
    expect = f"{len(payload)}:{hashlib.sha256(payload).hexdigest()[:16]}"
    assert out["TMP/base/real_file.txt"] == expect


def test_transient_exclusion_works_without_notes_list(tmp_path):
    mod = _load_tool()
    (tmp_path / ".crewai_write_test").write_bytes(b"probe")
    out: dict = {}
    mod._fingerprint_tree(tmp_path, "TMP/base", out, [], None)  # notes=None path
    assert not any(k.endswith(".crewai_write_test") for k in out)


def test_non_transient_same_shape_file_still_fingerprinted(tmp_path):
    mod = _load_tool()
    # a file that is NOT the known transient must NOT be absorbed: the
    # exclusion is a NAMED set, not a pattern
    (tmp_path / ".crewai_write_test_backup").write_bytes(b"keep")
    out: dict = {}
    notes: list = []
    mod._fingerprint_tree(tmp_path, "TMP/base", out, [], notes)
    assert "TMP/base/.crewai_write_test_backup" in out
    assert notes == []


def test_transient_set_is_exact():
    mod = _load_tool()
    # guard against silent widening of the exclusion set
    assert mod.TRANSIENT_FILENAMES == {".crewai_write_test"}


# --- R10-3: a covered area vanishing WHOLESALE must WARN, never pass in
# --- silence (residual R5). Everything runs on a tmp tree with monkeypatched
# --- environment variables — the real user directories are never touched.

import json
from types import SimpleNamespace  # noqa: E402


def _make_check_env(tmp_path, monkeypatch, with_crewai_config: bool):
    """Build a fully faked check_clean environment: LOCALAPPDATA points at a
    tmp tree that HAS a CrewAI dir; USERPROFILE points at a tmp tree whose
    .config/crewai exists only when with_crewai_config is True. git + tool
    runs are no-oped."""
    mod = _load_tool()
    tmp_appdata = tmp_path / "appdata"
    tmp_home = tmp_path / "home"
    (tmp_appdata / "CrewAI" / "multi_agent_collab").mkdir(parents=True)
    if with_crewai_config:
        (tmp_home / ".config" / "crewai").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_appdata))
    monkeypatch.setenv("USERPROFILE", str(tmp_home))

    monkeypatch.setattr(mod, "_git_status", lambda root: [])
    monkeypatch.setattr(mod, "_run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="", stderr=""))

    root = tmp_path / "repo"
    (root / "runs").mkdir(parents=True)
    fps = {
        "LOCALAPPDATA/CrewAI": "dir",
        "LOCALAPPDATA/CrewAI/multi_agent_collab": "dir",
        "USERPROFILE/.config/crewai": "dir",
    }
    (root / "runs" / "crewai_names_state.json").write_bytes(json.dumps(
        {"names": ["multi_agent_collab"], "fingerprints": fps}).encode("utf-8"))
    return mod, root


def test_missing_covered_area_warns_but_still_passes(tmp_path, monkeypatch):
    mod, root = _make_check_env(tmp_path, monkeypatch, with_crewai_config=False)
    problems, notes = mod.check_clean(root, log_dir_tmp=None, run_tools=True)
    warns = [n for n in notes if "covered area missing" in n]
    assert len(warns) == 1, f"expected exactly one WARN, notes={notes}"
    assert ".config" in warns[0] and "crewai" in warns[0]
    assert "was in baseline" in warns[0]
    # VISIBLE (note channel) but NOT a failure — legitimate cleanups are
    # absorbed, by design; the criterion itself must not go red
    assert not [p for p in problems if "J2(c)" in p]


def test_present_covered_area_emits_no_warning(tmp_path, monkeypatch):
    mod, root = _make_check_env(tmp_path, monkeypatch, with_crewai_config=True)
    problems, notes = mod.check_clean(root, log_dir_tmp=None, run_tools=True)
    assert not [n for n in notes if "covered area missing" in n]
    assert not [p for p in problems if "J2(c)" in p]


def test_never_recorded_area_does_not_warn(tmp_path, monkeypatch):
    # a dir that was NEVER in the baseline (e.g. crewai never installed on a
    # fresh machine) must NOT trigger the "was in baseline" warning
    mod, root = _make_check_env(tmp_path, monkeypatch, with_crewai_config=False)
    state = root / "runs" / "crewai_names_state.json"
    payload = json.loads(state.read_bytes().decode("utf-8"))
    del payload["fingerprints"]["USERPROFILE/.config/crewai"]
    state.write_bytes(json.dumps(payload).encode("utf-8"))
    problems, notes = mod.check_clean(root, log_dir_tmp=None, run_tools=True)
    assert not [n for n in notes if "covered area missing" in n]


# --- R10-3: a covered area vanishing WHOLESALE must WARN, never pass in
# --- silence (residual R5). Everything runs on a tmp tree with monkeypatched
# --- environment variables — the real user directories are never touched.

import json
from types import SimpleNamespace  # noqa: E402


def _make_check_env(tmp_path, monkeypatch, with_crewai_config: bool):
    """Build a fully faked check_clean environment: LOCALAPPDATA points at a
    tmp tree that HAS a CrewAI dir; USERPROFILE points at a tmp tree whose
    .config/crewai exists only when with_crewai_config is True. git + tool
    runs are no-oped."""
    mod = _load_tool()
    tmp_appdata = tmp_path / "appdata"
    tmp_home = tmp_path / "home"
    (tmp_appdata / "CrewAI" / "multi_agent_collab").mkdir(parents=True)
    if with_crewai_config:
        (tmp_home / ".config" / "crewai").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_appdata))
    monkeypatch.setenv("USERPROFILE", str(tmp_home))

    monkeypatch.setattr(mod, "_git_status", lambda root: [])
    monkeypatch.setattr(mod, "_run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="", stderr=""))

    root = tmp_path / "repo"
    (root / "runs").mkdir(parents=True)
    fps = {
        "LOCALAPPDATA/CrewAI": "dir",
        "LOCALAPPDATA/CrewAI/multi_agent_collab": "dir",
        "USERPROFILE/.config/crewai": "dir",
    }
    (root / "runs" / "crewai_names_state.json").write_bytes(json.dumps(
        {"names": ["multi_agent_collab"], "fingerprints": fps}).encode("utf-8"))
    return mod, root


def test_missing_covered_area_warns_but_still_passes(tmp_path, monkeypatch):
    mod, root = _make_check_env(tmp_path, monkeypatch, with_crewai_config=False)
    problems, notes = mod.check_clean(root, log_dir_tmp=None, run_tools=True)
    warns = [n for n in notes if "covered area missing" in n]
    assert len(warns) == 1, f"expected exactly one WARN, notes={notes}"
    assert ".config" in warns[0] and "crewai" in warns[0]
    assert "was in baseline" in warns[0]
    # VISIBLE (note channel) but NOT a failure — legitimate cleanups are
    # absorbed, by design; the criterion itself must not go red
    assert not [p for p in problems if "J2(c)" in p]


def test_present_covered_area_emits_no_warning(tmp_path, monkeypatch):
    mod, root = _make_check_env(tmp_path, monkeypatch, with_crewai_config=True)
    problems, notes = mod.check_clean(root, log_dir_tmp=None, run_tools=True)
    assert not [n for n in notes if "covered area missing" in n]
    assert not [p for p in problems if "J2(c)" in p]


def test_never_recorded_area_does_not_warn(tmp_path, monkeypatch):
    # a dir that was NEVER in the baseline (e.g. crewai never installed on a
    # fresh machine) must NOT trigger the "was in baseline" warning
    mod, root = _make_check_env(tmp_path, monkeypatch, with_crewai_config=False)
    state = root / "runs" / "crewai_names_state.json"
    payload = json.loads(state.read_bytes().decode("utf-8"))
    del payload["fingerprints"]["USERPROFILE/.config/crewai"]
    state.write_bytes(json.dumps(payload).encode("utf-8"))
    problems, notes = mod.check_clean(root, log_dir_tmp=None, run_tools=True)
    assert not [n for n in notes if "covered area missing" in n]
