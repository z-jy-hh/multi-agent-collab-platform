"""Thread-safe event bus + JSONL trace writer (C12).

The graph runs in a worker thread while SSE lives on the asyncio loop:
``attach_loop`` makes ``emit`` marshal handler calls onto the loop via
``loop.call_soon_threadsafe``. A bounded ring buffer keeps every event so a
reconnecting SSE client can replay from its Last-Event-ID.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable

from core.redact import (redact_strings_deep, record_run_redaction,
                         redaction_enabled)
from core.schemas import Event


class EventBus:
    def __init__(self, maxlen: int = 10000) -> None:
        self._subs: list[Callable[[Event], None]] = []
        self._lock = threading.RLock()
        self._seq = 0
        self._buffer: deque[Event] = deque(maxlen=maxlen)
        self._loop: Any = None
        self._loop_thread: threading.Thread | None = None

    # -- wiring ---------------------------------------------------------------
    def subscribe(self, fn: Callable[[Event], None]) -> None:
        with self._lock:
            self._subs.append(fn)

    def unsubscribe(self, fn: Callable[[Event], None]) -> None:
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def attach_loop(self, loop: Any) -> None:
        """Call from the asyncio side once the server loop is running."""
        with self._lock:
            self._loop = loop
            self._loop_thread = threading.current_thread()

    # -- publish ---------------------------------------------------------------
    def emit(
        self,
        type: str,
        run_id: str,
        payload: dict | None = None,
        agent_id: str | None = None,
    ) -> Event:
        with self._lock:
            self._seq += 1
            event = Event(seq=self._seq, run_id=run_id, type=type,
                          agent_id=agent_id, payload=payload or {})
            self._buffer.append(event)
            subs = list(self._subs)
            loop = self._loop
            loop_thread = self._loop_thread
        if loop is not None and threading.current_thread() is not loop_thread:
            # worker thread -> asyncio loop (C12). R15-M6: the attached loop may
            # already be CLOSED (its uvicorn server shut down after an SSE test
            # or reload) — call_soon_threadsafe then raises RuntimeError BEFORE
            # any subscriber runs, silently killing the emitting run (no trace,
            # no state, no op-log row). Fall back to direct delivery and drop
            # the dead loop reference so later emits skip the marshal entirely.
            try:
                for fn in subs:
                    loop.call_soon_threadsafe(self._safe_call, fn, event)
            except RuntimeError:
                for fn in subs:
                    self._safe_call(fn, event)
                with self._lock:
                    if self._loop is loop:
                        self._loop = None
                        self._loop_thread = None
        else:
            for fn in subs:
                self._safe_call(fn, event)
        return event

    @staticmethod
    def _safe_call(fn: Callable[[Event], None], event: Event) -> None:
        try:
            fn(event)
        except Exception:  # a broken subscriber must not kill the run
            import sys
            print(f"eventbus: subscriber {getattr(fn, '__name__', fn)} failed",
                  file=sys.stderr)

    # -- replay (Last-Event-ID) -----------------------------------------------
    def replay(self, after_seq: int) -> list[Event]:
        with self._lock:
            return [e for e in self._buffer if e.seq > after_seq]

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq


class EventJSONLWriter:
    """Write every event as one JSON line (trace.jsonl, C13).

    Round-4 (F2): an EXISTING trace is ROTATED, never overwritten and never
    blocking — the previous evidence is preserved byte-exact as
    ``trace.jsonl.1`` and every older generation keeps its index
    (``.N`` = 第 N 代, oldest first); the new run starts fresh at the bare
    name. Deterministic: existing generations are never rewritten."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            n = 1
            while Path(f"{self._path}.{n}").exists():
                n += 1
            self._path.rename(Path(f"{self._path}.{n}"))
        self._lock = threading.Lock()
        self._fh = open(self._path, "w", encoding="utf-8")

    def __call__(self, event: Event) -> None:
        # N2: deterministic pre-persistence redaction of every string VALUE
        # in the payload (mask form ⟨REDACTED:kind:sha256_8⟩, no plaintext
        # kept). Counts merged into <run_dir>/redaction.json ONLY when
        # something actually matched (zero extra IO for clean events).
        dump = event.model_dump()
        if redaction_enabled():
            dump, counts = redact_strings_deep(dump)
            if counts:
                record_run_redaction(self._path.parent, counts)
        line = json.dumps(dump, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._fh.close()

    # P3 (T2): context-manager face — only ADDS semantics; existing callers
    # (explicit close) are untouched. __exit__ closes on BOTH normal exit and
    # exception paths, so a `with EventJSONLWriter(...)` block can never leak
    # the handle.
    def __enter__(self) -> "EventJSONLWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
