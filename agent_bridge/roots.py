"""R17-P4FIX-4 (dsh): ONE place that decides where dispatch artifacts live.

Reviewer finding S2: ``gate_counters`` honoured ``$DSH_DISPATCH_ROOT`` while the
cwd/workspace-mutex root always used the project directory, so setting the
variable silently split "where we count" from "where we dispatch" - and the
dsh backend's own single-root boundary check then refused the cwd.

Everything now derives from :func:`dispatch_root`, and the dsh backend is given
the same root as an override so the boundary check and the gate agree.
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def dispatch_root() -> Path:
    """``$DSH_DISPATCH_ROOT`` when set, else ``<project>/runs``."""
    env = os.environ.get("DSH_DISPATCH_ROOT")
    if env:
        return Path(env)
    return project_root() / "runs"


def counter_file() -> Path:
    return dispatch_root() / "gate_counters.json"


def backend_workspace(backend_id: str, entry: dict | None = None, *,
                      root: str | Path | None = None) -> Path:
    """The workspace a backend's runs share (mutex key + cwd parent). Derived
    from the registry ``cwd.template`` when present so a custom layout keeps
    working, otherwise ``<dispatch_root>/<backend>_dispatch``.

    ``root`` overrides the platform dispatch root (W4-1: the native runtime
    hands the backend an override, so its cwd must be derived from the SAME
    root or the single-root boundary check refuses it)."""
    base = Path(root) if root is not None else dispatch_root()
    template = ((entry or {}).get("cwd") or {}).get("template")
    if template:
        filled = str(template).replace("{run_id}", "_mutex_")
        # templates are repo-relative ("runs/dsh_dispatch/{run_id}") but the
        # root may be overridden: keep the LAST component and re-base it.
        return (base / Path(filled).parts[-2]).resolve()
    return (base / f"{backend_id}_dispatch").resolve()


def backend_run_cwd(backend_id: str, entry: dict | None, run_id: str, *,
                    root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else dispatch_root()
    template = ((entry or {}).get("cwd") or {}).get("template")
    if template:
        return (backend_workspace(backend_id, entry, root=base) / run_id).resolve()
    return (base / f"{backend_id}_dispatch" / run_id).resolve()
