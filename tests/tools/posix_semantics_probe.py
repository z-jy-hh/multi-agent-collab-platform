#!/usr/bin/env python3
"""POSIX semantics probe (R10-4, O4 downgrade) — run on a Linux/macOS box to
verify, with ONE command, the path-semantics assumptions this platform's
guards rest on. The Windows CI machine has no second environment (no WSL
distro, no container), so this probe SHIPS VERIFIED-BY-CONSTRUCTION and is
executed by a third party instead.

Properties:
  * stdlib ONLY — no project imports, no pip installs (appdirs is probed
    opportunistically and reported as SKIPPED when absent);
  * ZERO disk writes — every path below is a pure ``Path`` object in memory;
    nothing is created, opened for writing, or removed;
  * deterministic — prints a JSON document whose ``all_passed`` field is the
    single verdict, plus a human-readable expectation table.

Checks:
  1. ``Path.is_relative_to`` is COMPONENT-level (a sibling sharing a string
     prefix is NOT inside);
  2. POSIX containment is CASE-SENSITIVE (``/a/ROOT`` does not contain
     ``/a/root/x``) — the mirror image of the Windows behaviour the
     inspector relies on (round-9 R9-3 case discipline);
  3. ``appdirs.user_data_dir("x", "CrewAI")`` on Linux resolves to
     ``~/.local/share/x`` (XDG) — printed only, never created (this is the
     path the redirect must cover; on POSIX it is NOT under any
     LOCALAPPDATA-equivalent the J2(c) baseline could fingerprint, which is
     exactly what round-8 O4 asks to be verified on a real POSIX box).

Environment guard (R11-3): if ``XDG_DATA_HOME`` is set in the environment,
check 3's hardcoded ``~/.local/share/x`` expectation does not apply (the
deployment overrides the XDG default), so check 3 SKIPS — recorded in the
JSON as ``"skipped": "XDG_DATA_HOME set"`` — and does NOT count as a
failure. The verdict semantics are unchanged: ``all_passed`` remains the
single criterion (executed checks must all match; skips are neither pass
nor fail), exit 0/1.

Usage (on Linux/macOS, from the project root or anywhere):
    python tests/tools/posix_semantics_probe.py
Exit code: 0 when every executed check matches its expectation, 1 otherwise
(a skip — appdirs absent or XDG_DATA_HOME set — alone does not fail the run).

Offline verification without a POSIX box (R11-3): the branch logic and the
expectation computation are factored into pure functions (``_appdirs_check``,
``_xdg_expected_for``, ``_skip_note``) that take their inputs as parameters
— they can be fed arguments and exercised without executing the POSIX
semantics assertions or touching the environment.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RESULTS: list[dict] = []


def _check(name: str, got, expected) -> None:
    RESULTS.append({
        "check": name,
        "got": got,
        "expected": expected,
        "ok": None if expected is None else (got == expected),
    })


def _xdg_expected_for(home: str) -> str:
    """Pure expectation computation (R11-3): the appdirs XDG default for
    user_data_dir("x", "CrewAI") given a home directory. Parameterised so it
    can be verified by feeding arguments, without a POSIX box."""
    return str(Path(home) / ".local" / "share" / "x")


def _appdirs_check(xdg_env: str | None, root: Path) -> None:
    """Check ③ (appdirs resolution), parameterised for offline verification
    (R11-3): ``xdg_env`` is the RAW XDG_DATA_HOME value (pass None to
    simulate an unset variable); ``root`` is the probe root. A non-empty
    value means the deployment overrides the XDG default, so the hardcoded
    ``~/.local/share/x`` expectation would be wrong — the check SKIPS
    (ok=None, never a failure) with the reason in its ``got`` field."""
    if xdg_env:
        _check("appdirs.user_data_dir('x', 'CrewAI')",
               f"SKIPPED: XDG_DATA_HOME set ({xdg_env!r}) — the "
               "~/.local/share/x expectation does not apply", None)
        return
    try:
        import appdirs  # optional; stdlib-only when absent
    except ImportError:
        _check("appdirs.user_data_dir('x', 'CrewAI')",
               "SKIPPED: appdirs not installed "
               "(pip install appdirs to complete this check)", None)
    else:
        resolved = appdirs.user_data_dir("x", "CrewAI")
        _check("appdirs.user_data_dir('x', 'CrewAI') == ~/.local/share/x",
               resolved, _xdg_expected_for(str(Path.home())))
        # and the resolved path must NOT live under the probe root
        _check("appdirs resolution is outside the project",
               Path(resolved).is_relative_to(root), False)


def _skip_note(results: list[dict]):
    """Pure JSON-field computation (R11-3): the ``skipped`` JSON entry is
    the string ``"XDG_DATA_HOME set"`` when that environment-driven skip
    fired (task-book wording), otherwise the plain skip count. Parameterised
    so it can be verified by feeding a RESULTS list."""
    for r in results:
        if r["ok"] is None and "XDG_DATA_HOME set" in str(r["got"]):
            return "XDG_DATA_HOME set"
    return len([r for r in results if r["ok"] is None])


def main() -> int:
    # ---- ① is_relative_to: component-level containment --------------------
    root = Path("/tmp/ma_probe_root")          # pure object; nothing exists
    _check("direct child is inside",
           (root / "runs" / "x").is_relative_to(root), True)
    _check("root itself is inside",
           root.is_relative_to(root), True)
    _check("deep descendant is inside",
           (root / "a" / "b" / "c").is_relative_to(root), True)

    # ---- ② same-prefix SIBLING directories are OUTSIDE --------------------
    # (the round-9 defect class: raw str.startswith idiom judged these inside)
    _check("sibling <root>_probe is outside",
           Path("/tmp/ma_probe_root_probe/runs/x").is_relative_to(root), False)
    _check("sibling <root>2 is outside",
           Path("/tmp/ma_probe_root2/x").is_relative_to(root), False)
    _check("sibling <root>-alt is outside",
           Path("/tmp/ma_probe_root-alt/x").is_relative_to(root), False)

    # ---- ③ POSIX containment is CASE-SENSITIVE ----------------------------
    # On POSIX, /tmp/MA_PROBE_ROOT is a DIFFERENT directory from
    # /tmp/ma_probe_root; containment must therefore be False. (On Windows
    # the same comparison is True — the platform difference R9-3 documents.)
    _check("posix case-sensitive containment (upper vs lower)",
           Path("/tmp/MA_PROBE_ROOT/runs/x").is_relative_to(root), False)

    # ---- ④ appdirs resolution on Linux (print-only, NEVER created) --------
    # R11-3: XDG_DATA_HOME guard — a non-empty value overrides the XDG
    # default, so the hardcoded expectation would misreport an environment
    # difference as a failure; the check SKIPS instead (never counts as a
    # failure). Branch logic lives in _appdirs_check (pure, feedable).
    _appdirs_check(os.environ.get("XDG_DATA_HOME"), root)

    # ---- report ------------------------------------------------------------
    executed = [r for r in RESULTS if r["ok"] is not None]
    skipped = [r for r in RESULTS if r["ok"] is None]
    failed = [r for r in executed if not r["ok"]]
    all_passed = bool(executed) and not failed

    print("== POSIX semantics probe (R10-4) — expectation table ==")
    for r in RESULTS:
        mark = {True: "OK  ", False: "FAIL", None: "SKIP"}[r["ok"]]
        print(f"  [{mark}] {r['check']}")
        print(f"         got={r['got']!r}  expected={r['expected']!r}")
    print("== JSON ==")
    print(json.dumps({
        "probe": "posix_semantics_probe (R10-4)",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "results": RESULTS,
        "all_passed": all_passed,
        "skipped": _skip_note(RESULTS),  # R11-3: reason string when the
        # XDG guard fired, else the skip count
    }, indent=2))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
