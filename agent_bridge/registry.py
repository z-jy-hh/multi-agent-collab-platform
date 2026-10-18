"""R17-W4a: agents/registry.yaml loader + STRICT lint (design v3 sec 3.1-3.4).

P1 scope only: load, lint, and surface health - the registry is NOT wired to
dispatch yet (design sec 7 P1: dispatch keeps the R16 behaviour; the only
reader is the doctor row, gated by ``AGENT_REGISTRY=1``).

This component prints, so it OWNS its output encoding: at import time both
stdout and stderr are reconfigured to UTF-8 with ``errors="replace"``
(``_force_utf8_stdio``). Rationale (P1 review P1-F1): the child's stdout
encoding otherwise follows the parent environment (``PYTHONIOENCODING`` /
locale), so the SAME command produced GBK bytes under one shell and UTF-8
under another - unreadable by any fixed-decoding caller. With the reconfigure
plus ASCII-only banner/error literals, output bytes are environment-stable.
Per the project discipline, printed/assert messages are ASCII-only; data
values echoed from the registry may still contain non-ASCII and are then
safely replaced, never fatal.

Lint is fail-closed by construction:
- UNKNOWN KEYS ARE ERRORS at every level (never ignored - a typo must not
  silently disable a guard, design 3.2);
- permission tiers are a CLOSED set per adapter; the reserved loosening
  tokens (``-y``, ``--dangerously-skip-permissions``, ``bypassPermissions``,
  ``danger-full-access``) are rejected in ANY STRING VALUE at any parse level
  of the parsed registry (P1 review P1-F2 wording). YAML COMMENTS ARE NOT
  SCANNED - deliberately: the registry itself declares its prohibitions in
  comments (T1/T2 of sec 6.3);
- ``capabilities`` must carry ALL 17 flags, each ``{value, evidence}`` with an
  evidence prefix ``measured@`` / ``static@`` / ``assumed-fail-closed``;
- ``paid=true`` implies ``requires_authorization=true``; ``platform_limits``
  must be positive ints and ``consent`` must stay ``required`` while a paid
  backend exists;
- usage-shape consistency (F7/F10 institutionalised, sec 6.3-T5): a budget
  that can NEVER fire on the real wire (``usage_shape=context_used_size``
  with ``tokens/money`` set, or ``usage_shape=none`` with any backend-fed
  budget) is a load error, not a silent no-op.

Errors ACCUMULATE (one run lists every problem, never fail-fast). Format:
``[E-<tag>] <yaml.path>: <human message>``. CLI: ``python -m
agent_bridge.registry --lint [--file ...]`` -> exit 0 all-green / 1 lint
errors / 2 usage-or-IO. The gate (``registry_gate()``) is the ONLY thing that
decides whether the platform reads the file at all: anything other than
``AGENT_REGISTRY=1`` means the file is never opened (C4 counter-proof lives in
tests/unit/test_agent_registry.py, tripwire on ``_read_yaml``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from agent_bridge.spi import SANDBOX_SEMANTICS, USAGE_SHAPES


def _force_utf8_stdio() -> None:
    """Pin stdout/stderr to UTF-8 (errors replaced) at import time.

    P1-F1: the CLI's output encoding used to follow the parent environment
    (GBK under a plain console, UTF-8 under PYTHONIOENCODING=utf-8), so the
    same command produced different BYTES per shell and broke fixed-decoding
    callers. Reconfiguring here makes the bytes environment-stable; combined
    with ASCII-only literals, output is identical under any console. Guarded
    for exotic streams without ``reconfigure`` (e.g. captured/patched
    wrappers)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):   # e.g. closed/replaced stream mid-import
            pass


_force_utf8_stdio()   # startup = import time (P1 review P1-F1, item 2)

SCHEMA_ID = "agent_bridge.registry/v1"
REGISTRY_ENV = "AGENT_REGISTRY"

# loosening tokens that must NEVER appear anywhere in the registry text
RESERVED_TOKENS = ("-y", "--dangerously-skip-permissions", "bypassPermissions",
                   "danger-full-access")
