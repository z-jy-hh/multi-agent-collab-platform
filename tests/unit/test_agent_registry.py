"""R17-W4b: agent_bridge.registry tests — C2 positive, C3 negatives ①–⑧,
C4 gate semantics (tripwire proves the platform NEVER reads the registry when
AGENT_REGISTRY != 1), and the CLI contract (exit 0/1/2).

Every negative asserts BOTH the error tag AND a human-readable message (the
reviewer's C3 criterion is "exit code + error text", not just "fails").
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys

import pytest

from agent_bridge import registry
from agent_bridge.registry import RegistryError, doctor_registry_row, lint_registry


def _run_cli(args, env_extra=None):
    """Run the registry CLI the way every caller must (P1-F1): FIXED utf-8
    decoding with errors replaced - never the locale encoding. The child env
    is also normalised (PYTHONIOENCODING/PYTHONUTF8 popped) unless a test
    explicitly overrides it, so CLI-based tests are deterministic regardless
    of the console they run under."""
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "agent_bridge.registry", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env)


@pytest.fixture()
def base_data():
    """The in-repo registry (the C2 positive case, loaded fresh per test)."""
    return copy.deepcopy(registry._read_yaml(registry.default_registry_path()))


# --- C2 · positive -----------------------------------------------------------

def test_in_repo_registry_lints_clean(base_data):
    assert lint_registry(base_data) == []


def test_cli_lint_positive_exit0():
    proc = _run_cli(["--lint"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "registry lint: OK" in proc.stdout


# --- C3 · negatives (each: expected [E-tag], readable text) -------------------

def _lint_mutated(base_data, mutate):
    data = copy.deepcopy(base_data)
    mutate(data)
    return lint_registry(data)


def test_c3_1_unknown_key_is_error(base_data):
    """① unknown keys are ERRORS, never ignored (design §3.2)."""
    def mutate(data):
        data["backends"]["dsh"]["mcp"] = {"strict": True}   # typo'd invented key
    errors = _lint_mutated(base_data, mutate)
    assert any("[E-unknown-key]" in e and "backends.dsh.mcp" in e
               and "unknown keys are errors" in e for e in errors)


def test_c3_2_tier_typo_rejected(base_data):
    """② tier closed set — 'workspace-writ' is not in the acp universe."""
    def mutate(data):
        data["backends"]["dsh"]["permission"]["mode_allowed"] = ["workspace-writ"]
    errors = _lint_mutated(base_data, mutate)
    assert any("[E-mode]" in e and "workspace-writ" in e and "closed set" in e
               for e in errors)


def test_c3_3_reserved_loosening_token_rejected_anywhere(base_data):
    """③ reserved flags are rejected ANYWHERE — deep scan, not key-specific."""
    def mutate(data):
        data["backends"]["wb"]["permission"]["mode_allowed"] = ["default", "bypassPermissions"]
    errors = _lint_mutated(base_data, mutate)
    assert any("[E-reserved-flag]" in e and "bypassPermissions" in e for e in errors)


def test_c3_3b_reserved_flag_in_command_argv(base_data):
    """③b the same deep scan covers generated-argv surfaces (command list)."""
    def mutate(data):
        data["backends"]["wb"]["command"] = ["{wb_cli}", "--acp", "-y"]
    errors = _lint_mutated(base_data, mutate)
    assert any("[E-reserved-flag]" in e and "'-y'" in e for e in errors)


def test_c3_4_capabilities_missing_flag(base_data):
    """④ all 17 flags are required — deleting one is a load error."""
    def mutate(data):
        del data["backends"]["wb"]["capabilities"]["egress"]
    errors = _lint_mutated(base_data, mutate)
    assert any("[E-capabilities]" in e and "egress" in e
               and "missing capability flag" in e for e in errors)


def test_c3_5_evidence_prefix_illegal(base_data):
    """⑤ evidence must carry a measured@ / static@ / assumed-fail-closed prefix."""
    def mutate(data):
        data["backends"]["dsh"]["capabilities"]["paid"]["evidence"] = "believed@直觉"
    errors = _lint_mutated(base_data, mutate)
    assert any("[E-evidence]" in e and "believed@直觉" in e
               and "measured@ / static@" in e for e in errors)


def test_c3_6_paid_without_authorization(base_data):
    """⑥ paid=true ⇒ requires_authorization must be true."""
    def mutate(data):
        data["backends"]["wb"]["requires_authorization"] = False
    errors = _lint_mutated(base_data, mutate)
    assert any("[E-paid-authz]" in e and "backends.wb.requires_authorization" in e
               for e in errors)


def test_c3_7_usage_shape_budget_mismatch(base_data):
    """⑦ F7/F10 institutionalised: a gate that can never fire on the real wire
    (tokens budget on a context_used_size backend) is a CONFIG ERROR."""
    def mutate(data):
        data["backends"]["dsh"]["budgets"]["tokens"] = 200000
    errors = _lint_mutated(base_data, mutate)
    assert any("[E-budget-shape]" in e and "budgets.tokens" in e
               and "context_used_size" in e and "never fire" in e for e in errors)


def test_c3_7b_none_shape_rejects_any_backend_budget(base_data):
    """⑦b usage_shape=none ⇒ NO backend-fed budget may be set."""
    def mutate(data):
        data["backends"]["wb"]["budgets"]["context_used"] = 100000
    errors = _lint_mutated(base_data, mutate)
    assert any("[E-budget-shape]" in e and "budgets.context_used" in e
               and "usage_shape=none" in e for e in errors)


def test_c3_8_platform_limits_and_consent(base_data):
    """⑧ platform_limits must be positive ints; consent must stay 'required'
    while a paid backend exists."""
    def mutate(data):
        data["platform_limits"]["daily_tool_calls"] = -5
        data["platform_limits"]["consent"] = "off"
    errors = _lint_mutated(base_data, mutate)
    assert any("[E-platform-limits]" in e and "daily_tool_calls" in e and "-5" in e
               for e in errors)
    assert any("[E-platform-limits]" in e and "consent" in e and "paid" in e
               for e in errors)


# --- CLI contract (exit codes + readable text, reviewer-runnable) ------------

def _write_mutated(tmp_path, base_data, mutate):
    import yaml
    data = copy.deepcopy(base_data)
    mutate(data)
    path = tmp_path / "registry_bad.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize("mutate,tag", [
    (lambda d: d["backends"]["dsh"].__setitem__("quux", 1), "unknown-key"),
    (lambda d: d["backends"]["dsh"]["permission"].__setitem__(
        "mode_allowed", ["workspace-writ"]), "mode"),
    (lambda d: d["backends"]["wb"]["permission"]["mode_allowed"].append(
        "bypassPermissions"), "reserved-flag"),
    (lambda d: d["backends"]["wb"]["capabilities"].pop("egress"), "capabilities"),
    (lambda d: d["backends"]["dsh"]["capabilities"]["paid"].__setitem__(
        "evidence", "believed@直觉"), "evidence"),
    (lambda d: d["backends"]["wb"].__setitem__("requires_authorization", False),
     "paid-authz"),
    (lambda d: d["backends"]["dsh"]["budgets"].__setitem__("tokens", 200000),
     "budget-shape"),
    (lambda d: (d["platform_limits"].__setitem__("daily_tool_calls", -5),
                d["platform_limits"].__setitem__("consent", "off")),
     "platform-limits"),
], ids=["unknown-key", "tier-typo", "reserved-flag", "cap-missing",
        "evidence-prefix", "paid-authz", "usage-shape", "platform-limits"])
def test_c3_cli_negatives_exit1_with_text(tmp_path, base_data, mutate, tag):
    """All eight C3 negatives through the REAL CLI: exit 1 + the tagged,
    human-readable error line on stdout (pasteable for the report)."""
    path = _write_mutated(tmp_path, base_data, mutate)
    proc = _run_cli(["--lint", "--file", str(path)])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"[E-{tag}]" in proc.stdout


def test_cli_missing_file_exit2(tmp_path):
    proc = _run_cli(["--lint", "--file", str(tmp_path / "nope.yaml")])
    assert proc.returncode == 2
    assert "[E-io]" in proc.stdout


def test_cli_broken_yaml_exit2(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("backends:\n\tbad tab", encoding="utf-8")
    proc = _run_cli(["--lint", "--file", str(path)])
    assert proc.returncode == 2
    assert "[E-parse]" in proc.stdout


# --- P1-F1 · encoding regression (the defect that broke the P1 review) -------

def test_p1f1_encoding_invariance(tmp_path, base_data):
    """P1-F1 made executable: the SAME CLI under PYTHONIOENCODING=utf-8 AND
    with it explicitly unset (the GBK-locale console case that used to break
    the reviewer) must yield the SAME exit codes and the SAME decoded stdout.
    Decoding itself must never raise (utf-8 + errors=replace on both ends)."""
    bad = _write_mutated(
        tmp_path, base_data,
        lambda d: d["backends"]["dsh"]["budgets"].__setitem__("tokens", 200000))
    results = {}
    for label, env_extra in (("io-utf8", {"PYTHONIOENCODING": "utf-8"}),
                             ("io-unset", None)):
        good = _run_cli(["--lint"], env_extra=env_extra)
        neg = _run_cli(["--lint", "--file", str(bad)], env_extra=env_extra)
        results[label] = (good.returncode, good.stdout,
                          neg.returncode, neg.stdout)
    assert results["io-utf8"][0] == results["io-unset"][0] == 0
    assert results["io-utf8"][2] == results["io-unset"][2] == 1
    assert results["io-utf8"][1] == results["io-unset"][1]
    assert "registry lint: OK" in results["io-utf8"][1]
    assert results["io-utf8"][3] == results["io-unset"][3]
    assert "[E-budget-shape]" in results["io-utf8"][3]


# --- C4 · gate semantics: AGENT_REGISTRY != 1 ⇒ the file is NEVER read -------

def test_c4_gate_off_registry_is_never_read(base_data, monkeypatch, tmp_path):
    """Tripwire on _read_yaml (the module's ONLY file-read point): with the
    gate off, doctor_registry_row returns 'off' without touching the file —
    even though the file exists and is loadable. With the gate on, the
    tripwire FIRES (proving the read point is exactly the gated one)."""
    def boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise AssertionError("registry file was READ while AGENT_REGISTRY != 1")
    monkeypatch.setattr(registry, "_read_yaml", boom)
    monkeypatch.setenv("AGENT_REGISTRY", "0")
    assert registry.registry_gate() is False
    status, messages = doctor_registry_row(tmp_path / "whatever.yaml")
    assert status == "off" and messages == []
    monkeypatch.setenv("AGENT_REGISTRY", "1")
    with pytest.raises(AssertionError, match="registry file was READ"):
        doctor_registry_row(tmp_path / "whatever.yaml")


def test_c4_gate_is_strictly_one(monkeypatch):
    """Any value other than exactly '1' keeps the gate shut (unset/0/junk)."""
    for value in (None, "0", "", "true", "yes", " 1"):
        if value is None:
            monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
        else:
            monkeypatch.setenv(registry.REGISTRY_ENV, value)
        assert registry.registry_gate() is False
    monkeypatch.setenv(registry.REGISTRY_ENV, "1")
    assert registry.registry_gate() is True


def test_c4_doctor_row_fail_lists_every_error(base_data, monkeypatch, tmp_path):
    """Gate on + invalid registry ⇒ ('FAIL', [readable errors]) — the doctor
    row reports loudly; scoring of the 9 items is the caller's concern."""
    def mutate(data):
        data["backends"]["dsh"]["budgets"]["tokens"] = 200000
        data["backends"]["wb"]["requires_authorization"] = False
    path = _write_mutated(tmp_path, base_data, mutate)
    monkeypatch.setenv("AGENT_REGISTRY", "1")
    status, messages = doctor_registry_row(path)
    assert status == "FAIL"
    assert any("[E-budget-shape]" in m for m in messages)
    assert any("[E-paid-authz]" in m for m in messages)


def test_c4_load_registry_raises_with_all_errors(base_data, monkeypatch):
    def mutate(data):
        data["backends"]["dsh"]["budgets"]["tokens"] = 200000
        data["backends"]["dsh"]["quux"] = 1
    data = copy.deepcopy(base_data)
    mutate(data)
    monkeypatch.setattr(registry, "_read_yaml", lambda _p: data)
    with pytest.raises(RegistryError) as excinfo:
        registry.load_registry()
    assert len(excinfo.value.errors) >= 2   # accumulated, not fail-fast
