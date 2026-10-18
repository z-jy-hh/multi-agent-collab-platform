"""Round-8 C1: the CrewAI import-footprint tracer is THREE-WAY (K3) and its
allowlist is a real criterion — a new out-of-project location must FAIL.

Every case injects a fake child (``run_child=...``), so unit tests import no
crewai and spawn no process; the REAL child is exercised by the integration
doctor test.
"""

import json
import os
import subprocess

import pytest

from inspector.crewai_trace import SENTINEL
from inspector.runner import KNOWN_OUTSIDE_SUFFIXES, measure_crewai_mkdir_trace

CRED = r"C:\Users\someone\AppData\Local\crewai\credentials"
CONF = r"C:\Users\someone\.config\crewai"
FOREIGN = r"C:\Users\someone\AppData\Local\crewai\somewhere_new"


def _child(returncode=0, payload=None, stdout=None, stderr=""):
    def run(argv, timeout):
        out = stdout
        if out is None:
            out = (SENTINEL + json.dumps(payload)) if payload is not None else ""
        return subprocess.CompletedProcess(argv, returncode, out, stderr)
    return run


def _payload(**over):
    base = {"resolved": r"D:\proj\runs\crewai_storage", "resolved_inside": True,
            "inside_attempts": 12, "refused": 0, "outside": [], "seconds": 3.1}
    base.update(over)
    return base


def _outside(path, kind="Path.mkdir", stack=("a.py:1:f",)):
    return {"kind": kind, "path": path, "stack": list(stack)}


def test_pass_when_only_disclosed_locations():
    payload = _payload(refused=2, outside=[_outside(CRED), _outside(CONF)])
    result = measure_crewai_mkdir_trace(run_child=_child(payload=payload))
    assert result.status == "PASS"
    # nothing hides behind a bare "ok": both locations AND their crewai source
    # lines must be named in the details (K5)
    assert CRED in result.details and CONF in result.details
    assert "token_manager.py:88" in result.details
    assert "settings.py:26,76" in result.details
    assert "inside_project=True" in result.details and "outside_refused=2" in result.details


def test_fail_when_storage_resolves_outside_project():
    # the S4 blind spot, measured for real: the real resolver must not point
    # outside; PASS here would mean the redirect is ineffective
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_payload(
            resolved=r"C:\Users\someone\AppData\Local\CrewAI\proj",
            resolved_inside=False)))
    assert result.status == "FAIL"
    assert "OUTSIDE" in result.details


def test_fail_on_new_undisclosed_location():
    payload = _payload(outside=[_outside(CRED), _outside(FOREIGN)])
    result = measure_crewai_mkdir_trace(run_child=_child(payload=payload))
    assert result.status == "FAIL"
    assert FOREIGN in result.details and "NOT disclosed" in result.details


def test_warn_when_child_exits_nonzero():
    result = measure_crewai_mkdir_trace(run_child=_child(returncode=1, stderr="boom"))
    assert result.status == "WARN" and "UNVERIFIABLE" in result.details


def test_warn_when_payload_missing_from_stdout():
    result = measure_crewai_mkdir_trace(run_child=_child(stdout="crewai banner only"))
    assert result.status == "WARN" and "UNVERIFIABLE" in result.details


def test_warn_when_child_reports_import_failure():
    # a broken probe proves nothing about the redirect (K3): WARN, never PASS
    payload = _payload(import_error="ImportError: no crewai")
    result = measure_crewai_mkdir_trace(run_child=_child(payload=payload))
    assert result.status == "WARN"
    assert "UNVERIFIABLE" in result.details and "import_error" in result.details


def test_warn_when_child_times_out():
    def run(argv, timeout):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    result = measure_crewai_mkdir_trace(run_child=run)
    assert result.status == "WARN" and "UNVERIFIABLE" in result.details


def test_warn_when_child_cannot_start():
    def run(argv, timeout):
        raise OSError("spawn denied")

    result = measure_crewai_mkdir_trace(run_child=run)
    assert result.status == "WARN" and "UNVERIFIABLE" in result.details


def test_allowlist_entries_carry_a_source_line():
    # the allowlist is load-bearing: every entry must cite WHERE crewai creates
    # that path, otherwise a future leak would be absorbed with no provenance
    assert len(KNOWN_OUTSIDE_SUFFIXES) == 2
    for suffix, source in KNOWN_OUTSIDE_SUFFIXES.items():
        assert suffix.startswith("\\"), suffix
        assert "crewai_core/" in source and ":" in source, source


# --- R9-1: path containment is COMPONENT-level (round-9) -------------------
# Pure path cases: no directory is created, nothing touches the disk.
# The round-9 defect: the old raw-prefix idiom str(p).lower().startswith(root)
# judged a sibling like <root>_probe as INSIDE (reproduced live by the main
# agent, 2026-09-26). _is_inside must be the single source of truth.

from inspector.crewai_trace import _is_inside  # noqa: E402

ROOT = r"D:/proj/root"


def test_is_inside_direct_child_true():
    assert _is_inside(ROOT, ROOT + r"\runs\x") is True


def test_is_inside_root_itself_true():
    assert _is_inside(ROOT, ROOT) is True


