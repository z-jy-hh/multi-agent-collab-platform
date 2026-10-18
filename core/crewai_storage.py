"""CrewAI storage redirect — SINGLE implementation (round-6 D1/K2).

crewai_core.paths calls ``appdirs.user_data_dir(<project dir name>, "CrewAI")``
AT IMPORT TIME and mkdirs the result. The %LOCALAPPDATA%\\CrewAI base
directory cannot be changed via environment, so the only way to keep the
engine inside its own tree is to patch the appdirs module attribute BEFORE
crewai is imported anywhere.

Entry points that must call :func:`redirect_crewai_storage` before any
crewai import: ``main.py``, ``server.py``, ``tests/conftest.py`` (and
``crews/factory.py`` delegates to it as well).

This module MUST NEVER import crewai (K2) — only ``appdirs`` and
``core.paths``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from core.paths import runs_dir


def redirect_crewai_storage() -> str | None:
    """Patch ``appdirs.user_data_dir`` so CrewAI's import-time storage lands
    at ``runs/crewai_storage/`` inside the project. Returns the target path
    on success; on failure prints ONE stderr line and returns None — never
    silent (K5). Does not switch modes, create databases, or touch any of the
    historical directories under %LOCALAPPDATA%\\CrewAI."""
    try:
        import appdirs

        target = runs_dir() / "crewai_storage"
        target.mkdir(parents=True, exist_ok=True)
        appdirs.user_data_dir = lambda *a, **k: str(target)
        return str(target)
    except Exception as exc:
        # import-time helper: never raises (entries must keep working), but
        # the failure is LOUD on stderr (K5) — a silently ineffective redirect
        # is exactly the class of out-of-project writes this exists to prevent.
        print(f"[core.crewai_storage] WARNING: CrewAI storage redirect not applied: "
              f"{type(exc).__name__}: {exc} — crewai will write to its default "
              f"appdirs location", file=sys.stderr)
        return None
