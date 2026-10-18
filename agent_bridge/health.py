"""R17-P4FIX-3 (dsh): the platform-performed health read for B1.

Why this exists: P4FIX-2 shipped a B1 freshness *check* with no production
recorder, so ``DISPATCH_HETERO=1`` refused every run (P4FIX-2 review, finding
F2). B1's original intent is "a DISPATCH must be preceded by a READ" - the
platform is the party that must perform the read, then remember it.

``health_read(backend_id)`` performs that read offline and cheaply:

1. the registry entry must exist, be ``enabled``, and pass the loader's
   fail-closed capability validation;
2. the backend must be constructible through the factory (for dsh that also
   resolves the command template, so a missing binary/env fails here);
3. if the backend exposes ``preflight()`` or ``initialize()`` (the SPI's
   optional health hooks) that result is used too.

It deliberately does NOT start a model turn or a session: that is what the
per-run deadline and the permission gate are for. The read result is recorded
through ``DispatchGate.record_freshness`` by the caller.
"""

from __future__ import annotations


def health_read(backend_id: str, *, overrides: dict | None = None) -> tuple[bool, str]:
    """Return ``(ok, detail)``. Never raises: a failure IS the read result."""
    try:
        from agent_bridge.factory import build_backend
        from agent_bridge.registry import load_registry
    except Exception as exc:  # pragma: no cover - import-time failure
        return False, f"import failed: {type(exc).__name__}: {exc}"

    try:
        data = load_registry()
    except Exception as exc:
        return False, f"registry unreadable: {type(exc).__name__}: {exc}"

    entry = (data.get("backends") or {}).get(backend_id)
    if entry is None:
        return False, f"backend {backend_id!r} is not declared in the registry"
    if not entry.get("enabled"):
        return False, f"backend {backend_id!r} is disabled in the registry"

    try:
        backend = build_backend(backend_id, overrides=overrides)
    except Exception as exc:
        return False, f"construction failed: {type(exc).__name__}: {exc}"
    if backend is None:
        return False, (f"backend {backend_id!r} is unreachable through the factory "
                       f"(gate off or backend disabled)")

    for hook in ("preflight", "initialize"):
        fn = getattr(backend, hook, None)
        if callable(fn):
            try:
                result = fn()
            except Exception as exc:
                return False, f"{hook}() failed: {type(exc).__name__}: {exc}"
            detail = f"{hook}() ok" if result is None else f"{hook}() -> {str(result)[:120]}"
            return True, detail

    caps = getattr(backend, "capabilities", None)
    n_flags = len(getattr(caps, "__dataclass_fields__", {}) or {})
    return True, (f"constructed + capabilities validated "
                  f"({n_flags} flags; no preflight hook exported)")
