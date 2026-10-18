"""One-off backfill: add the ``diff`` field to historical version records
(rectification N1 / appendix A).

Algorithm (auditor-verified on real data, appendix A):
1. snapshot semantics: ``prompts/.versions/vNNN/<rel>`` holds the file content
   AS IT WAS BEFORE the candidate was applied (rollback backup);
2. candidate replay: ``candidate = snapshot_content + optimizer.RULES[kind][1]``
   where ``kind`` is parsed from ``record.reason`` after ``"due to: "``
   (multiple kinds on the same file append ONCE — matching the per-file merge
   in ``propose_changes``; the FIRST recognised kind decides);
3. diff: ``difflib.unified_diff(snapshot, candidate, fromfile=f"a/{rel}",
   tofile=f"b/{rel}")``;
4. cross-check KEEP versions: replayed candidate must be byte-identical to the
   live file (v003 anchor sha256_16 = a5ad2f34a30508f8). v001/v002 are exempt:
   their live files were legitimately hand-edited afterwards (crit11 fixes).

Idempotent: records already carrying ``diff`` are skipped. Existing fields are
never modified — only the ``diff`` key is ADDED (temp file + rename, K4).
Unreplayable versions get ``"diff": null`` + ``"diff_unavailable_reason"`` —
never an empty string pretending to be a diff (K4).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import tempfile
from pathlib import Path

from core.paths import prompt_versions_dir, version_dirs, PROJECT_ROOT
from evolver.optimizer import RULES
from evolver.versions import check_record_consistency


def parse_kinds(reason: str) -> list[str]:
    if " due to: " not in reason:
        return []
    tail = reason.split(" due to: ", 1)[1]
    return [k.strip() for k in tail.split(",") if k.strip()]


def replay_candidate(snapshot_content: str, kinds: list[str]) -> str:
    """First recognised kind's refinement is appended exactly once."""
    for kind in kinds:
        if kind in RULES:
            return snapshot_content + RULES[kind][1]
    return snapshot_content


def replay_candidate_sha16(snapshot_content: str, kinds: list[str]) -> str:
    return hashlib.sha256(replay_candidate(snapshot_content, kinds).encode("utf-8")).hexdigest()[:16]


def backfill(versions_dir: Path | None = None, project_root: Path | None = None) -> dict:
    root = Path(project_root) if project_root else PROJECT_ROOT
    base = Path(versions_dir) if versions_dir else prompt_versions_dir()
    check_record_consistency(base)  # R9-D3: corrupted history stops here, loud
    summary = {"updated": [], "skipped": [], "unavailable": [], "keep_crosscheck": {}}

    for vdir in version_dirs(base):
        record_path = vdir / "record.json"
        if not record_path.exists():
            continue
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if "diff" in record:  # idempotent
            summary["skipped"].append(vdir.name)
            continue

        files: dict[str, str] = record.get("files", {})
        kinds = parse_kinds(record.get("reason", ""))
        if len(files) != 1 or not kinds:
            record["diff"] = None
            record["diff_unavailable_reason"] = (
                f"expected exactly 1 file and a parsable kind; got files={list(files)}, kinds={kinds}")
            _atomic_update(record_path, record)
            summary["unavailable"].append(vdir.name)
            continue

        rel = next(iter(files))
        snapshot_path = vdir / rel
        if not snapshot_path.exists():
            record["diff"] = None
            record["diff_unavailable_reason"] = f"snapshot file missing: {rel}"
            _atomic_update(record_path, record)
            summary["unavailable"].append(vdir.name)
            continue

        snapshot_content = snapshot_path.read_text(encoding="utf-8")
        candidate = replay_candidate(snapshot_content, kinds)
        diff = "".join(difflib.unified_diff(
            snapshot_content.splitlines(keepends=True),
            candidate.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        record["diff"] = diff

        if record.get("decision") == "KEEP":
            live = root / rel
            actual16 = hashlib.sha256(live.read_bytes()).hexdigest()[:16] if live.exists() else "ABSENT"
            replay16 = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16]
            summary["keep_crosscheck"][vdir.name] = {
                "replay_sha256_16": replay16, "live_sha256_16": actual16,
                "match": actual16 == replay16,
            }
        _atomic_update(record_path, record)
        summary["updated"].append(vdir.name)
    return summary


def _atomic_update(record_path: Path, record: dict) -> None:
    """Only ADD the new keys; every pre-existing field passes through untouched."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(record_path.parent),
                                     delete=False) as tmp:
        tmp.write(json.dumps(record, ensure_ascii=False, indent=2))
        tmp_path = Path(tmp.name)
    tmp_path.replace(record_path)


def main() -> int:
    summary = backfill()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
