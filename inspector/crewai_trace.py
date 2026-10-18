"""CrewAI out-of-project footprint tracer (round-8 C1) — child process.

MEASURES what importing crewai actually tries to create on disk, instead of
constructing the path string ourselves. The round-5 S4 check does the latter,
so a crewai version that stopped using ``appdirs.user_data_dir`` would keep S4
green while writing outside the project. This tracer cannot be fooled that
way: it watches the creation calls themselves.

How it measures (K2-safe: it creates NOTHING outside the project):
  1. apply the F4 redirect (``core.crewai_storage``) BEFORE any crewai import;
  2. intercept ``pathlib.Path.mkdir`` / ``os.mkdir`` / ``os.makedirs``:
     attempts INSIDE the project are really executed (crewai keeps working),
     attempts OUTSIDE are recorded with their caller stack and REFUSED;
  3. ``import crewai`` (import-time mkdirs happen here) and call
     ``crewai_core.paths.db_storage_path()`` — the REAL resolver, which
     ``measure_crewai_redirect`` may not call in-process because it mkdirs at
     call time (K4);
  4. print ONE sentinel line carrying the JSON result: crewai writes banners
     to stdout/stderr, so the parent must not parse the whole stream.

Footprint note (honest): refusing a directory creation does not suppress a
*file* write into an already-existing out-of-project directory — crewai's own
``.crewai_write_test`` probe still runs inside ``~/.config/crewai`` and is
removed by crewai itself. That is byte-identical to what any ordinary
``import crewai`` does, i.e. this check adds no new location.

Run as: ``python -m inspector.crewai_trace [project_root]``. Never imports
crewai at module import time (K6: the trimmed-env layer proofs import every
project module).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

SENTINEL = "<<<CREWAI_TRACE_JSON>>>"

# R13-1: the settings fallback file crewai writes when the primary config
# location is not writable (crewai_core/settings.py:76 chain, candidate 2 is
# ``Path(tempfile.gettempdir()) / <this name>``; creating it mkdirs the
# PROCESS TEMP ROOT — the exact path the dynamic exemption in
# ``inspector.runner.measure_crewai_mkdir_trace`` allows).
SETTINGS_FALLBACK_BASENAME = "crewai_settings.json"

FALLBACK_HINT = "crewai config fallback (crewai_core/settings.py:76 chain)"


def _norm_literal(p: str) -> str:
    """Separator-normalised LITERAL for exact-equality comparisons (R13-1):
    forward slashes become backslashes and trailing separators are stripped,
    but CASE IS PRESERVED. The disclosure exemption must match the child's
    own ``tempfile.gettempdir()`` literal; a case variant cannot be verified
    as the same-process temp and must FAIL CLOSED in the parent (the hint
    here is advisory only — the parent never trusts it as a criterion)."""
    return p.replace("/", "\\").rstrip("\\")


def _fallback_hint(p: str, temp_root: str) -> str | None:
    """HINT ONLY (advisory, never a verdict criterion — R13-1): flags an
    out-of-project attempt whose path is EXACTLY the process temp root or
    the fallback settings file inside it. The parent recomputes the equality
    itself from the payload's ``temp_root``; this field exists so a human
    reading raw tracer output sees the attribution immediately."""
    if not temp_root:
        return None
    tr = _norm_literal(temp_root)
    np = _norm_literal(p)
    if np == tr or np == tr + "\\" + SETTINGS_FALLBACK_BASENAME:
        return FALLBACK_HINT
    return None


def _is_inside(root, p) -> bool:
    """True only if p is root itself or a DESCENDANT of root (path-component
    level, not a raw string prefix — a sibling like ``<root>_probe`` must NOT
    count as inside; reproduced live in round-9 R9-1).

    Single source of truth for path containment: ``inspector.crewai_trace``
    (recorder + main) and ``inspector.runner.measure_crewai_redirect`` all
    call this. Uses ``Path.is_relative_to`` (3.9+; the project venv is 3.11),
    which is case-insensitive on Windows via the PureWindowsPath flavor —
    matching the previous lowercased-prefix semantics without its
    sibling-prefix hole. Any resolution failure ⇒ False (unverifiable ≠
    inside, K3 spirit)."""
    try:
        return Path(p).resolve().is_relative_to(Path(root).resolve())
    except Exception:
        return False


def _install_recorder(root: Path, outside: list, counters: dict,
                      temp_root: str = "") -> None:
    real_path_mkdir = Path.mkdir
    real_os_mkdir = os.mkdir
    real_os_makedirs = os.makedirs

    def _inside(p) -> bool:
        return _is_inside(root, p)

    def _note(kind: str, p) -> bool:
        if _inside(p):
            counters["inside"] += 1
            return True
        frames = traceback.extract_stack()[:-3][-6:]
        outside.append({
            "kind": kind,
            "path": str(p),
            "fallback_hint": _fallback_hint(str(p), temp_root),
            "stack": [f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames],
        })
        counters["refused"] += 1
        return False

    def path_mkdir(self, *a, **k):
        if _note("Path.mkdir", self):
            return real_path_mkdir(self, *a, **k)
        return None

    def os_mkdir(p, *a, **k):
        if _note("os.mkdir", p):
            return real_os_mkdir(p, *a, **k)
        return None

    def os_makedirs(p, *a, **k):
        if _note("os.makedirs", p):
            return real_os_makedirs(p, *a, **k)
        return None

    Path.mkdir = path_mkdir
    os.mkdir = os_mkdir
    os.makedirs = os_makedirs


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        os.chdir(root)
    except OSError:
        pass

    counters = {"inside": 0, "refused": 0}
    outside: list = []
    # R13-1: resolve the process temp root BEFORE installing the recorder
    # (gettempdir() verifies writability with a transient FILE, not a mkdir,
    # but resolving first keeps the recorder's view of the run untouched).
    # The parent recomputes the fallback exemption from THIS value — it must
    # be the child's own literal, which is exactly what crewai's fallback
    # chain (settings.py:87) constructs the path from.
    try:
        temp_root = tempfile.gettempdir()
    except Exception:  # noqa: BLE001 — no temp root verifiable -> exemption inert
        temp_root = ""
    result = {
        "root": str(root), "redirect": None, "redirect_error": None,
        "import_error": None, "resolve_error": None, "resolved": None,
        "resolved_inside": False, "inside_attempts": 0, "refused": 0,
        "outside": outside, "seconds": 0.0, "temp_root": temp_root,
    }
    t0 = time.time()
    try:
        from core.crewai_storage import redirect_crewai_storage

        result["redirect"] = redirect_crewai_storage()
    except Exception as exc:  # noqa: BLE001
        result["redirect_error"] = f"{type(exc).__name__}: {exc}"

    _install_recorder(root, outside, counters, temp_root=temp_root)

    try:
        import crewai  # noqa: F401

        result["crewai"] = getattr(crewai, "__version__", "unknown")
    except BaseException as exc:  # noqa: BLE001
        result["import_error"] = f"{type(exc).__name__}: {exc}"

    try:
        from crewai_core.paths import db_storage_path

        resolved = db_storage_path()
        result["resolved"] = str(resolved)
        result["resolved_inside"] = _is_inside(root, resolved)
    except BaseException as exc:  # noqa: BLE001
        result["resolve_error"] = f"{type(exc).__name__}: {exc}"

    result["inside_attempts"] = counters["inside"]
    result["refused"] = counters["refused"]
    result["seconds"] = round(time.time() - t0, 2)
    print(SENTINEL + json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
