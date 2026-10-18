"""Evolver version store + pure decision function (C8).

``decide`` is a PURE function: no file reads, no network, no LLM — its branch
coverage is unit-tested in isolation (D12 / U5). The threshold boundary is
fixed by the task book: KEEP if and only if ``score_delta > threshold``;
``== threshold`` and below are ROLLBACK.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.errors import GuardViolation
from core.paths import prompt_versions_dir, version_dirs

# metric weights for the composite score (documented in README)
W_QUALITY = 0.6
W_COST = 0.25
W_LATENCY = 0.15

EvolveDecision = Literal["KEEP", "ROLLBACK"]
# R14-1: a record's decision field now has THREE legal values over its
# lifetime. "PENDING" is the provisional state the auto flow writes BEFORE
# applying a candidate; it may be upgraded EXACTLY ONCE to a final
# "KEEP"/"ROLLBACK" (same version, same dir) and to nothing else. After that
# the record is final and the append-only refusal applies unchanged.
RecordDecision = Literal["PENDING", "KEEP", "ROLLBACK"]
PENDING = "PENDING"


@dataclass
class Metrics:
    """quality 0-100 (higher better); cost tokens, latency seconds (lower
    better). Normalized to [0,1] before weighting."""

    quality: float
    cost: float
    latency_s: float

    @staticmethod
    def _norm_quality(q: float) -> float:
        return max(0.0, min(1.0, q / 100.0))

    @staticmethod
    def _norm_cost(c: float) -> float:
        return 1.0 / (1.0 + max(0.0, c) / 1000.0)

    @staticmethod
    def _norm_latency(l: float) -> float:
        return 1.0 / (1.0 + max(0.0, l))


def composite_score(m: Metrics) -> float:
    """Pure: weighted normalized score, higher is better."""
    return (
        W_QUALITY * Metrics._norm_quality(m.quality)
        + W_COST * Metrics._norm_cost(m.cost)
        + W_LATENCY * Metrics._norm_latency(m.latency_s)
    )


def decide(baseline: Metrics, candidate: Metrics, threshold: float) -> EvolveDecision:
    """Pure decision. KEEP iff score_delta > threshold (strictly); the
    boundary case ``== threshold`` is ROLLBACK — asserted by D12."""
    delta = composite_score(candidate) - composite_score(baseline)
    return "KEEP" if delta > threshold else "ROLLBACK"


@dataclass
class VersionRecord:
    version: int
    reason: str
    decision: RecordDecision
    score_delta: float
    created_at: float
    files: dict[str, str]  # relative path -> sha256 of snapshot copy
    diff: str = ""         # unified diff snapshot -> candidate (N1); "" only for legacy records before backfill


class VersionStore:
    """Every candidate change is stored as a FULL snapshot (version N +
    reason + diff + complete copy of touched files), written atomically via
    temp file + rename (C8).

    Snapshot semantics (rectification N1, documented): the ``vNNN/`` folder
    holds the file content AS IT WAS BEFORE the candidate was applied —
    it is the rollback backup. The candidate content itself (what the
    optimizer proposed) is carried by the ``diff`` field of the record:
    ``candidate = snapshot + refinement rule``. KEEP versions therefore have
    live files == snapshot + rule; ROLLBACK versions have live files ==
    snapshot (byte-identical restore).
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = Path(base_dir) if base_dir else prompt_versions_dir()
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def base(self) -> Path:
        return self._base

    def _version_dir(self, version: int) -> Path:
        return self._base / f"v{version:03d}"

    def next_version(self) -> int:
        """max(existing)+1. PENDING (in-progress/tombstone) versions OCCUPY
        their slot (R14-1): a crashed auto run leaves vNNN with a PENDING
        record, and the next run proceeds as vNNN+1 — version numbers are
        never reused, which keeps every dir name <-> record version mapping
        injective (the invariant check_record_consistency enforces)."""
        existing = [int(p.name[1:]) for p in version_dirs(self._base)]
        return (max(existing) + 1) if existing else 1

    @staticmethod
    def file_hash(path: Path) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def snapshot(self, version: int, files: dict[str, Path]) -> dict[str, str]:
        """Copy whitelisted files into the version dir; returns path->sha256.
        Binary copy — never round-trip through text mode (EOL fidelity)."""
        vdir = self._version_dir(version)
        if vdir.exists():
            raise GuardViolation(f"version dir already exists: {vdir}")
        hashes: dict[str, str] = {}
        for rel, src in files.items():
            src = Path(src)
            dst = vdir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("wb", dir=str(dst.parent), delete=False) as tmp:
                tmp.write(src.read_bytes())
                tmp_path = Path(tmp.name)
            tmp_path.replace(dst)
            hashes[rel] = self.file_hash(dst)
        return hashes

    def save_record(self, version: int, reason: str, decision: RecordDecision,
                    score_delta: float, files: dict[str, str], diff: str = "") -> Path:
        """Write the version record. save_record does NOT assume the version
        directory exists; it is created on demand (append semantics, R11-1) —
        the only known crash path (fresh store + direct save_record ->
        FileNotFoundError) is closed here, before any bytes move the gate
        below still runs first.

        R10-1 write-side gate (residual R1): the gate used to live only at
        the backfill() entry, so a corrupted record written through THIS
        path stayed invisible until the next maintenance pass. Two checks,
        both BEFORE any bytes move — a refused write leaves no half-product:
          1. the EXISTING history must be append-only-consistent (a broken
             history must not be extendable);
          2. THIS write must not introduce a duplicate record version in
             another canonical directory, nor overwrite a FINAL record
             (append-only: record.json is written exactly once per version
             dir — R14-1 refines this to exactly one PENDING->final
             transition, see below).

        R14-1 record-state gate: the auto flow legitimately writes the SAME
        version twice (provisional before apply, final after golden
        compare). The ONLY permitted second write is upgrading the SAME
        version's PENDING record to ONE final KEEP/ROLLBACK. Everything
        else is refused: PENDING->PENDING, final->anything, and any write
        introducing a duplicate version elsewhere — the R10-1 intent (a
        bad/final history must not be extendable) is unchanged.
        """
        check_record_consistency(self._base)
        existing_record = self._version_dir(version) / "record.json"
        if existing_record.exists():
            prior = json.loads(existing_record.read_text(encoding="utf-8"))
            prior_decision = prior.get("decision")
            prior_version = prior.get("version")
            if (prior_decision == PENDING and prior_version == version
                    and decision in ("KEEP", "ROLLBACK")):
                pass  # the ONE permitted transition: PENDING -> final (R14-1)
            elif prior_decision == PENDING:
                raise GuardViolation(
                    f"version history inconsistent: {self._version_dir(version).name}/ "
                    f"carries a PENDING record version {prior_version}; only ONE "
                    f"final (KEEP/ROLLBACK) upgrade is allowed, refusing "
                    f"{decision} with {version}")
            else:
                raise GuardViolation(
                    f"version history inconsistent: {self._version_dir(version).name}/ "
                    f"already carries record version {prior_version}; append-only "
                    f"store, refusing overwrite with {version}")
        for vdir in version_dirs(self._base):
            if vdir == self._version_dir(version):
                continue  # own dir: the PENDING->final upgrade path above decided
            rec = vdir / "record.json"
            if rec.exists() and json.loads(
                    rec.read_text(encoding="utf-8")).get("version") == version:
                raise GuardViolation(
                    f"version history inconsistent: duplicate record version {version} "
                    f"would be created in {vdir.name}/")
        record = VersionRecord(
            version=version, reason=reason, decision=decision,
            score_delta=score_delta, created_at=time.time(), files=files,
            diff=diff,
        )
        path = self._version_dir(version) / "record.json"
        path.parent.mkdir(parents=True, exist_ok=True)  # R11-1: dir on demand
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record.__dict__, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
        return path

    def restore(self, version: int, project_root: Path) -> dict[str, str]:
        """Byte-level restore of a snapshot back into the live tree.
        Returns rel->sha256 of restored files (posix-style rel paths)."""
        vdir = self._version_dir(version)
        if not vdir.is_dir():
            raise GuardViolation(f"unknown version: {version}")
        restored: dict[str, str] = {}
        for src in vdir.rglob("*"):
            if not src.is_file() or src.name == "record.json":
                continue
            rel = src.relative_to(vdir)
            dst = Path(project_root) / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            restored[rel.as_posix()] = self.file_hash(dst)
        return restored

    def list_versions(self) -> list[int]:
        return sorted(int(p.name[1:]) for p in version_dirs(self._base))


