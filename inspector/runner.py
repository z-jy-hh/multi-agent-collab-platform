"""Post-run checker suite + system doctor (inspector layer, G4).

- ``run_checks``: six pluggable checkers produce a HealthReport per run;
  graphs/runner.py writes it to runs/<run_id>/health.json (D8).
- ``run_doctor``: environment / dependencies / configs / storage / offline
  smoke / LLM connectivity (Python stack only, R26; api_key is NEVER printed
  beyond its length and last 4 chars, C18).
"""

from __future__ import annotations

import json
import os
import sys
from importlib import import_module

from core import errors as errors_mod
from core.events import EventBus
from core.llm import FakeLLM
from core.memory import MemoryStore
from core.paths import storage_path
from core.registry import Registry, load_registry
from core.paths import configs_dir
from core.schemas import CheckResult, HealthReport

from inspector.checks import (
    arbitration,
    cost,
    match_quality,
    memory_invariant,
    schema,
    timeout,
)

CHECKER_MODULES = [schema, timeout, memory_invariant, match_quality, arbitration, cost]


def build_check_context(run_id: str, session_id: str, mode: str, events: list,
                        final_state: dict, memory: MemoryStore, params: dict,
                        llm=None, registry: Registry | None = None) -> dict:
    return {
        "run_id": run_id, "session_id": session_id, "mode": mode,
        "events": events, "final_state": final_state, "memory": memory,
        "params": params, "llm": llm, "registry": registry,
    }


def run_checks(ctx: dict) -> HealthReport:
    report = HealthReport(run_id=ctx["run_id"])
    for module in CHECKER_MODULES:
        try:
            report.checks.append(module.run(ctx))
        except Exception as exc:  # a broken checker must not kill the report
            report.checks.append(CheckResult(
                name=module.__name__.rsplit(".", 1)[-1], status="WARN",
                details=f"checker crashed: {type(exc).__name__}: {exc}"))
    passed = sum(1 for c in report.checks if c.status == "PASS")
    report.score = round(100.0 * passed / len(report.checks), 1)
    fails = [c.name for c in report.checks if c.status == "FAIL"]
    report.summary = "all checks passed" if not fails else f"failed: {', '.join(fails)}"
    return report


# -- doctor ----------------------------------------------------------------------


def _mask_key(key: str) -> str:
    return f"len={len(key)} last4={key[-4:] if key else '----'}" if key else "KEY NOT SET"


