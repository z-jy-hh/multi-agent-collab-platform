"""R17-P4FIX-3 (dsh): persisted platform-level dispatch counters.

Why this exists: ``DispatchGate.check_platform_limits`` needs REAL usage
numbers, not "is the configured cap non-zero". Before this module the
pre-run gate only validated configuration, so no run could ever be refused
for exhausting a cap (P4FIX-2 review, finding F4).

Counters live in ``<dispatch_root>/gate_counters.json`` (dispatch_root =
``$DSH_DISPATCH_ROOT`` or ``<project>/runs``), are written atomically, and
are intentionally simple: one day bucket, one hour bucket, and a per-backend
active-run map. The op-log remains the audit authority (W7 dual-source
reconcile); these counters are the cheap cap enforcer and can be rebuilt from
the op-log if they are ever lost.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()


def _root() -> Path:
    """One root for counters, cwds and workspace locks (reviewer finding S2)."""
    from agent_bridge.roots import dispatch_root
    return dispatch_root()


def _path() -> Path:
    from agent_bridge.roots import counter_file
    return counter_file()


def _day() -> str:
    return time.strftime("%Y-%m-%d")


def _hour() -> str:
    return time.strftime("%Y-%m-%dT%H")


def _empty() -> dict:
    return {"day": _day(), "daily": 0, "hour": _hour(), "hourly": 0,
            "active": {}, "corrupt": False}


def _read() -> dict:
    path = _path()
    base = _empty()
    if not path.exists():
        return base                      # first ever dispatch: legitimately zero
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("counter file is not a JSON object")
    except Exception:
        # Reviewer finding S6: a corrupt counter file used to be treated as
        # "zero usage", i.e. a cap bypass. It is now flagged so the gate can
        # fail closed instead of assuming the caps are untouched.
        base["corrupt"] = True
        return base
    if data.get("day") == base["day"]:
        base["daily"] = int(data.get("daily", 0) or 0)
    if data.get("hour") == base["hour"]:
        base["hourly"] = int(data.get("hourly", 0) or 0)
    active = data.get("active") or {}
    if isinstance(active, dict):
        base["active"] = {str(k): int(v) for k, v in active.items() if int(v) > 0}
    return base


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def snapshot() -> dict:
    """Current usage: ``{day, daily, hour, hourly, active{backend: n}}``."""
    with _LOCK:
        return _read()


def record_dispatch(backend_id: str) -> dict:
    """Count one dispatch to ``backend_id``."""
    with _LOCK:
        data = _read()
        data["daily"] += 1
        data["hourly"] += 1
        _write(data)
        return data


def register_active(backend_id: str) -> dict:
    with _LOCK:
        data = _read()
        data["active"][backend_id] = int(data["active"].get(backend_id, 0)) + 1
        _write(data)
        return data


def unregister_active(backend_id: str) -> dict:
    with _LOCK:
        data = _read()
        left = int(data["active"].get(backend_id, 0)) - 1
        if left > 0:
            data["active"][backend_id] = left
        else:
            data["active"].pop(backend_id, None)
        _write(data)
        return data


def reset() -> None:
    """Test helper: drop the counter file (never used in production paths)."""
    with _LOCK:
        path = _path()
        if path.exists():
            path.unlink()
