"""R17-W5c: the backend factory - the SINGLE point where the AGENT_REGISTRY
gate decides whether the platform ever sees the registry path.

Rollback property (P2 red line): with ``AGENT_REGISTRY != 1`` this function
returns ``None`` WITHOUT importing the registry module, WITHOUT reading the
registry file, and WITHOUT constructing any backend - the caller falls back
to the pre-P2 construction path (R16 behaviour). The env check precedes every
import of ``agent_bridge.registry`` so the factory keeps working even if the
registry module itself is reverted away (P1 W4c2 lesson, applied here).

C4 counter-proof (executable, not asserted): see
``tests/unit/test_backend_factory.py`` - a tripwire on
``agent_bridge.registry._read_yaml`` (the registry's ONLY file-read point)
plus one on ``DshBackend.__init__`` prove that NEITHER fires at gate 0 while
BOTH fire at gate 1.
"""

from __future__ import annotations

import os

from agent_bridge.errors import SpiError

GATE_ENV = "AGENT_REGISTRY"
WB_GATE_ENV = "WB_ENABLED"


def registry_gate() -> bool:
    """True ONLY when ``AGENT_REGISTRY=1``. Mirrors
    ``agent_bridge.registry.registry_gate()`` (which cannot be imported here
    without defeating the lazy-import rollback property)."""
    return os.environ.get(GATE_ENV, "0") == "1"


def env_overrides() -> dict | None:
    """Parse ``DISPATCH_BACKEND_OVERRIDES`` (offline/test hook).

    W4-1: ONE parser for both the hetero pre-pass and the native runtime, so
    the two paths cannot drift. Malformed JSON / non-object is a LOUD failure.
    """
    raw = os.environ.get("DISPATCH_BACKEND_OVERRIDES")
    if not raw:
        return None
    import json
    try:
        data = json.loads(raw)
    except Exception as exc:
        raise SpiError(f"DISPATCH_BACKEND_OVERRIDES is not valid JSON: {exc}",
                       kind="dispatch", backend="gate") from exc
    if not isinstance(data, dict):
        raise SpiError("DISPATCH_BACKEND_OVERRIDES must be a JSON object",
                       kind="dispatch", backend="gate")
    return data


def wb_gate() -> bool:
    """True ONLY when BOTH ``AGENT_REGISTRY=1`` AND ``WB_ENABLED=1``.
    WB is default-off (design §7-P3: the registry entry has ``enabled:false``
    and the factory gate doubles as the code-level switch)."""
    return registry_gate() and os.environ.get(WB_GATE_ENV, "0") == "1"


# ---------------------------------------------------------------------------
# W2-1: backend registry. Adding a backend must be DATA (a registration), not a
# new branch in build_backend. ``gate`` keeps code-level switches (e.g. wb's
# double gate) intact: a gated backend returns None instead of being built.
_BACKENDS: dict[str, tuple[str, str, "Callable[[], bool] | None"]] = {}


def register_backend(backend_id: str, module: str, class_name: str,
                     *, gate=None) -> None:
    """Register a backend adapter. ``gate`` (optional) must return True for the
    backend to be constructible; it is evaluated on every build."""
    _BACKENDS[backend_id] = (module, class_name, gate)


def registered_backends() -> tuple[str, ...]:
    return tuple(sorted(_BACKENDS))


register_backend("dsh", "agent_bridge.backends.dsh", "DshBackend")
register_backend("wb", "agent_bridge.backends.wb", "WbBackend", gate=wb_gate)


def build_backend(backend_id: str, *, store=None, emit=None,
                  overrides: dict | None = None):
    """Build the named backend from the registry, or return None at gate 0.

    ``overrides`` flows to the backend constructor (node_bin / dsh_bin_js /
    dispatch_root / client_extra_args / ...). Raises SpiError(kind=dispatch)
    for an unknown backend_id (fail-closed routing, design §3.1)."""
    if not registry_gate():
        return None
    from agent_bridge.registry import load_registry
    data = load_registry()
    entry = (data.get("backends") or {}).get(backend_id)
    if entry is None:
        raise SpiError(f"backend {backend_id!r} not in registry "
                       f"(fail-closed routing)", kind="dispatch",
                       backend=backend_id)
    defaults = data.get("defaults") or {}
    registered = _BACKENDS.get(backend_id)
    if registered is None:
        raise SpiError(f"no adapter registered for backend {backend_id!r}",
                       kind="dispatch", backend=backend_id)
    module_name, class_name, gate = registered
    if gate is not None and not gate():
        return None                         # code-level switch still wins
    from importlib import import_module
    cls = getattr(import_module(module_name), class_name)
    return cls(entry, defaults=defaults, store=store, emit=emit,
               **(overrides or {}))