def check_storage(db_path=None) -> CheckResult:
    """Storage invariant, MEASURED not asserted (K2). Four states (round-5 C1):
      1. absent — file does not exist: judge the nearest EXISTING ancestor's
         writability with a REAL create-and-delete probe (tempfile.mkstemp +
         os.remove) run in a SUBPROCESS with a 5s hard timeout (N3, round-6;
         the earlier access-bit proxy was rejected — weak measurement on
         Windows and false FAILs on missing directories; rationale in the N3
         body comment below).
         writable → PASS (the store is created on first run); unwritable →
         FAIL; probe could not run at all (spawn failure etc.) → WARN with
         UNVERIFIABLE details (round-7 W1, three-way like S4) — a broken probe
         proves nothing and must neither escape (run_doctor calls this bare)
         nor fake a verdict. A timed-out child is treated as unwritable, and
         any .storage_probe_* residue it left inside the ancestor is swept
         (round-7 W2, path-gated: prefix + one level + files only);
      2. exists + wal      → PASS;
      3. exists + non-wal  → FAIL (K3 unchanged);
      4. exists + unreadable → FAIL (+ F3's sidecar hint).
    Accepts an arbitrary path so fixtures can exercise all states (K4)."""
    from pathlib import Path

    from core.paths import storage_path
    from core.storage import SQLiteOpStore

    path = Path(db_path).resolve() if db_path else storage_path()
    if not path.exists():
        # C1: judge the nearest EXISTING ancestor — the parent itself may not
        # exist on a fresh checkout (runs/ is untracked), and os.access on a
        # missing directory returns False, which produced a false FAIL in the
        # R5 review's fresh-checkout probe.
        # N3 (round-6): REAL create-delete probe, run in a SUBPROCESS with a
        # 5s hard timeout — os.access is a weak measurement on Windows (it
        # judged C:\Windows\System32\config writable), and an in-process
        # mkstemp HANGS indefinitely on that directory (os.open blocks instead
        # of denying; captured with faulthandler). A timed-out child was
        # killed at the open call, so it leaves no residue (K5); a failure
        # marks the parent unwritable.
        import subprocess as _sub

        ancestor = path.parent
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        parent_writable: bool | None = True
        extra_note = ""
        probe_code = (
            "import tempfile, os, sys\n"
            "fd, p = tempfile.mkstemp(dir=sys.argv[1], prefix='.storage_probe_')\n"
            "os.close(fd)\n"
            "os.remove(p)\n"
        )
        try:
            proc = _sub.run([sys.executable, "-c", probe_code, str(ancestor)],
                            capture_output=True, timeout=5)
            parent_writable = proc.returncode == 0
        except _sub.TimeoutExpired:
            parent_writable = False
            # W2 (round-7): a child killed between mkstemp and os.remove leaves
            # a .storage_probe_* residue the parent cannot name. Sweep it.
            # PATH GATE (K4): regular files ONLY, ONLY directly inside
            # <ancestor> (one level, no recursion), ONLY with the
            # '.storage_probe_' prefix — nothing else is ever touched; a sweep
            # failure never changes the verdict.
            extra_note = "timeout ⇒ treated as unwritable"
            try:
                swept = 0
                for child in ancestor.iterdir():
                    if child.is_file() and child.name.startswith(".storage_probe_"):
                        try:
                            child.unlink()
                            swept += 1
                        except OSError:
                            pass
                if swept:
                    extra_note += f"; swept={swept}"
            except OSError:
                pass
        except Exception as exc:  # W1 (round-7): the child may fail to START at
            # all (spawn failure, resource exhaustion, interpreter trouble).
            # That must not escape check_storage: run_doctor calls this bare
            # (no try), so an exception here kills the whole doctor. It must
            # also not be swallowed into PASS/FAIL — a broken probe proves
            # nothing about writability. Three-way WARN with UNVERIFIABLE,
            # same shape as S4 (K3).
            parent_writable = None
            extra_note = f"UNVERIFIABLE error={type(exc).__name__}: {exc}"
        if parent_writable is None:
            status = "WARN"          # cannot judge: neither PASS nor FAIL (K3)
            writable_text = extra_note
            extra_note = ""
        else:
            status = "PASS" if parent_writable else "FAIL"
            writable_text = str(parent_writable)
        return CheckResult(
            name="storage", status=status,
            details=f"sqlite state=absent (created on first run); "
                    f"parent_writable={writable_text} "
                    f"(create-delete probe in {ancestor}, subprocess+5s timeout) path={path}"
                    + (f"; {extra_note}" if extra_note else ""))
    try:
        mode = SQLiteOpStore.journal_mode(path)
        ops = SQLiteOpStore.ops_count_at(path)
    except Exception as exc:
        # F3 (round-4): a failed ro-open often means a CRASHED WRITER left a
        # -wal/-shm sidecar behind — say so in human terms instead of a bare
        # sqlite exception (mode=ro cannot run recovery; a read-write open
        # must happen once to recover).
        sidecars = [str(path) + suffix for suffix in ("-wal", "-shm")
                    if Path(str(path) + suffix).exists()]
        hint = ""
        if sidecars:
            hint = ("; -wal/-shm sidecar present — a writer may have crashed; "
                    "open the database read-write once to complete recovery, then re-run")
        return CheckResult(
            name="storage", status="FAIL",
            details=f"sqlite journal_mode=UNREADABLE path={path} "
                    f"error={type(exc).__name__}: {exc} sidecars={sidecars or 'none'}{hint}")
    ops_text = "n/a" if ops is None else str(ops)
    status = "PASS" if mode == "wal" else "FAIL"  # K3: non-wal is FAIL, not WARN
    return CheckResult(
        name="storage", status=status,
        details=f"sqlite journal_mode={mode} path={path} ops={ops_text}")


def measure_crewai_redirect() -> CheckResult:
    """Round-5 S4: MEASURED CrewAI redirect self-check — construct the path
    STRING that crewai_core.paths would compute (appdirs.user_data_dir(<name>,
    "CrewAI")) and assert it lives inside the project (the F4 appdirs patch
    makes it so). NEVER call crewai_core.paths.db_storage_path() here: it
    mkdirs at call time, so with an ineffective redirect the doctor itself
    would create out-of-project directories (K4)."""
    from pathlib import Path

    from core.paths import PROJECT_ROOT
    from inspector.crewai_trace import _is_inside

    try:
        import appdirs

        storage_name = os.getenv("CREWAI_STORAGE_DIR") or Path.cwd().name
        constructed = Path(appdirs.user_data_dir(storage_name, "CrewAI"))
        inside = _is_inside(PROJECT_ROOT, constructed)
        local = os.getenv("LOCALAPPDATA", "")
        legacy = Path(local) / "CrewAI" if local else None
        legacy_note = (f"legacy={legacy} exists={legacy.is_dir() if legacy else 'n/a'} "
                       f"(historical, untouched)")
        status = "PASS" if inside else "FAIL"
        return CheckResult(
            name="crewai_storage", status=status,
            details=f"measured: crewai would use {constructed} "
                    f"inside_project={inside} | {legacy_note}")
    except Exception as exc:
        return CheckResult(
            name="crewai_storage", status="WARN",
            details=f"UNVERIFIABLE: {type(exc).__name__}: {exc}")