# substring-safe long tokens (exact "-y" only — substring "-y" would false-hit)
RESERVED_SUBSTRINGS = ("--dangerously-skip-permissions", "bypassPermissions",
                       "danger-full-access")

CAPABILITY_NAMES = (
    "protocol_version", "transport", "permission_has_path", "permission_options",
    "usage_shape", "cancel_is_notification", "server_times_out", "supports_resume",
    "supports_background", "paid", "single_prompt_per_session", "single_root_cwd",
    "single_handler_slot", "audit_hash_chain", "sandbox_semantics",
    "supports_elicitation", "egress",
)
_BOOL_FLAGS = {n for n in CAPABILITY_NAMES} - {
    "protocol_version", "transport", "permission_options", "usage_shape",
    "sandbox_semantics",
}

_ALLOWED: dict[str, set[str]] = {
    "": {"schema", "defaults", "platform_limits", "backends", "bindings", "routing"},
    "defaults": {"deadline_permission_s", "deadline_prompt_s", "close_grace_s",
                 "budget_tool_calls", "budget_wall_s"},
    "platform_limits": {"consent", "daily_tool_calls", "hourly_tool_calls",
                        "daily_wall_s", "max_concurrent_runs_per_backend",
                        "counters_source"},
    "backend": {"adapter", "transport", "protocol_version", "command", "mcp_policy",
                "command_env", "cwd", "permission", "budgets", "version_lock",
                "healthcheck", "capabilities", "enabled", "requires_authorization",
                # W3: ordered transport preferences (optional; additive)
                "modes"},
    "mode": {"adapter", "transport", "protocol_version", "endpoint"},
    "command_env": {"set", "allow"},
    "cwd": {"template", "must_contain"},
    "permission": {"mode_default", "mode_allowed", "option_answer"},
    "budgets": {"context_used", "tokens", "money", "tool_calls", "wall_s", "turns"},
    "version_lock": {"agent_version"},
    "healthcheck": {"kind"},
    "capability": set(CAPABILITY_NAMES),
}
ADAPTERS = {"acp"}
TRANSPORTS = {"stdio"}
ADAPTER_MODE_UNIVERSE = {"acp": {"read-only", "workspace-write", "default"}}
OPTION_ANSWERS = {"server-offered-only"}
MCP_POLICIES = {"optional-strict"}
HEALTHCHECK_KINDS = {"dsh-check", "wb-check"}
COMMAND_PLACEHOLDERS = {"node", "dsh_bin", "wb_cli"}
EVIDENCE_PREFIXES = ("measured@", "static@", "assumed-fail-closed")


