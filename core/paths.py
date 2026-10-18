"""Single-point project-root constant (P5).

Every write path in the whole project MUST derive from ``PROJECT_ROOT`` so no
code can ever reach outside the project folder (C1). Nothing in this project
references any external directory; baseline snapshots of foreign paths are
taken by shell commands only and never appear here.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def configs_dir() -> Path:
    return PROJECT_ROOT / "configs"


def topologies_dir() -> Path:
    return configs_dir() / "topologies"


def prompts_dir() -> Path:
    return PROJECT_ROOT / "prompts"


def prompt_versions_dir() -> Path:
    """Evolver snapshot storage lives *inside* the guard whitelist (C8)."""
    return prompts_dir() / ".versions"


def version_dirs(base: Path) -> list:
    """Canonical version directories under ``base``: ``v`` + at least 3
    digits (``\\d{3,}``), sorted ascending. SINGLE source of truth for
    version enumeration (R9-2). Width policy (R10-2, DEFINED): ``\\d{3,}``
    — ``v001``..``v999`` and beyond (``v1234``) are accepted; ``v1`` (1-2
    digits) and non-numeric names (``vX92_tmp``, ``v004_hidden``) are
    rejected. NO UPPER BOUND (R11-2, documented): any digit count >= 3 is
    accepted — ``v999999`` and wider are all canonical; there is no
    rejection rule above 3 digits and none is planned. Derivation keeps up
    naturally: ``VersionStore.next_version()`` returns max+1, and the
    store's own ``v{N:03d}`` writer format widens past 999 (``v1000``) and
    past 999999 (``v1000000``) on its own — the enumeration accepts whatever
    the writer produces. Rationale for the >= 3 floor: tightening to exactly
    3 would have changed behaviour for histories that cross the thousand
    mark; the floor only excludes names the writer can never emit (``v1``,
    ``v01``, ``vX92_tmp``).
    All consumers (api/routes.py, evolver/versions.py,
    evolver/backfill_diffs.py) must go through this helper."""
    return sorted(
        p for p in base.glob("v*")
        if p.is_dir() and p.name[1:].isdigit() and len(p.name) >= 4
    )


def data_dir() -> Path:
    return PROJECT_ROOT / "data"


def corpus_dir() -> Path:
    return data_dir() / "corpus"


def runs_dir() -> Path:
    return PROJECT_ROOT / "runs"


def run_dir(run_id: str) -> Path:
    return runs_dir() / run_id


def reports_dir() -> Path:
    return PROJECT_ROOT / "reports"


def storage_path() -> Path:
    """SQLite database file (C13)."""
    return runs_dir() / "collab.sqlite3"
