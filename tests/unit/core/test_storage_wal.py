"""F1 (round-4): WAL switches are MEASURED, not assumed.

- SQLiteOpStore.__init__ raises StorageError when the WAL switch fails
  (previously the PRAGMA result was ignored);
- happy path: the measured mode is wal on a fresh store and on a copy of the
  real persistent store.
"""

import shutil
import sqlite3

import pytest

from core.errors import StorageError
from core.storage import SQLiteOpStore


def test_init_raises_when_wal_switch_fails(tmp_path):
    """Positive fixture: an in-memory database CANNOT enable WAL — the PRAGMA
    silently returns 'memory'. The old ignored-return-value code accepted that
    (no concurrency/crash-recovery guarantees); the measured __init__ must
    refuse it."""
    with pytest.raises(StorageError, match="journal_mode switch failed"):
        SQLiteOpStore(":memory:")


def test_delete_mode_copy_flips_back_to_wal_on_init(tmp_path):
    """Documents the known trap (audit 20260926): instantiating on a
    delete-mode copy switches it back to WAL — which the measured __init__
    now verifies instead of assuming."""
    dst = tmp_path / "delete_mode.sqlite3"
    seed = SQLiteOpStore(dst)
    seed._conn.close()
    conn = sqlite3.connect(str(dst))
    conn.execute("PRAGMA journal_mode=delete").fetchone()
    conn.close()
    assert sqlite3.connect(str(dst)).execute("PRAGMA journal_mode").fetchone()[0] == "delete"

    store = SQLiteOpStore(dst)  # switch re-applied AND measured this time
    assert store.journal_mode(dst) == "wal"
    store._conn.close()


def test_init_measures_wal_on_fresh_store(tmp_path):
    store = SQLiteOpStore(tmp_path / "fresh.sqlite3")
    assert store.journal_mode(tmp_path / "fresh.sqlite3") == "wal"
    store._conn.close()


def test_copy_of_real_store_measures_wal(tmp_path):
    from core.paths import storage_path

    src = storage_path()
    if not src.exists():
        pytest.skip("persistent store not created yet on this checkout")
    dst = tmp_path / "copy.sqlite3"
    shutil.copy2(src, dst)
    assert SQLiteOpStore.journal_mode(dst) == "wal"