class RegistryError(Exception):
    """Raised by :func:`load_registry` when lint found any problem.

    ``errors`` carries EVERY problem (accumulated, never fail-fast), each line
    already formatted ``[E-<tag>] <yaml.path>: <message>``."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__(f"registry lint failed with {len(self.errors)} error(s)")


def _err(errors: list[str], tag: str, path: str, msg: str) -> None:
    errors.append(f"[E-{tag}] {path or '<root>'}: {msg}")


def _check_keys(node: dict, allowed: set[str], path: str,
                errors: list[str]) -> None:
    for key in sorted(set(node) - allowed):
        where = f"{path}.{key}" if path else key
        _err(errors, "unknown-key", where,
             f"unknown key (allowed: {', '.join(sorted(allowed))}) - "
             f"unknown keys are errors, never ignored (design 3.2)")


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _positive_num(node: dict, key: str, path: str, errors: list[str]) -> None:
    value = node.get(key)
    if value is None:
        return
    if not _is_num(value) or value <= 0:
        _err(errors, "platform-limits" if "platform_limits" in path else "defaults",
             f"{path}.{key}", f"must be a positive number, got {value!r}")


def _scan_reserved(node, path: str, errors: list[str]) -> None:
    """Reserved loosening tokens are rejected in ANY STRING VALUE at any
    parse level of the parsed registry (P1 review P1-F2 wording) - a tier
    whitelist, a command argument, anywhere in the DATA. YAML comments are
    NOT scanned - deliberately: the registry itself declares its prohibitions
    in comments (6.3-T2)."""
    if isinstance(node, str):
        if node in RESERVED_TOKENS or any(t in node for t in RESERVED_SUBSTRINGS):
            _err(errors, "reserved-flag", path or "<root>",
                 f"reserved loosening token {node!r} - must never appear in "
                 f"registry string values (design 3.2; 6.3 T2)")
    elif isinstance(node, dict):
        for key, value in node.items():
            _scan_reserved(key, f"{path}.{key}" if path else str(key), errors)
            _scan_reserved(value, f"{path}.{key}" if path else str(key), errors)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _scan_reserved(value, f"{path}[{i}]", errors)


def _lint_capabilities(backend: str, caps, errors: list[str]) -> None:
    path = f"backends.{backend}.capabilities"
    if not isinstance(caps, dict):
        _err(errors, "capabilities", path,
             f"must be a mapping with all 17 flags: {', '.join(CAPABILITY_NAMES)}")
        return
    _check_keys(caps, _ALLOWED["capability"], path, errors)
    for name in CAPABILITY_NAMES:
        if name not in caps:
            _err(errors, "capabilities", f"{path}.{name}",
                 "missing capability flag (all 17 are required)")
    for name, entry in caps.items():
        epath = f"{path}.{name}"
        if not isinstance(entry, dict) or set(entry) != {"value", "evidence"}:
            _err(errors, "capabilities", epath,
                 "must be exactly {value, evidence}")
            continue
        evidence = entry["evidence"]
        if not isinstance(evidence, str) or not (
                evidence.startswith(EVIDENCE_PREFIXES[:2])
                or evidence.startswith("assumed-fail-closed")):
            _err(errors, "evidence", f"{epath}.evidence",
                 f"must start with measured@ / static@ or be "
                 f"assumed-fail-closed, got {evidence!r}")
        value = entry["value"]
        if name in _BOOL_FLAGS and not isinstance(value, bool):
            _err(errors, "capabilities", f"{epath}.value",
                 f"expected bool, got {value!r}")
        if name == "protocol_version" and not isinstance(value, int):
            _err(errors, "capabilities", f"{epath}.value",
                 f"expected int, got {value!r} (handshake enforces the pinned value)")
        if name == "transport" and value not in TRANSPORTS:
            _err(errors, "capabilities", f"{epath}.value",
                 f"must be one of {sorted(TRANSPORTS)}, got {value!r}")
        if name == "usage_shape" and value not in USAGE_SHAPES:
            _err(errors, "capabilities", f"{epath}.value",
                 f"must be one of {list(USAGE_SHAPES)}, got {value!r}")
        if name == "sandbox_semantics" and value not in SANDBOX_SEMANTICS:
            _err(errors, "capabilities", f"{epath}.value",
                 f"must be one of {list(SANDBOX_SEMANTICS)}, got {value!r}")
        if name == "permission_options" and not (
                isinstance(value, list) and all(isinstance(v, str) for v in value)):
            _err(errors, "capabilities", f"{epath}.value",
                 "expected a list of optionId strings")


def _lint_usage_shape(backend: str, shape, budgets: dict, errors: list[str]) -> None:
    """F7/F10 institutionalised (6.3-T5): a budget that can never fire on the
    real wire is a CONFIG ERROR, never a silent no-op."""
    bpath = f"backends.{backend}.budgets"
    if shape == "context_used_size":
        for key in ("tokens", "money"):
            if budgets.get(key) is not None:
                _err(errors, "budget-shape", f"{bpath}.{key}",
                     f"set but capabilities.usage_shape=context_used_size - this "
                     f"gate can never fire on the real wire (F7/F10); set null "
                     f"(design 3.2 / 6.3-T5)")
    elif shape == "none":
        for key in ("context_used", "tokens", "money"):
            if budgets.get(key) is not None:
                _err(errors, "budget-shape", f"{bpath}.{key}",
                     f"set but capabilities.usage_shape=none - no backend-fed "
                     f"usage data exists; set null (design 3.2 / 6.3-T5)")


def _lint_backend(name: str, node, errors: list[str]) -> None:
    path = f"backends.{name}"
    if not isinstance(node, dict):
        _err(errors, "type", path, "backend entry must be a mapping")
        return
    _check_keys(node, _ALLOWED["backend"], path, errors)
    adapter = node.get("adapter")
    if adapter not in ADAPTERS:
        _err(errors, "value", f"{path}.adapter",
             f"must be one of {sorted(ADAPTERS)}, got {adapter!r}")
    if node.get("transport") not in TRANSPORTS:
        _err(errors, "value", f"{path}.transport",
             f"must be one of {sorted(TRANSPORTS)}, got {node.get('transport')!r}")
    version = node.get("protocol_version")
    if not isinstance(version, int) or isinstance(version, bool):
        _err(errors, "value", f"{path}.protocol_version",
             f"must be an int, got {version!r}")
    command = node.get("command")
    if not isinstance(command, list) or not all(isinstance(a, str) for a in command):
        _err(errors, "value", f"{path}.command", "must be a list of strings")
    else:
        import re
        for arg in command:
            for placeholder in re.findall(r"\{([a-z_]+)\}", arg):
                if placeholder not in COMMAND_PLACEHOLDERS:
                    _err(errors, "command", f"{path}.command",
                         f"unknown placeholder {{{placeholder}}} "
                         f"(allowed: {sorted(COMMAND_PLACEHOLDERS)})")
    if "mcp_policy" in node and node["mcp_policy"] not in MCP_POLICIES:
        _err(errors, "value", f"{path}.mcp_policy",
             f"must be one of {sorted(MCP_POLICIES)}, got {node['mcp_policy']!r}")
    if "modes" in node:
        modes = node["modes"]
        if not isinstance(modes, list) or not modes:
            _err(errors, "type", f"{path}.modes", "must be a non-empty list")
        else:
            for idx, mode in enumerate(modes):
                mpath = f"{path}.modes[{idx}]"
                if not isinstance(mode, dict):
                    _err(errors, "type", mpath, "must be a mapping")
                    continue
                _check_keys(mode, _ALLOWED["mode"], mpath, errors)
                if mode.get("adapter") not in ADAPTERS:
                    _err(errors, "value", f"{mpath}.adapter",
                         f"must be one of {sorted(ADAPTERS)}, got {mode.get('adapter')!r}")
                if mode.get("transport") not in TRANSPORTS:
                    _err(errors, "value", f"{mpath}.transport",
                         f"must be one of {sorted(TRANSPORTS)}, "
                         f"got {mode.get('transport')!r}")
                mver = mode.get("protocol_version")
                if not isinstance(mver, int) or isinstance(mver, bool):
                    _err(errors, "value", f"{mpath}.protocol_version",
                         f"must be an int, got {mver!r}")
                if "endpoint" in mode and not isinstance(mode["endpoint"], str):
                    _err(errors, "type", f"{mpath}.endpoint", "must be a string")
    env = node.get("command_env")
    if isinstance(env, dict):
        _check_keys(env, _ALLOWED["command_env"], f"{path}.command_env", errors)
        if not isinstance(env.get("set", {}), dict):
            _err(errors, "type", f"{path}.command_env.set", "must be a mapping")
        allow = env.get("allow", [])
        if not isinstance(allow, list) or not all(isinstance(a, str) for a in allow):
            _err(errors, "type", f"{path}.command_env.allow",
                 "must be a list of environment variable names")
    cwd = node.get("cwd")
    if isinstance(cwd, dict):
        _check_keys(cwd, _ALLOWED["cwd"], f"{path}.cwd", errors)
        template = cwd.get("template")
        import re as _re
        match = _re.fullmatch(r"runs/([a-z0-9_]+)_dispatch/\{run_id\}",
                              str(template or ""))
        if match is None:
            _err(errors, "cwd", f"{path}.cwd.template",
                 f"must be runs/<backend>_dispatch/{{run_id}} (single-root, "
                 f"per-run, F-6), got {template!r}")
        elif match.group(1) != name:
            _err(errors, "cwd", f"{path}.cwd.template",
                 f"dispatch dir segment {match.group(1)!r} != backend {name!r}")
        if not isinstance(cwd.get("must_contain"), bool):
            _err(errors, "type", f"{path}.cwd.must_contain", "must be a bool")
    perm = node.get("permission")
    if isinstance(perm, dict):
        ppath = f"{path}.permission"
        _check_keys(perm, _ALLOWED["permission"], ppath, errors)
        universe = ADAPTER_MODE_UNIVERSE.get(adapter, set())
        modes = perm.get("mode_allowed")
        if not isinstance(modes, list) or not modes or \
                not all(isinstance(m, str) for m in modes):
            _err(errors, "mode", f"{ppath}.mode_allowed",
                 "must be a non-empty list of tier names (closed set)")
        else:
            for mode in modes:
                if mode not in universe:
                    _err(errors, "mode", f"{ppath}.mode_allowed",
                         f"unknown tier {mode!r} for adapter {adapter!r} "
                         f"(closed set: {sorted(universe)})")
        if perm.get("mode_default") not in (modes or []):
            _err(errors, "mode", f"{ppath}.mode_default",
                 f"must be one of mode_allowed, got {perm.get('mode_default')!r}")
        if perm.get("option_answer") not in OPTION_ANSWERS:
            _err(errors, "value", f"{ppath}.option_answer",
                 f"must be one of {sorted(OPTION_ANSWERS)}, "
                 f"got {perm.get('option_answer')!r}")
    budgets = node.get("budgets")
    shape = None
    if isinstance(budgets, dict):
        bpath = f"{path}.budgets"
        _check_keys(budgets, _ALLOWED["budgets"], bpath, errors)
        for key in ("tool_calls", "wall_s", "turns"):
            value = budgets.get(key)
            if value is not None and (not _is_num(value) or value <= 0):
                _err(errors, "value", f"{bpath}.{key}",
                     f"platform counter must be a positive number, got {value!r}")
        caps = node.get("capabilities")
        if isinstance(caps, dict) and isinstance(caps.get("usage_shape"), dict):
            shape = caps["usage_shape"].get("value")
        _lint_usage_shape(name, shape, budgets, errors)
    lock = node.get("version_lock")
    if isinstance(lock, dict):
        _check_keys(lock, _ALLOWED["version_lock"], f"{path}.version_lock", errors)
        if not isinstance(lock.get("agent_version"), (str, type(None))):
            _err(errors, "type", f"{path}.version_lock.agent_version",
                 "must be a string or null")
    health = node.get("healthcheck")
    if isinstance(health, dict):
        _check_keys(health, _ALLOWED["healthcheck"], f"{path}.healthcheck", errors)
        if health.get("kind") not in HEALTHCHECK_KINDS:
            _err(errors, "value", f"{path}.healthcheck.kind",
                 f"must be one of {sorted(HEALTHCHECK_KINDS)}, "
                 f"got {health.get('kind')!r}")
    _lint_capabilities(name, node.get("capabilities"), errors)
    if not isinstance(node.get("enabled"), bool):
        _err(errors, "type", f"{path}.enabled", "must be a bool")
    if not isinstance(node.get("requires_authorization"), bool):
        _err(errors, "type", f"{path}.requires_authorization", "must be a bool")
    caps = node.get("capabilities")
    paid = isinstance(caps, dict) and isinstance(caps.get("paid"), dict) \
        and caps["paid"].get("value") is True
    if paid and node.get("requires_authorization") is not True:
        _err(errors, "paid-authz", f"{path}.requires_authorization",
             "paid=true => requires_authorization must be true (design 3.2)")


def lint_registry(data) -> list[str]:
    """Lint the parsed registry; returns EVERY problem (accumulated)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"[E-type] <root>: registry must be a mapping, got {type(data).__name__}"]
    _check_keys(data, _ALLOWED[""], "", errors)
    _scan_reserved(data, "", errors)
    if data.get("schema") != SCHEMA_ID:
        _err(errors, "schema", "schema",
             f"must be {SCHEMA_ID!r}, got {data.get('schema')!r}")
    defaults = data.get("defaults")
    if isinstance(defaults, dict):
        dpath = "defaults"
        _check_keys(defaults, _ALLOWED["defaults"], dpath, errors)
        for key in _ALLOWED["defaults"]:
            _positive_num(defaults, key, dpath, errors)
    limits = data.get("platform_limits")
    if not isinstance(limits, dict):
        _err(errors, "missing-key", "platform_limits",
             "required top-level section (D4 two-layer caps)")
    else:
        _check_keys(limits, _ALLOWED["platform_limits"], "platform_limits", errors)
        for key in ("daily_tool_calls", "hourly_tool_calls", "daily_wall_s",
                    "max_concurrent_runs_per_backend"):
            _positive_num(limits, key, "platform_limits", errors)
        if limits.get("consent") not in ("required", "off", None):
            _err(errors, "platform-limits", "platform_limits.consent",
                 f"must be 'required' or 'off', got {limits.get('consent')!r}")
    backends = data.get("backends")
    if not isinstance(backends, dict) or not backends:
        _err(errors, "missing-key", "backends", "required non-empty mapping")
        backends = {}
    for name, node in backends.items():
        _lint_backend(str(name), node, errors)
    # P4FIX-4 reconciliation: two backends sharing one cwd.template share a
    # workspace, so the N13 mutex refuses the second acquire and a multi-backend
    # run can never start. Catch the misconfiguration here instead of leaving a
    # confusing runtime "already locked" (it also confounded an S1 probe).
    seen_cwd: dict[str, str] = {}
    for name, node in backends.items():
        tpl = ((node or {}).get("cwd") or {}).get("template") if isinstance(node, dict) else None
        if not isinstance(tpl, str):
            continue
        other = seen_cwd.get(tpl)
        if other is not None:
            _err(errors, "cwd-duplicate", f"backends.{name}.cwd.template",
                 f"is identical to backends.{other}.cwd.template ({tpl!r}); each "
                 f"backend needs its own dispatch workspace or the N13 workspace "
                 f"mutex refuses the run")
        else:
            seen_cwd[tpl] = str(name)
    any_paid = any(
        isinstance(node, dict) and isinstance(node.get("capabilities"), dict)
        and isinstance(node["capabilities"].get("paid"), dict)
        and node["capabilities"]["paid"].get("value") is True
        for node in backends.values())
    if isinstance(limits, dict) and any_paid and limits.get("consent") != "required":
        _err(errors, "platform-limits", "platform_limits.consent",
             "must stay 'required' while a paid backend exists (design 3.2)")
    bindings = data.get("bindings")
    if isinstance(bindings, dict):
        for agent_id, target in bindings.items():
            if not isinstance(target, str) or not target.endswith(":") or \
                    target[:-1] not in backends:
                _err(errors, "bindings", f"bindings.{agent_id}",
                     f"must be '<declared-backend>:' with backend in "
                     f"{sorted(backends)}, got {target!r}")
    routing = data.get("routing")
    if isinstance(routing, dict):
        rpath = "routing"
        _check_keys(routing, {"prefix_separator", "unknown_prefix", "unbound_agent",
                              # G12: real agent_id -> backend mapping (additive)
                              "map"},
                    rpath, errors)
        if routing.get("prefix_separator") != ":":
            _err(errors, "routing", f"{rpath}.prefix_separator",
                 "must be ':' (design 3.1)")
        if routing.get("unknown_prefix") != "reject":
            _err(errors, "routing", f"{rpath}.unknown_prefix",
                 "must be 'reject' (fail-closed routing, design 3.1)")
        if routing.get("unbound_agent") != "crewai":
            _err(errors, "routing", f"{rpath}.unbound_agent",
                 "must be 'crewai' (unbound agent_ids keep the R15 behaviour)")
        route_map = routing.get("map")
        if route_map is not None:
            if not isinstance(route_map, dict):
                _err(errors, "type", f"{rpath}.map", "must be a mapping")
            else:
                for agent_id, backend_id in route_map.items():
                    target = str(backend_id or "").rstrip(":")
                    if not isinstance(agent_id, str) or not agent_id:
                        _err(errors, "value", f"{rpath}.map",
                             f"agent_id must be a non-empty string, got {agent_id!r}")
                    elif target not in (backends or {}):
                        _err(errors, "routing", f"{rpath}.map.{agent_id}",
                             f"must point to a declared backend, got {backend_id!r}")
    return errors