def check_record_consistency(base_dir: Path | str | None = None) -> None:
    """Fail loud (GuardViolation) when the canonical version history is not
    append-only-consistent (R9-D3, closing review residual #4): a record
    version that mismatches its directory number, or two directories claiming
    the SAME record version. Wired at the backfill() entry so a corrupted
    history stops the maintenance pass instead of being silently processed.

    PENDING records (R14-1) count as "in progress" and pass through the SAME
    identity checks: a PENDING record's version must match its dir number
    and be unique, exactly like a final one — so a PENDING tombstone can
    never break the continuous ascending sequence (dir names sort ascending,
    each record's version == its dir number, all unique). A PENDING record
    is not special-cased away here: if the auto flow crashes after the
    provisional write, the PENDING dir REMAINS and surfaces through every
    read path (list_versions/next_version skip nothing).
    Deliberate scoping (round-9 report): the read-only endpoint
    ``/api/evolve/versions`` stays FAITHFUL (no dedup, no warning injection) —
    duplicates must SURFACE through this check, not be absorbed silently."""
    base = Path(base_dir) if base_dir else prompt_versions_dir()
    seen: dict[int, str] = {}
    for vdir in version_dirs(base):
        rec_path = vdir / "record.json"
        if not rec_path.exists():
            continue
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        ver = rec.get("version")
        name_num = int(vdir.name[1:])
        if ver != name_num:
            raise GuardViolation(
                f"version history inconsistent: {vdir.name}/ carries record version {ver}")
        if ver in seen:
            raise GuardViolation(
                f"version history inconsistent: duplicate record version {ver} "
                f"in {seen[ver]}/ and {vdir.name}/")
        seen[ver] = vdir.name