def test_is_inside_sibling_prefix_probe_false():
    # THE round-9 RED fixture: <root>_probe shares a string prefix but is a
    # SIBLING, not a descendant
    assert _is_inside(ROOT, ROOT + r"_probe\runs\x") is False


def test_is_inside_digit_suffix_sibling_false():
    assert _is_inside(ROOT, ROOT + r"2\x") is False


@pytest.mark.skipif(os.name != "nt", reason="Windows path flavor is case-insensitive")
def test_is_inside_case_variants_agree():
    # same location, different case literals -> same verdict (True)
    assert _is_inside(ROOT, ROOT.lower() + r"\runs\x") is True
    assert _is_inside(ROOT.lower(), ROOT + r"\runs\x") is True


# --- R13-1/R13-2: crewai config fallback, EXACT-equality disclosure ---------
# All cases inject a fake child: no crewai, no process, no disk writes.
# TR is the child-reported process temp root. The exemption must be EXACT
# equality recomputed by the PARENT (hint never trusted); everything else
# still FAILs — including the adversarial siblings/variants below.

TR = r"C:\Users\someone\AppData\Local\Temp"


def _with_hint(item: dict, hint: str | None) -> dict:
    out = dict(item)
    out["fallback_hint"] = hint
    return out


def _r13(temp_root=TR, **over):
    payload = _payload(**over)
    if temp_root is not None:
        payload["temp_root"] = temp_root
    return payload


def test_r13_temp_root_mkdir_passes_with_named_source_and_note():
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_r13(outside=[_outside(CRED), _outside(TR)])))
    assert result.status == "PASS", result.details
    # named item-by-item WITH its source line (K5), plus a visible NOTE (K3):
    assert TR in result.details and "settings.py:76" in result.details
    assert "fallback chain" in result.details
    assert "NOTE: crewai config fallback wrote through " + TR in result.details
    assert "disclosed, not a leak" in result.details
    # the static allowlist was NOT touched (criterion: dynamic, not static)
    assert len(KNOWN_OUTSIDE_SUFFIXES) == 2


def test_r13_fallback_settings_file_path_passes():
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_r13(outside=[
            _outside(TR + r"\crewai_settings.json")])))
    assert result.status == "PASS", result.details
    assert "NOTE: crewai config fallback" in result.details


def test_r13_new_location_still_fails_despite_temp_root():
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_r13(outside=[_outside(TR), _outside(FOREIGN)])))
    assert result.status == "FAIL"
    assert FOREIGN in result.details and "NOT disclosed" in result.details


def test_r13_adversarial_sibling_suffix_still_fails():
    # <temp_root>_evil shares a string prefix but is NOT the temp root
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_r13(outside=[_outside(TR + "_evil")])))
    assert result.status == "FAIL"
    assert (TR + "_evil") in result.details and "NOT disclosed" in result.details


def test_r13_adversarial_subdirectory_still_fails():
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_r13(outside=[_outside(TR + r"\sub\dir")])))
    assert result.status == "FAIL"
    assert "NOT disclosed" in result.details


def test_r13_adversarial_case_variant_still_fails():
    # case-PRESERVING equality: a case-variant literal cannot be verified as
    # the same-process temp -> FAIL CLOSED. (In a real run both literals come
    # from the same gettempdir() call, so the legit fallback always matches.)
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_r13(outside=[_outside(TR.upper())])))
    assert result.status == "FAIL"
    assert "NOT disclosed" in result.details


def test_r13_self_consistent_case_literal_passes():
    # the inverse of the adversarial case test: when child temp_root and the
    # attempted path carry the SAME literal, equality holds regardless of
    # which case that literal uses (equality is literal-relative, not
    # hard-wired to one casing)
    upper = TR.upper()
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_r13(temp_root=upper, outside=[_outside(upper)])))
    assert result.status == "PASS", result.details
    assert "NOTE: crewai config fallback" in result.details


def test_r13_missing_temp_root_field_disables_exemption():
    # no temp_root in the payload -> the exemption is INERT: the temp-root
    # mkdir must FAIL exactly like any other undisclosed location (guards
    # against "missing field allows everything")
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_r13(temp_root=None, outside=[_outside(TR)])))
    assert result.status == "FAIL"
    assert TR in result.details and "NOT disclosed" in result.details


def test_r13_empty_temp_root_disables_exemption():
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_r13(temp_root="", outside=[_outside(TR)])))
    assert result.status == "FAIL"


def test_r13_parent_never_trusts_child_hint():
    # a LYING hint on an adversarial path must not flip the verdict: the
    # parent recomputes exact equality and ignores fallback_hint entirely
    liar = _with_hint(_outside(TR + "_evil"),
                      "crewai config fallback (crewai_core/settings.py:76 chain)")
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_r13(outside=[liar])))
    assert result.status == "FAIL"
    assert "NOT disclosed" in result.details


def test_r13_hint_absent_but_path_exact_still_passes():
    # the inverse: the parent does not NEED the hint — exact path equality
    # alone is the criterion
    bare = _with_hint(_outside(TR), None)
    result = measure_crewai_mkdir_trace(
        run_child=_child(payload=_r13(outside=[bare])))
    assert result.status == "PASS", result.details
    assert "NOTE: crewai config fallback" in result.details