def _read_yaml(path: Path):
    """THE single file-read point of this module. The C4 tripwire patches
    this name to prove the gate actually keeps the platform from reading."""
    with open(path, "rb") as handle:
        return yaml.safe_load(handle)


def default_registry_path() -> Path:
    from core.paths import PROJECT_ROOT
    return PROJECT_ROOT / "agents" / "registry.yaml"


def load_registry(path: Path | str | None = None) -> dict:
    """Read + lint; raises :class:`RegistryError` carrying ALL errors."""
    file_path = Path(path) if path else default_registry_path()
    try:
        data = _read_yaml(file_path)
    except yaml.YAMLError as exc:
        raise RegistryError([f"[E-parse] {file_path}: YAML is not parseable: {exc}"])
    errors = lint_registry(data)
    if errors:
        raise RegistryError(errors)
    return data


def registry_gate() -> bool:
    """True ONLY when ``AGENT_REGISTRY=1``. Anything else (unset, 0, junk)
    means the registry file is NEVER read (first-line rollback: the old path
    runs without touching the file, C4)."""
    return os.environ.get(REGISTRY_ENV, "0") == "1"


def doctor_registry_row(path: Path | str | None = None) -> tuple[str, list[str]]:
    """Doctor hook (W4c): ('off', []) when gated off; ('PASS', [summary]) on a
    clean lint; ('FAIL', [every error line]) otherwise. NEVER mutates the
    9-item doctor scoring - callers print this as an extra line."""
    if not registry_gate():
        return "off", []
    try:
        data = load_registry(path)
    except RegistryError as exc:
        return "FAIL", list(exc.errors)
    backends = data.get("backends") or {}
    enabled = [n for n, node in backends.items()
               if isinstance(node, dict) and node.get("enabled")]
    return "PASS", [f"backends={len(backends)} enabled={len(enabled)} "
                    f"lint=0 errors (schema {SCHEMA_ID})"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent_bridge.registry",
        description="agents/registry.yaml strict loader/lint (design v3 3.2)")
    parser.add_argument("--lint", action="store_true",
                        help="lint the registry file (exit 0 green / 1 errors / 2 usage-IO)")
    parser.add_argument("--file", default=None,
                        help="registry path (default: agents/registry.yaml)")
    args = parser.parse_args(argv)
    if not args.lint:
        parser.print_help()
        return 2
    path = Path(args.file) if args.file else default_registry_path()
    try:
        data = _read_yaml(path)
    except OSError as exc:
        print(f"[E-io] {path}: cannot read: {exc}")
        return 2
    except yaml.YAMLError as exc:
        print(f"[E-parse] {path}: YAML is not parseable: {exc}")
        return 2
    errors = lint_registry(data)
    if errors:
        print(f"registry lint: {len(errors)} error(s) in {path}")
        for line in errors:
            print(f"  {line}")
        return 1
    backends = data.get("backends") or {}
    enabled = sorted(n for n, node in backends.items()
                     if isinstance(node, dict) and node.get("enabled"))
    print(f"registry lint: OK - schema {SCHEMA_ID}, backends={len(backends)} "
          f"(enabled: {', '.join(enabled) or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