# Round-8 C1: KNOWN and DISCLOSED out-of-project locations that crewai creates
# regardless of the appdirs redirect. Both are hardcoded inside crewai itself
# (no env switch exists, appdirs is never consulted), so they CANNOT be
# redirected without patching third-party internals — a route explicitly
# declined (round-8 decision ①: measure + disclose + content-level baseline).
# Key: normalised path SUFFIX; value: the source line that creates it.
KNOWN_OUTSIDE_SUFFIXES = {
    "\\appdata\\local\\crewai\\credentials":
        "crewai_core/token_manager.py:88 (LOCALAPPDATA/'crewai/credentials' "
        "mkdir + secret.key)",
    "\\.config\\crewai":
        "crewai_core/settings.py:26,76 (Path.home()/'.config/crewai' "
        "settings.json dir + write-test)",
}

# R13-1: the crewai CONFIG FALLBACK (<temp>/crewai_settings.json, whose
# creation mkdirs the process temp root; crewai_core/settings.py:76 chain)
# is deliberately NOT listed here and must never be added. It is exempted
# DYNAMICALLY in measure_crewai_mkdir_trace by EXACT equality against the
# child's own tempfile.gettempdir() (see _exact_norm). A static suffix
# entry would over-allow anything ending in the same text (e.g.
# <temp>\sub\crewai_settings.json or a <temp>_evil sibling) — the exact
# adversarial cases R13's tests pin as MUST-FAIL.


def _win_norm(path: str) -> str:
    return path.replace("/", "\\").lower()


def _exact_norm(path: str) -> str:
    """R13-1: separator-normalised, CASE-PRESERVING literal for the dynamic
    fallback exemption. Deliberately NOT ``_win_norm``: the exemption must be
    EXACT equality — no suffix/prefix/case fuzz. A case variant of the temp
    root cannot be verified as the same-process temp and therefore FAILS
    CLOSED (matching the R13 adversarial criteria and the fail-closed spirit
    of ``_is_inside``). In the real run both literals derive from the SAME
    ``tempfile.gettempdir()`` call in the SAME child process, so the legit
    fallback always matches exactly."""
    return path.replace("/", "\\").rstrip("\\")


def _known_outside(path: str) -> str | None:
    norm = _win_norm(path)
    for suffix, source in KNOWN_OUTSIDE_SUFFIXES.items():
        if norm.endswith(suffix):
            return source
    return None


def _trace_child(argv, timeout):
    import subprocess as _sub

    from core.paths import PROJECT_ROOT

    return _sub.run(argv, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=timeout, cwd=str(PROJECT_ROOT))


