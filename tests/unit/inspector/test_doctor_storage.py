"""Doctor storage invariant (WAL measurement) — both directions.

K1 discipline: the positive fixture flips the journal mode on a COPY; the
real database is never modified, never checkpointed, never re-created.
The fallback store generator lets these tests run on a fresh checkout
where runs/collab.sqlite3 does not exist yet.
"""

import shutil
import sqlite3
from pathlib import Path

from core.paths import storage_path
from core.storage import SQLiteOpStore
from inspector.runner import check_storage


def _real_wal_db(tmp_path):
    """A genuine WAL database created by the engine itself — DETERMINISTIC:
    never copies the shared live store (runs/collab.sqlite3), whose -wal
    content races with the full-suite ordering (round-6 lesson: the copy
    silently lost the ops rows and read back ops=n/a). The store is
    checkpointed and closed before returning, so the file is self-contained."""
    gen = tmp_path / "generated.sqlite3"
    store = SQLiteOpStore(gen)
    from core.schemas import BlackboardOp

    store.append_op(BlackboardOp(
        op_id="fixture-op-1", session_id="s", agent_id="a", key="k",
        value="v", clock={"a": 1}, hlc=(1, 0, "a"), scope="private"))
    store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    store._conn.close()
    dst = tmp_path / "wal_copy.sqlite3"
    shutil.copy2(gen, dst)  # self-contained: main file holds everything
    return dst


def test_pass_on_real_wal_copy(tmp_path):
    """Fixture 1: a copy of a real WAL database must measure PASS with the
    measured value in details."""
    result = check_storage(_real_wal_db(tmp_path))
    assert result.status == "PASS"
    assert "journal_mode=wal" in result.details


def test_fail_on_delete_mode_copy(tmp_path):
    """Fixture 2 (positive, must catch): switching a COPY to journal_mode=delete
    must measure FAIL — proving the check reads reality instead of asserting."""
    dst = _real_wal_db(tmp_path)
    conn = sqlite3.connect(str(dst))
    conn.execute("PRAGMA journal_mode=delete").fetchone()
    conn.close()
    # the flip must have survived (this is why the accessor is a staticmethod:
    # instantiating SQLiteOpStore here would flip the copy back to WAL)
    assert SQLiteOpStore.journal_mode(dst) == "delete"

    result = check_storage(dst)
    assert result.status == "FAIL"
    assert "journal_mode=delete" in result.details


def test_negative_no_false_fail_on_normal_db(tmp_path):
    """Fixture 3: a healthy WAL database must not be falsely failed."""
    result = check_storage(_real_wal_db(tmp_path))
    assert result.status == "PASS"
    assert "journal_mode=delete" not in result.details


def test_absent_parent_writable_is_pass(tmp_path):
    """C1 state 1a: no database file + writable parent → PASS (the store is
    created on first run); details carry the measured state."""
    db = tmp_path / "fresh" / "collab.sqlite3"
    db.parent.mkdir(parents=True)
    result = check_storage(db)
    assert result.status == "PASS"
    assert "state=absent" in result.details
    assert "parent_writable=True" in result.details


def test_absent_parent_unwritable_is_fail(tmp_path, monkeypatch):
    """C1 state 1b: unwritable parent → FAIL. N3: the create-delete probe is
    exercised against C:\\Windows\\System32\\config (a directory a normal
    process cannot write) — the REAL measurement, not the os.access proxy."""

    import pytest as _pytest

    result = check_storage(Path(r"C:\Windows\System32\config") / "nope.sqlite3")
    if result.status == "PASS":
        _pytest.skip("environment unexpectedly allows writing System32\\config")
    assert result.status == "FAIL"
    assert "parent_writable=False" in result.details
    assert "create-delete probe" in result.details
    assert not (Path(r"C:\Windows\System32\config") / "nope.sqlite3").exists()


def test_measured_values_in_details(tmp_path):
    """K2: the details must carry MEASURED values (mode + ops count), not prose."""
    result = check_storage(_real_wal_db(tmp_path))
    assert "journal_mode=wal" in result.details
    assert "ops=" in result.details and "ops=n/a" not in result.details


def test_hot_journal_failure_is_explained(tmp_path, monkeypatch):
    """F3 (round-4): when the ro measurement fails AND -wal/-shm sidecars are
    present, the details must say a writer may have crashed and how to
    recover — not just dump a bare sqlite exception."""
    import sqlite3 as sq3

    dst = _real_wal_db(tmp_path)
    Path(str(dst) + "-wal").write_bytes(b"\x00" * 32)  # sidecar present

    def _boom(_db_path):
        raise sq3.OperationalError("unable to open database file")

    monkeypatch.setattr(SQLiteOpStore, "journal_mode", staticmethod(_boom))
    result = check_storage(dst)
    assert result.status == "FAIL"
    assert "sidecar present" in result.details
    assert "open the database read-write once" in result.details


def test_no_sidecar_no_hint(tmp_path, monkeypatch):
    """The hint must NOT appear when no sidecar exists (no false lead)."""
    dst = _real_wal_db(tmp_path)

    def _boom(_db_path):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(SQLiteOpStore, "journal_mode", staticmethod(_boom))
    result = check_storage(dst)
    assert result.status == "FAIL"
    assert "sidecar present" not in result.details
    assert "sidecars=none" in result.details


def test_absent_probe_spawn_failure_is_warn_unverifiable(tmp_path, monkeypatch):
    """W1 (round-7): if the probe subprocess cannot even START (e.g. OSError
    from a broken interpreter or resource exhaustion), the check must return
    WARN with UNVERIFIABLE details — never raise out of check_storage (that
    would kill the whole run_doctor), never fake a PASS/FAIL verdict (K3)."""
    import subprocess

    db = tmp_path / "fresh3" / "collab.sqlite3"
    db.parent.mkdir(parents=True)

    def _boom(*args, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(subprocess, "run", _boom)
    result = check_storage(db)
    assert result.status == "WARN"
    assert "UNVERIFIABLE" in result.details
    assert "OSError" in result.details


def test_timeout_sweeps_probe_residue_only(tmp_path, monkeypatch):
    """W2 (round-7): a probe killed between mkstemp and os.remove leaves a
    .storage_probe_* residue. The timeout path must sweep such files — and
    NOTHING else in the directory (K4 path gate: prefix + one level + files
    only). Verdict stays FAIL (timeout ⇒ treated as unwritable)."""
    import subprocess

    ancestor = tmp_path / "sweepme"
    ancestor.mkdir()
    orphan = ancestor / ".storage_probe_orphan"
    orphan.write_bytes(b"")
    keeper = ancestor / "keepme.txt"
    keeper.write_text("keep", encoding="utf-8")

    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=5)

    monkeypatch.setattr(subprocess, "run", _timeout)
    result = check_storage(ancestor / "collab.sqlite3")
    assert result.status == "FAIL"
    assert "timeout ⇒ treated as unwritable" in result.details
    assert "swept=1" in result.details
    assert not orphan.exists(), "probe residue must be swept"
    assert keeper.exists() and keeper.read_text(encoding="utf-8") == "keep"
