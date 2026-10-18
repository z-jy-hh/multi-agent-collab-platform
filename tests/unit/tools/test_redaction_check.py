"""N2 scanner + R10 quarantine tests (fixtures in tmp roots; the real runs/
and reports/ are never touched from here).

Covers: R2 (planted unmasked form must FAIL, named, without printing
content), R5 two-state semantics at the scanner level, R7 (allowlist
mechanism + scope: tests/ and runs/_quarantine/ are outside the scan), and
the R10 move-only quarantine entry with pointer + restore.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[3] / "tests" / "tools" / "audit_invariants.py"
_QTOOL = Path(__file__).resolve().parents[3] / "tests" / "tools" / "quarantine_plaintext.py"

SK = "sk-abcdef1234567890ghij"  # synthetic; lives in tests/ (not scanned)


def _load_tool():
    d = str(_TOOL.parent)
    if d not in sys.path:
        sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location("audit_invariants_n2", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_qtool():
    spec = importlib.util.spec_from_file_location("quarantine_plaintext", _QTOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mk_run(root: Path, name: str, trace_text: str) -> Path:
    rd = root / "runs" / name
    rd.mkdir(parents=True)
    (rd / "trace.jsonl").write_text(trace_text, encoding="utf-8")
    return rd


class TestRedactionCheck:
    def test_masked_artifact_passes(self, tmp_path):
        mod = _load_tool()
        _mk_run(tmp_path, "r1", '{"payload": "note ⟨REDACTED:openai_sk:abcd1234⟩"}')
        problems, notes = mod.check_redaction(tmp_path)
        assert problems == []
        assert any("scanned files=1" in n for n in notes)

    def test_planted_plaintext_fails_named_without_content(self, tmp_path):
        """R2 discriminator: an unmasked sk- form planted by hand MUST be
        flagged, naming the file, WITHOUT printing the secret."""
        mod = _load_tool()
        _mk_run(tmp_path, "r2", f'{{"payload": "key {SK}"}}\n')
        problems, _ = mod.check_redaction(tmp_path)
        assert any("runs/r2/trace.jsonl" in p and "kind=openai_sk" in p
                   for p in problems)
        assert all(SK not in p for p in problems), "content must never print"

    def test_reports_md_in_scope(self, tmp_path):
        mod = _load_tool()
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "R17_x_report.md").write_text(
            f"phone 13812345678 in prose\n", encoding="utf-8")
        problems, _ = mod.check_redaction(tmp_path)
        assert any("reports/R17_x_report.md" in p and "kind=phone_cn" in p
                   for p in problems)

    def test_quarantine_depth_out_of_scope(self, tmp_path):
        """runs/_quarantine/<ts>/<dir>/trace.jsonl is depth-3 and holds
        evidence moved out BECAUSE it contains plaintext: not scanned."""
        mod = _load_tool()
        q = tmp_path / "runs" / "_quarantine" / "ts1" / "r9"
        q.mkdir(parents=True)
        (q / "trace.jsonl").write_text(f"key {SK}\n", encoding="utf-8")
        problems, notes = mod.check_redaction(tmp_path)
        assert problems == []
        assert any("scanned files=0" in n for n in notes)

    def test_allowlist_excludes_named_files(self, tmp_path):
        mod = _load_tool()
        _mk_run(tmp_path, "r3", f"key {SK}\n")
        problems, notes = mod.check_redaction(
            tmp_path, extra_allows=["runs/r3/trace.jsonl"])
        assert problems == []
        assert any("allowlist" in n and "1" in n for n in notes)

    def test_default_allowlist_file_loaded(self, tmp_path):
        mod = _load_tool()
        _mk_run(tmp_path, "r4", f"key {SK}\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "tools").mkdir()
        (tmp_path / "tests" / "tools" / "redaction_allow.json").write_text(
            json.dumps(["runs/r4/trace.jsonl"]), encoding="utf-8")
        problems, _ = mod.check_redaction(tmp_path)
        assert problems == []

    def test_cli_registered(self, tmp_path):
        _mk_run(tmp_path, "r5", f"key {SK}\n")
        proc = subprocess.run(
            [sys.executable, str(_TOOL), "--check", "redaction",
             "--root", str(tmp_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_TOOL.parents[2]))
        assert proc.returncode == 1
        assert "runs/r5/trace.jsonl" in proc.stderr
        # clean fixture root passes
        clean = tmp_path / "clean_root"
        (clean / "runs" / "ok").mkdir(parents=True)
        (clean / "runs" / "ok" / "state.json").write_text(
            '{"status": "done"}', encoding="utf-8")
        proc2 = subprocess.run(
            [sys.executable, str(_TOOL), "--check", "redaction",
             "--root", str(clean)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_TOOL.parents[2]))
        assert proc2.returncode == 0


class TestQuarantinePlaintext:
    def test_move_pointer_manifest_and_restore(self, tmp_path, monkeypatch):
        mod = _load_qtool()
        qroot = tmp_path / "qroot"
        src = tmp_path / "runs" / "r6" / "trace.jsonl"
        src.parent.mkdir(parents=True)
        src.write_text(f"key {SK}\n", encoding="utf-8")
        original_bytes = src.read_bytes()  # text-mode write => CRLF on Windows
        import hashlib
        digest = hashlib.sha256(original_bytes).hexdigest()
        monkeypatch.setattr(mod, "QUARANTINE_ROOT", qroot)
        out = mod.quarantine_file(src, "R2 planted plaintext (fixture)")
        assert not src.exists(), "original must be MOVED away"
        qf = Path(out["quarantine_path"])
        assert qf.is_file() and qf.is_relative_to(qroot)
        assert hashlib.sha256(qf.read_bytes()).hexdigest() == digest
        pointer = Path(out["pointer"])
        assert pointer.exists() and pointer.name == "trace.jsonl.quarantined.json"
        p = json.loads(pointer.read_text(encoding="utf-8"))
        assert p["sha256"] == digest and p["reason"] and p["restorable"]
        # manifest in the quarantine dir carries the entry
        manifest = json.loads((qroot / p["ts"] / "manifest.json")
                              .read_text(encoding="utf-8"))
        assert any(e["sha256"] == digest for e in manifest["files"])
        # restore is byte-identical and removes the pointer
        r = mod.restore_file(pointer)
        assert r["sha256"] == digest
        assert src.exists() and src.read_bytes() == original_bytes
        assert not pointer.exists()

    def test_restore_collision_is_loud(self, tmp_path, monkeypatch):
        mod = _load_qtool()
        qroot = tmp_path / "qroot"
        src = tmp_path / "a.txt"
        src.write_text("data", encoding="utf-8")
        monkeypatch.setattr(mod, "QUARANTINE_ROOT", qroot)
        out = mod.quarantine_file(src, "fixture")
        (tmp_path / "a.txt").write_text("conflict", encoding="utf-8")
        import pytest
        with pytest.raises(RuntimeError, match="restore collision"):
            mod.restore_file(Path(out["pointer"]))


class TestDbColumnScan:
    """N2-FIX1: the runs registry (SQLite) text columns are scanned too."""

    def _mk_db(self, root: Path, task_rows: list[str]) -> Path:
        import sqlite3
        data = root / "runs"
        data.mkdir(parents=True, exist_ok=True)
        db = data / "collab.sqlite3"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, task TEXT)")
        conn.execute("CREATE TABLE ops (op_id TEXT PRIMARY KEY, value TEXT)")
        for i, t in enumerate(task_rows):
            conn.execute("INSERT INTO runs VALUES (?,?)", (f"r{i}", t))
        conn.commit()
        conn.close()
        return db

    def test_plaintext_task_flagged_by_table_column_kind(self, tmp_path):
        mod = _load_tool()
        self._mk_db(tmp_path, [f"key {SK} and 13812345678"])
        problems, _ = mod.check_redaction(tmp_path)
        assert any("collab.sqlite3#runs.task" in p and "kind=openai_sk" in p
                   for p in problems)
        assert any("kind=phone_cn" in p for p in problems
                   if "runs.task" in p)
        assert all(SK not in p for p in problems)

    def test_masked_and_clean_rows_not_flagged(self, tmp_path):
        mod = _load_tool()
        self._mk_db(tmp_path, [
            '{"state": ⟨REDACTED:openai_sk:abcd1234⟩}',
            "plain demo task with no rule-shaped forms",
        ])
        problems, _ = mod.check_redaction(tmp_path)
        assert problems == []

    def test_ops_value_column_scanned(self, tmp_path):
        import sqlite3
        mod = _load_tool()
        db = self._mk_db(tmp_path, [])
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO ops VALUES ('o1', ?)", (f"bearer {SK}",))
        conn.commit()
        conn.close()
        problems, _ = mod.check_redaction(tmp_path)
        assert any("collab.sqlite3#ops.value" in p and "kind=openai_sk" in p
                   for p in problems)

    def test_missing_tables_skipped_with_note(self, tmp_path):
        import sqlite3
        mod = _load_tool()
        db = self._mk_db(tmp_path, [])
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE ops")
        conn.commit()
        conn.close()
        problems, notes = mod.check_redaction(tmp_path)
        assert problems == []
        assert any("ops.value not readable" in n for n in notes)
