"""Persistence backends for the append-only op log (C13).

Two interchangeable backends behind one interface:
- ``InMemoryOpStore``  — tests and offline spikes.
- ``SQLiteOpStore``    — WAL mode, short transactions, thread-safe; survives
  restarts so the session/memory lists persist (C13).

Idempotency lives here: ``append_op`` returns False when the op_id already
exists, so a retried node execution writes exactly one row (D2).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from core.errors import StorageError
from core.redact import record_redaction, redact_strings_deep, redaction_enabled
from core.schemas import BlackboardOp

_SCOPE_BLACKBOARD = "blackboard"


class BaseOpStore:
    def append_op(self, op: BlackboardOp) -> bool:
        raise NotImplementedError

    def get_op(self, op_id: str) -> BlackboardOp | None:
        raise NotImplementedError

    def ops_for_session(self, session_id: str, scope: str | None = None) -> list[BlackboardOp]:
        raise NotImplementedError

    def ops_count(self) -> int:
        raise NotImplementedError


class InMemoryOpStore(BaseOpStore):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_id: dict[str, BlackboardOp] = {}
        self._order: list[str] = []

    def append_op(self, op: BlackboardOp) -> bool:
        with self._lock:
            if op.op_id in self._by_id:
                return False
            self._by_id[op.op_id] = op
            self._order.append(op.op_id)
            return True

    def get_op(self, op_id: str) -> BlackboardOp | None:
        with self._lock:
            return self._by_id.get(op_id)

    def ops_for_session(self, session_id: str, scope: str | None = None) -> list[BlackboardOp]:
        with self._lock:
            ops = [self._by_id[i] for i in self._order]
        if scope is not None:
            ops = [o for o in ops if o.scope == scope]
        return [o for o in ops if o.session_id == session_id]

    def ops_count(self) -> int:
        with self._lock:
            return len(self._order)


class SQLiteOpStore(BaseOpStore):
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.RLock()
        self._db_path = Path(path)
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row  # name-based row access
            self._conn.execute("PRAGMA journal_mode=WAL")
            # F1 (round-4): MEASURE, never assume — a failed WAL switch would
            # silently downgrade the ops log's concurrency/crash-recovery promises
            mode = str(self._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            if mode != "wal":
                raise StorageError(
                    f"journal_mode switch failed: expected 'wal', measured '{mode}' "
                    f"at {path}")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ops (
                    op_id      TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id   TEXT NOT NULL,
                    key        TEXT NOT NULL,
                    value      TEXT NOT NULL,
                    clock      TEXT NOT NULL,
                    hlc        TEXT NOT NULL,
                    ts         REAL NOT NULL,
                    scope      TEXT NOT NULL
                )
                """
            )
            self._conn.commit()
        except sqlite3.Error as exc:  # pragma: no cover - env dependent
            raise StorageError(f"cannot open sqlite store at {path}: {exc}") from exc

    def _marker_path(self) -> Path:
        """N2 audit marker next to the ops DB (store-level, not run-scoped)."""
        return self._db_path.parent / "redaction_ops.json"

    @staticmethod
    def _row_to_op(row: sqlite3.Row) -> BlackboardOp:
        return BlackboardOp(
            op_id=row["op_id"],
            session_id=row["session_id"],
            agent_id=row["agent_id"],
            key=row["key"],
            value=json.loads(row["value"]),
            clock=json.loads(row["clock"]),
            hlc=tuple(json.loads(row["hlc"])),
            ts=row["ts"],
            scope=row["scope"],
        )

    def append_op(self, op: BlackboardOp) -> bool:
        with self._lock:
            value = op.value
            if redaction_enabled():
                # N2: the op payload is serialized as JSON (below) -- mask
                # every rule hit before it reaches the database. The op store
                # is global (not run-scoped), so counts land in a marker file
                # next to the DB instead of a run's redaction.json.
                value, counts = redact_strings_deep(value)
                if counts:
                    record_redaction(self._marker_path(), counts)
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO ops VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    op.op_id,
                    op.session_id,
                    op.agent_id,
                    op.key,
                    json.dumps(value, ensure_ascii=False),
                    json.dumps(op.clock),
                    json.dumps(list(op.hlc)),
                    op.ts,
                    op.scope,
                ),
            )
            self._conn.commit()  # short transaction: commit immediately (C13)
            return cur.rowcount == 1

    def get_op(self, op_id: str) -> BlackboardOp | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM ops WHERE op_id=?", (op_id,))
            row = cur.fetchone()
        return self._row_to_op(row) if row else None

    def ops_for_session(self, session_id: str, scope: str | None = None) -> list[BlackboardOp]:
        q = "SELECT * FROM ops WHERE session_id=?"
        args: list = [session_id]
        if scope is not None:
            q += " AND scope=?"
            args.append(scope)
        q += " ORDER BY ts, rowid"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [self._row_to_op(r) for r in rows]

    def ops_count(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM ops")
            return int(cur.fetchone()[0])

    # -- read-only invariant accessors (doctor WAL measurement, K1) -----------
    # These are STATICMETHODS ON PURPOSE: instantiating SQLiteOpStore runs
    # PRAGMA journal_mode=WAL in __init__, which would flip a non-WAL database
    # back to WAL before it could ever be measured (empirically verified —
    # a delete-mode copy read `wal` right after construction). The ro
    # connection below never switches the mode, never creates tables, and
    # never writes. `immutable=1` is deliberately NOT used: an immutable
    # connection skips WAL recovery and misreports `delete`.

    @staticmethod
    def journal_mode(db_path) -> str:
        """Measured journal mode of the database file (lowercase), read via a
        mode=ro connection. No writes, no mode switch, no schema changes."""
        uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            conn.close()

    @staticmethod
    def ops_count_at(db_path) -> int | None:
        """Read-only ops count for reporting; None when the file has no ops
        table (i.e. it is not an ops store)."""
        uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM ops").fetchone()[0])
        except sqlite3.Error:
            return None
        finally:
            conn.close()
