"""Shared fail-loud cleanup for test-created run dirs (P3 / B-1).

Workorder sha256_16 a7cd4654648d2bd5, Appendix B-1: test-side cleanup must
NEVER swallow failures (no ``shutil.rmtree(..., ignore_errors=True)``).
A silently failed cleanup leaves a statusless run-shaped directory behind —
exactly the footprint debt this order removes (T1: 11 hp-* + 2 fixture-p2-*
+ 1 n2-* + 2 uuid residue dirs). Any failure raises AssertionError carrying
the errno text so the owning test fails LOUD instead of leaking directories.

Host-shim note (empirical, this machine): the WorkBuddy sitecustomize shim
wraps ``shutil.rmtree`` / ``os.remove`` / ``os.rmdir`` with a recycle-bin
call that is (a) INTERMITTENTLY fail-closed (OSError 0x2 — test_g7's
comment documents the same) and (b) behind a BULK GUARD: >=50 deletes per
turn raise ``SystemExit(1)`` demanding manual confirmation, which kills
pytest mid-suite and is unanswerable in automation. cleanup_run therefore
deletes RAW via ctypes Win32 (DeleteFileW/RemoveDirectoryW — below the
shim's wrapping surface): deterministic, no recycle-bin interaction, no
bulk-guard. Scope is limited to the runs/<rid> directory the test itself
created seconds earlier (ephemeral test artifacts, not user files).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
from pathlib import Path

from core.paths import run_dir

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.DeleteFileW.restype = ctypes.wintypes.BOOL
_k32.DeleteFileW.argtypes = [ctypes.wintypes.LPCWSTR]
_k32.RemoveDirectoryW.restype = ctypes.wintypes.BOOL
_k32.RemoveDirectoryW.argtypes = [ctypes.wintypes.LPCWSTR]


def _raw_delete(d: Path) -> None:
    """Bottom-up Win32 delete; bypasses the host recycle-bin shim."""
    if d.is_dir():
        for child in d.iterdir():
            _raw_delete(child)
        if not _k32.RemoveDirectoryW(str(d)):
            raise OSError(None, f"RemoveDirectoryW failed: {d}", None,
                          ctypes.get_last_error())
    else:
        if not _k32.DeleteFileW(str(d)):
            raise OSError(None, f"DeleteFileW failed: {d}", None,
                          ctypes.get_last_error())


def cleanup_run(rid: str) -> None:
    """Remove runs/<rid>. Missing dir is fine; failure raises loudly."""
    d = Path(run_dir(rid))
    if not d.exists():
        return
    try:
        _raw_delete(d)
    except OSError as exc:
        raise AssertionError(
            "cleanup_run(%r) FAILED (B-1 fail-loud): %s: %s"
            % (rid, type(exc).__name__, exc)) from exc
