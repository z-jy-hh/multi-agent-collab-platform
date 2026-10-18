"""SSE stream for one run (C12).

Order matters: subscribe to the live feed FIRST, then replay history, then
drain the live queue deduplicating by seq — no gap, no duplicates.
- reconnect: Last-Event-ID header becomes the seq cursor;
- 15s heartbeat keeps intermediaries from closing the stream;
- the stream CLOSES after a terminal event (run_finished / run_cancelled /
  run_error) so an already-finished run yields a finite response.
"""

from __future__ import annotations

import asyncio
import json
import os

TERMINAL_TYPES = {"run_finished", "run_cancelled", "run_error"}


def dsh_acp_enabled() -> bool:
    """R17-W9a: ``DSH_ACP_ENABLED`` switch (design §7-P5, §9-W9 ④).

    Default OFF (D1: the switch did not exist before P5 — it is now created).
    At 0, dsh-backend events are filtered out of the SSE stream (the backend
    can still run offline; only its SSE visibility is affected). At 1, all
    events flow through."""
    return os.environ.get("DSH_ACP_ENABLED", "0") == "1"


def _should_emit_event(event_type: str) -> bool:
    # F2: must-answer events are EXEMPT from the DSH_ACP_ENABLED filter —
    # hiding them would make the permission card unreachable (the turn
    # would hang until permission_timeout)
    if event_type in _MUST_ANSWER_EVENTS:
        return True
    if dsh_acp_enabled():
        return True
    return not event_type.startswith("dsh_")


_MUST_ANSWER_EVENTS = {"dsh_permission_request", "spi_permission_request",
                       "permission_answered"}


def _format(event) -> dict:
    return {
        "id": str(event.seq),
        "event": event.type,
        "data": json.dumps(event.model_dump(), ensure_ascii=False),
    }


async def event_stream(run_id: str, request, bus) -> dict:
    loop = asyncio.get_running_loop()
    bus.attach_loop(loop)
    try:
        cursor = int(request.headers.get("Last-Event-ID", "0"))
    except ValueError:
        cursor = 0

    queue: asyncio.Queue = asyncio.Queue()

    def _on_event(event) -> None:
        if event.run_id == run_id:
            queue.put_nowait(event)  # runs on the loop thread via call_soon_threadsafe

    bus.subscribe(_on_event)
    try:
        seen = cursor
        for event in bus.replay(cursor):
            if event.run_id != run_id:
                continue
            if not _should_emit_event(event.type):
                continue
            seen = event.seq
            yield _format(event)
            if event.type in TERMINAL_TYPES:
                return
        while True:
            event = await queue.get()
            if event.seq <= seen:
                continue
            if not _should_emit_event(event.type):
                continue
            seen = event.seq
            yield _format(event)
            if event.type in TERMINAL_TYPES:
                return
    finally:
        bus.unsubscribe(_on_event)
