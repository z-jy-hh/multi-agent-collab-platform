"""N2 R10: quarantine entry for archived plaintext (move, never rewrite).

The owner decision for already-persisted plaintext is QUARANTINE, never
in-place rewriting:
  * the file is MOVED to ``D:\\ai-memory-drop\\quarantine\\<ts>\\``;
  * a manifest (per-file sha256 + reason) travels with it;
  * a POINTER record (``<name>.quarantined.json``) stays at the original
    location so the move is auditable and reversible;
  * restoring moves the file back byte-verified.

Usage:
  python tests/tools/quarantine_plaintext.py --file runs/<id>/trace.jsonl \\
      --reason "pre-N2 plaintext found by --check redaction"
  python tests/tools/quarantine_plaintext.py --restore <pointer.json>

The redaction audit scans runs/* and reports/*.md; a quarantined file is
gone from both scopes, so the check returns green without any in-place
editing (red line: existing artifacts are never rewritten).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

QUARANTINE_ROOT = Path(r"D:\ai-memory-drop\quarantine")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def quarantine_file(path: Path, reason: str, *,
                    quarantine_root: Path | None = None) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    # resolved at CALL time so tests can redirect the root via monkeypatch
    root = Path(quarantine_root) if quarantine_root is not None else QUARANTINE_ROOT
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    digest = _sha256(path)
    qdir = root / ts
    qdir.mkdir(parents=True, exist_ok=True)
    dst = qdir / path.name
    if dst.exists():
        raise RuntimeError(f"quarantine collision: {dst} already exists")
    shutil.move(str(path), str(dst))
    moved_sha = _sha256(dst)
    if moved_sha != digest:
        shutil.move(str(dst), str(path))  # verify-then-move-back: zero loss
        raise RuntimeError(f"quarantine verification failed for {path}")
    pointer = {
        "pointer_version": "n2-v2",
        "original": str(path),
        "quarantine_path": str(dst),
        "sha256": digest,
        "reason": reason,
        "ts": ts,
        "restorable": True,
    }
    ppath = path.with_name(path.name + ".quarantined.json")
    ppath.write_text(json.dumps(pointer, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    manifest = qdir / "manifest.json"
    entry = {"file": path.name, "sha256": digest, "reason": reason,
             "bytes": dst.stat().st_size, "pointer": str(ppath)}
    data = {"tool": "tests/tools/quarantine_plaintext.py", "ts": ts,
            "files": []}
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            pass
    data["files"].append(entry)
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return {"pointer": str(ppath), "quarantine_path": str(dst),
            "sha256": digest}


def restore_file(pointer_path: Path) -> dict:
    pointer_path = Path(pointer_path)
    p = json.loads(pointer_path.read_text(encoding="utf-8"))
    src = Path(p["quarantine_path"])
    dst = Path(p["original"])
    if dst.exists():
        raise RuntimeError(f"restore collision: {dst} already exists")
    if _sha256(src) != p["sha256"]:
        raise RuntimeError("quarantined copy differs from pointer record - "
                           "restore refused, nothing changed")
    shutil.move(str(src), str(dst))
    if _sha256(dst) != p["sha256"]:
        raise RuntimeError(f"post-restore verification failed for {dst}")
    pointer_path.unlink()  # restore complete: the pointer served its purpose
    return {"restored": str(dst), "sha256": p["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="N2 R10: quarantine archived plaintext (move + sha256 + "
                    "pointer; never rewrite in place)")
    ap.add_argument("--file", default=None,
                    help="plaintext artifact to quarantine")
    ap.add_argument("--reason", default="unmasked plaintext (N2 --check "
                                        "redaction hit)")
    ap.add_argument("--restore", default=None,
                    help="pointer file (*.quarantined.json) to restore from")
    a = ap.parse_args(argv)
    if a.restore:
        out = restore_file(Path(a.restore))
        print(f"restored : {out['restored']}")
        print(f"sha256   : {out['sha256']}")
        return 0
    if not a.file:
        ap.error("--file or --restore is required")
    out = quarantine_file(Path(a.file), a.reason)
    print(f"quarantined : {out['quarantine_path']}")
    print(f"pointer     : {out['pointer']}")
    print(f"sha256      : {out['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
