"""R17-P0a: session handle registry — the bridge between the API layer and
the SPI session handles created in background threads.

When ``DshBackend.open_session`` or ``WbBackend.open_session`` creates a
session handle, the handle is registered here by ``run_id``. The API layer's
permission card endpoint looks up the handle by ``run_id`` and delivers the
answer through ``handle.answer_permission(request, decision)``.

This module has no imports from dsh_bridge or any backend — it is a pure
registry (dict + lock). Handles are removed on ``close``.
"""

from __future__ import annotations

import threading
from typing import Any

_handles: dict[str, Any] = {}
_lock = threading.Lock()


def register(run_id: str, handle) -> None:
    with _lock:
        _handles[run_id] = handle


def unregister(run_id: str) -> None:
    with _lock:
        _handles.pop(run_id, None)


def get(run_id: str):
    with _lock:
        return _handles.get(run_id)


def pending_permission(run_id: str):
    """Return the pending PermissionRequest for this run, or None."""
    handle = get(run_id)
    if handle is None:
        return None
    wait = getattr(handle, "wait_permission", None)
    if wait is None:
        return None
    return handle._pending.get("request") if hasattr(handle, "_pending") else None