def measure_crewai_mkdir_trace(run_child=None, timeout: float = 60.0) -> CheckResult:
    """Round-8 C1: MEASURED import-time footprint (three-way, K3).

    Runs ``python -m inspector.crewai_trace``: a child that applies the
    redirect, intercepts Path.mkdir / os.mkdir / os.makedirs and REFUSES any
    out-of-project creation, then imports crewai and calls the real
    ``crewai_core.paths.db_storage_path()`` — which the in-process S4 check
    may never call because it mkdirs at call time (K4). This is what turns
    the S4 blind spot ("a crewai version that stops using appdirs stays
    green") into a check that can go RED.

    Verdict:
      * child crashed / timed out / payload missing -> WARN + UNVERIFIABLE
        (a broken probe proves nothing about the redirect);
      * the storage path resolves OUTSIDE the project -> FAIL (redirect
        ineffective, measured for real instead of string-constructed);
      * an out-of-project creation attempt is NOT in
        ``KNOWN_OUTSIDE_SUFFIXES`` and NOT the crewai config fallback
        -> FAIL (a NEW leak must be seen, never silently absorbed);
      * the fallback (R13-1) is exempted ONLY by EXACT equality, recomputed
        HERE in the parent from the child-reported ``temp_root`` — the
        child's ``fallback_hint`` is advisory and NEVER a verdict criterion:
        an attempt is the fallback iff its path equals ``temp_root`` or
        ``<temp_root>/crewai_settings.json`` literally (case-preserving,
        separator-normalised — see ``_exact_norm``). Missing/empty
        ``temp_root`` disables the exemption entirely (fail closed);
      * only disclosed locations -> PASS, and details NAME every one of them
        with its crewai source line — nothing hides behind a bare "ok"; a
        fallback hit additionally emits a visible NOTE (disclosed, not a
        leak), so the allowance is never silent (K3).
    """
    import subprocess as _sub

    from core.paths import PROJECT_ROOT
    from inspector.crewai_trace import SENTINEL

    argv = [sys.executable, "-m", "inspector.crewai_trace", str(PROJECT_ROOT)]
    child = run_child or _trace_child
    try:
        proc = child(argv, timeout)
    except _sub.TimeoutExpired:
        return CheckResult(
            name="crewai_mkdir_trace", status="WARN",
            details=f"UNVERIFIABLE: tracer child exceeded {timeout}s")
    except Exception as exc:  # the child may fail to START (spawn trouble)
        return CheckResult(
            name="crewai_mkdir_trace", status="WARN",
            details=f"UNVERIFIABLE: tracer child could not start: "
                    f"{type(exc).__name__}: {exc}")

    payload = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith(SENTINEL):
            try:
                payload = json.loads(line[len(SENTINEL):])
            except ValueError:
                payload = None
    if proc.returncode != 0 or payload is None:
        return CheckResult(
            name="crewai_mkdir_trace", status="WARN",
            details=f"UNVERIFIABLE: tracer exit={proc.returncode}, no JSON payload; "
                    f"stderr_tail={(proc.stderr or '').strip()[-200:]!r}")
    broken = [name for name in ("redirect_error", "import_error", "resolve_error")
              if payload.get(name)]
    if broken:
        return CheckResult(
            name="crewai_mkdir_trace", status="WARN",
            details="UNVERIFIABLE: " + "; ".join(f"{n}={payload[n]}" for n in broken))
    if not payload.get("resolved_inside"):
        return CheckResult(
            name="crewai_mkdir_trace", status="FAIL",
            details=f"measured: crewai storage resolves OUTSIDE the project "
                    f"resolved={payload.get('resolved')} (redirect ineffective); "
                    f"outside_refused={payload.get('refused')} "
                    f"seconds={payload.get('seconds')}")

    seen: dict[str, dict] = {}
    for item in payload.get("outside") or []:
        seen.setdefault(_win_norm(item.get("path", "")), item)

    # R13-1 dynamic exact-equality exemption (NOT a static allowlist entry —
    # see the KNOWN_OUTSIDE_SUFFIXES comment). The parent recomputes equality
    # ITSELF from the child-reported temp_root; a missing/empty temp_root
    # disables the exemption (fail closed).
    temp_root = payload.get("temp_root") or ""
    fallback_exempt: dict[str, str] = {}
    if temp_root:
        tr = _exact_norm(temp_root)
        fallback_exempt = {
            tr: ("crewai_core/settings.py:76 fallback chain "
                 "(get_writable_config_path -> <temp>/crewai_settings.json "
                 "parent mkdir)"),
            tr + "\\crewai_settings.json": (
                "crewai_core/settings.py:76 fallback chain "
                "(fallback settings file passed to a mkdir)"),
        }
    unknown: dict[str, dict] = {}
    fallback_hits: dict[str, str] = {}
    known_hits: dict[str, dict] = {}
    for p, item in seen.items():
        src = fallback_exempt.get(_exact_norm(item.get("path", "")))
        if src is not None:
            fallback_hits[item.get("path", "")] = src
        elif _known_outside(p) is not None:
            known_hits[p] = item
        else:
            unknown[p] = item
    if unknown:
        shown = "; ".join(
            f"{item.get('kind')}:{item.get('path')} via "
            f"{[f for f in item.get('stack', [])][:2]}"
            for item in unknown.values())
        return CheckResult(
            name="crewai_mkdir_trace", status="FAIL",
            details=f"NEW out-of-project creation, NOT disclosed (verify it, then "
                    f"register it in KNOWN_OUTSIDE_SUFFIXES): {shown}")
    disclosed = "; ".join(
        [f"{item.get('path')} <- {_known_outside(p)}" for p, item in sorted(known_hits.items())]
        + [f"{p} <- {src}" for p, src in sorted(fallback_hits.items())])
    note = ""
    if fallback_hits:
        note = " | " + "; ".join(
            f"NOTE: crewai config fallback wrote through {p} — disclosed, "
            f"not a leak (dynamic exact-equality exemption, "
            f"NOT in KNOWN_OUTSIDE_SUFFIXES)"
            for p in sorted(fallback_hits))
    return CheckResult(
        name="crewai_mkdir_trace", status="PASS",
        details=f"measured: resolved={payload.get('resolved')} inside_project=True; "
                f"inside_attempts={payload.get('inside_attempts')} "
                f"outside_refused={payload.get('refused')} "
                f"seconds={payload.get('seconds')} | disclosed_outside: "
                + (disclosed or "none") + note)


