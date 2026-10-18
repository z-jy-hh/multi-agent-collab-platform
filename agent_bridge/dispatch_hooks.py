"""R17-P4FIX-3 (dsh): pre-run gate that ACTUALLY runs checks.

History: P4 (no wiring) -> P4FIX (wired but no-op: ``gate = DispatchGate();
_ = gate``) -> P4FIX-2 (real checks, but no production freshness recorder, so
the switch refused every run) -> this version.

At ``DISPATCH_HETERO == 1`` this hook:

1. resolves the registry bindings and keeps the **enabled** ones (B3: the
   registry is the only routing truth);
2. checks REAL platform usage against the caps: daily/hourly dispatch counts
   and per-backend active runs, read from ``agent_bridge.gate_counters``
   (persisted, op-log reconcilable) -- not merely "is the cap non-zero";
3. performs the **B1 health read itself** for every bound backend whose
   freshness record is stale/missing (``agent_bridge.health``) and records the
   result, so the check is satisfiable by the platform rather than by an
   external recorder (P4FIX-2 finding F2);
4. takes a workspace mutex per bound backend's dispatch root (N13: one writer
   per workspace) and returns a token the caller MUST release.

Anything that fails raises ``SpiError`` and the run does not start. At
``DISPATCH_HETERO != 1`` the hook is a no-op and returns ``None``.

``DispatchGate`` is imported INSIDE the function on purpose: a module-level
import binds the original class and defeats monkeypatch-based tripwires
(that mistake shipped once already - P4FIX-2 e2e failure).
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    from agent_bridge.roots import project_root as _pr
    return _pr()


def dispatch_root_for(backend_id: str, data: dict) -> str:
    """The WORKSPACE a backend's runs share (mutex key + cwd parent). One root
    resolution for counters, cwds and locks (P4FIX-4, reviewer finding S2)."""
    from agent_bridge.roots import backend_workspace
    entry = (data.get("backends") or {}).get(backend_id) or {}
    return str(backend_workspace(backend_id, entry))


def bound_backends(data: dict) -> dict[str, str]:
    """agent_id -> backend for the pre-run gate (bindings + routing.map).

    Pure and separately testable: G12's whole point is that routing.map binds
    REAL agent ids; deleting that loop must fail a test, not just weaken a gate.
    """
    backends = data.get("backends") or {}
    bound: dict[str, str] = {}
    for agent_id, target in (data.get("bindings") or {}).items():
        backend_id = str(target).rstrip(":")
        if backend_id in backends:
            bound[agent_id] = backend_id
    for agent_id, backend_id in ((data.get("routing") or {}).get("map") or {}).items():
        target = str(backend_id).rstrip(":")
        if target in backends:
            bound[agent_id] = target
    return bound


def pre_run_gate(mode: str, *, run_id: str | None = None,
                 registry_data: dict | None = None) -> dict | None:
    """Run the pre-dispatch gate chain. Returns a release token (or None)."""
    from agent_bridge.dispatch import DispatchGate, dispatch_hetero_enabled

    if not dispatch_hetero_enabled():
        return None                      # old path: gate never consulted

    from agent_bridge import gate_counters as counters
    from agent_bridge.errors import SpiError
    from agent_bridge.health import health_read
    from agent_bridge.registry import load_registry

    data = registry_data if registry_data is not None else load_registry()
    gate = DispatchGate()
    limits = data.get("platform_limits") or {}
    backends = data.get("backends") or {}

    bound = bound_backends(data)
    enabled_backends = sorted({b for b in bound.values()
                               if (backends.get(b) or {}).get("enabled")})
    if not enabled_backends:
        raise SpiError("no bound backend is enabled in the registry "
                       "(pre-run gate)", kind="dispatch", backend="gate")

    snap = counters.snapshot()

    # Reviewer finding S6: an unreadable counter file must not silently mean
    # "zero usage" (that is a cap bypass). Fail closed unless the owner opts in.
    if snap.get("corrupt") and os.environ.get("DISPATCH_COUNTERS_ALLOW_CORRUPT") != "1":
        raise SpiError(
            "gate counter file is unreadable/corrupt - refusing to dispatch with "
            "unknown usage (set DISPATCH_COUNTERS_ALLOW_CORRUPT=1 to override)",
            kind="budget", backend="gate")

    # 1) real usage vs caps (daily/hourly totals + per-backend concurrency)
    for backend_id in enabled_backends:
        gate.check_platform_limits(
            backend_id, limits=limits, daily_count=int(snap["daily"]),
            active=int((snap.get("active") or {}).get(backend_id, 0)),
            hourly_count=int(snap["hourly"]))

    # 2) B1: the platform performs the read when the record is stale
    reads: list[dict] = []
    for backend_id in enabled_backends:
        rec = gate._check_freshness(backend_id)
        if rec.get("fresh") and rec.get("result"):
            reads.append({"backend_id": backend_id, "read": "cached"})
            continue
        ok, detail = health_read(backend_id)
        gate.record_freshness(backend_id, ok=ok, detail=detail)
        reads.append({"backend_id": backend_id, "read": "performed", "ok": ok, "detail": detail})
        if not ok:
            raise SpiError(
                f"backend {backend_id!r} failed its pre-dispatch health read: "
                f"{detail} (B1: no fresh read, dispatch refused)",
                kind="dispatch", backend=backend_id)

    # 3) workspace mutex per backend dispatch root (N13). Reviewer finding S1:
    # a failure while taking the Nth lock must NOT leak the first N-1.
    # Defensive duplicate check first: identical cwd templates would otherwise
    # surface as a confusing "already locked" (P4FIX-4 reconciliation).
    seen_ws: dict[str, str] = {}
    for backend_id in enabled_backends:
        ws = dispatch_root_for(backend_id, data)
        other = seen_ws.get(ws)
        if other is not None:
            raise SpiError(
                f"bound backends {other!r} and {backend_id!r} share the workspace "
                f"{ws!r} - each backend needs its own cwd template (N13)",
                kind="dispatch", backend=backend_id)
        seen_ws[ws] = backend_id
    locks: list[str] = []
    try:
        for backend_id in enabled_backends:
            cwd = dispatch_root_for(backend_id, data)
            gate.acquire_workspace(cwd)  # raises when already held
            locks.append(cwd)
    except Exception:
        for held in reversed(locks):
            gate.release_workspace(held)
        raise

    return {"mode": mode, "run_id": run_id, "locks": locks,
            "backends": enabled_backends, "reads": reads,
            "limits_snapshot": {"daily": int(snap["daily"]), "hourly": int(snap["hourly"])}}


def release_run_gate(token: dict | None) -> None:
    """Release the workspace mutexes taken by :func:`pre_run_gate`."""
    if not token:
        return
    from agent_bridge.dispatch import DispatchGate
    gate = DispatchGate()
    for cwd in token.get("locks") or []:
        gate.release_workspace(cwd)


def hetero_consent() -> tuple[str | None, bool]:
    """Per-run authorization inputs (env-driven; default = no consent, no
    data opt-in, which is what a paid/egressing backend requires)."""
    return os.environ.get("DISPATCH_CONSENT_TOKEN"), os.environ.get("DISPATCH_DATA_OPT_IN") == "1"