def run_doctor() -> HealthReport:
    report = HealthReport(run_id="doctor")
    add = report.add

    # 1. python env
    add("python", "PASS", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    # 2. dependencies importable
    missing = []
    for mod in ("langgraph", "crewai", "fastapi", "pydantic", "yaml", "httpx"):
        try:
            import_module(mod)
        except Exception as exc:
            missing.append(f"{mod} ({exc})")
    add("dependencies", "PASS" if not missing else "FAIL",
        "all core deps importable" if not missing else "; ".join(missing))

    # 3. configs load (C14: modes are backend-driven)
    try:
        registry = load_registry(configs_dir())
        n_agents, n_modes = len(registry.agents()), len(registry.topology_names())
        add("configs", "PASS" if n_agents and n_modes >= 4 else "FAIL",
            f"{n_agents} agents, {n_modes} topologies: {registry.topology_names()}")
    except Exception as exc:
        add("configs", "FAIL", f"{type(exc).__name__}: {exc}")

    # 4. storage invariant (C13) — MEASURED from the file, not asserted (K2/K3)
    report.checks.append(check_storage())

    # 5. offline smoke: one single-mode run, fully in-memory
    try:
        from graphs.runner import run_topology

        # F2: unique run_id — traces are refuse-to-overwrite now
        smoke_run_id = f"doctor-smoke-{os.urandom(4).hex()}"
        final = run_topology("single", "doctor 冒烟测试", run_id=smoke_run_id,
                             session_id="doctor-smoke",
                             llm=FakeLLM(script=["doctor smoke reply"]))
        ok = bool(final.get("final_report"))
        add("offline_smoke", "PASS" if ok else "FAIL",
            "single topology end-to-end offline" if ok else "empty final report")
    except Exception as exc:
        add("offline_smoke", "FAIL", f"{type(exc).__name__}: {exc}")

    # 6. LLM config + connectivity — Python stack only (R26), key masked (C18)
    from core.llm import _load_env_file

    _load_env_file()
    key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    if not key:
        add("llm", "PASS", "no key configured — offline FakeLLM mode (rollback path §五)")
    else:
        add("llm", "PASS", f"key present ({_mask_key(key)}), base_url={base_url}")
        try:
            import httpx

            resp = httpx.get(base_url.rstrip("/") + "/models", timeout=5.0)
            # any HTTP status (even 401) proves the endpoint is reachable —
            # a TLS/Schannel failure would raise instead
            add("connectivity", "PASS",
                f"{base_url} reachable via Python stack (HTTP {resp.status_code})")
        except Exception as exc:
            add("connectivity", "WARN",
                f"{base_url} unreachable via Python stack: {type(exc).__name__}")

    # 7. CrewAI storage — MEASURED (round-5 S4): the redirect is applied, then
    # the path crewai would actually use is constructed and asserted to be
    # inside the project. NEVER call crewai_core.paths.db_storage_path() here
    # (K4: it mkdirs at call time). The legacy %LOCALAPPDATA% location is only
    # disclosed, never touched.
    try:
        import crews.factory  # noqa: F401  — applies the F4 appdirs redirect
    except Exception as exc:
        add("crewai_storage", "WARN", f"redirect import failed: {type(exc).__name__}: {exc}")
    report.checks.append(measure_crewai_redirect())

    # 7b. Round-8 C1: the S4 check above builds the path STRING itself, so a
    # crewai version that stopped consulting appdirs would keep it green while
    # writing outside the project. This one MEASURES the real import-time
    # footprint in a subprocess (K4: db_storage_path mkdirs at call time) and
    # refuses every out-of-project creation; anything not in the disclosed
    # allowlist FAILS. Cost: one crewai import (~4s), timeout 60s.
    report.checks.append(measure_crewai_mkdir_trace())

    passed = sum(1 for c in report.checks if c.status == "PASS")
    report.score = round(100.0 * passed / len(report.checks), 1)
    fails = [c.name for c in report.checks if c.status == "FAIL"]
    report.summary = "all doctor checks passed" if not fails else f"failed: {', '.join(fails)}"
    return report
