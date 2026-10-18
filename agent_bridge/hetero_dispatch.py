"""R17-P4FIX-3 (dsh): real per-subtask dispatch to bound backends.

This is the piece the previous two P4 attempts only faked: P4 emitted a
``hetero_dispatch_resolved`` event and discarded the mapping, and P4FIX-2 did
the same thing again. Here every bound-and-enabled agent is actually handed a
:class:`~agent_bridge.spi.TaskSpec` through
:meth:`AgentBackend.execute_task`, and the outcome is emitted as a run event
so a real (offline) mixed run leaves auditable evidence.

Contract:

* the registry ``bindings`` are the ONLY routing truth (B3);
* a disabled backend is SKIPPED with an explicit ``hetero_dispatch_skipped``
  event -- never silently (zero-silent);
* a failing backend emits ``hetero_dispatch_error`` and is counted; the run
  continues for the other leaves unless ``strict=True``;
* every dispatch bumps ``gate_counters`` and registers/unregisters the
  backend's active count, which is what the pre-run cap check reads.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_bridge.errors import SpiError


def _cwd_for(backend_id: str, entry: dict, run_id: str) -> str:
    from agent_bridge.roots import backend_run_cwd
    return str(backend_run_cwd(backend_id, entry, run_id))


def _default_store():
    """The platform's own op-log store, opened the same way api/routes.py does
    (so a hetero leaf's ops land in the SAME ledger the platform audits)."""
    try:
        from core.paths import storage_path
        from core.storage import SQLiteOpStore
        return SQLiteOpStore(storage_path())
    except Exception:
        return None


def _env_overrides() -> dict | None:
    """Thin compatibility shell: the ONE parser now lives in ``factory``.

    Kept as a module-level name because existing callers/tests monkeypatch it
    (W4-1-2b); the behaviour (loud on malformed JSON) is unchanged.
    """
    from agent_bridge.factory import env_overrides
    return env_overrides()


def dispatch_bound_leaves(*, run_id: str, session_id: str | None, task_text: str,
                          bus, registry_data: dict | None = None, store=None,
                          overrides: dict | None = None, strict: bool = False) -> dict:
    """Dispatch ``task_text`` to every bound+enabled backend. Returns
    ``{agent_id: TaskResult}`` for the leaves that produced a result."""
    from agent_bridge import gate_counters as counters
    from agent_bridge.dispatch_hooks import hetero_consent
    from agent_bridge.factory import build_backend
    from agent_bridge.registry import load_registry
    from agent_bridge.spi import BudgetSpec, RunAuthorization, TaskSpec

    data = registry_data if registry_data is not None else load_registry()
    bindings = dict(data.get("bindings") or {})
    backends = data.get("backends") or {}
    defaults = data.get("defaults") or {}
    # G12: routing.map maps REAL agent_ids (the 9 cards) to backends; bindings
    # stay as the legacy pseudo-agent form. Additive: bindings still apply.
    for agent_id, backend_id in ((data.get("routing") or {}).get("map") or {}).items():
        bindings[agent_id] = str(backend_id).rstrip(":") + ":"
    token, opt_in = hetero_consent()

    # Reviewer finding S3: build the op store ONCE per call, not per leaf.
    resolved_store = store if store is not None else _default_store()
    # Reviewer finding S2: give the backend the SAME root the gate/counters use,
    # otherwise its single-root boundary check refuses the cwd we hand it.
    from agent_bridge.roots import dispatch_root as _dispatch_root
    base_overrides = dict(overrides if overrides is not None else (_env_overrides() or {}))
    base_overrides.setdefault("dispatch_root", str(_dispatch_root()))

    results: dict = {}
    for agent_id, target in bindings.items():
        backend_id = str(target).rstrip(":")
        entry = backends.get(backend_id)
        if entry is None or not entry.get("enabled"):
            bus.emit("hetero_dispatch_skipped", run_id,
                     {"agent_id": agent_id, "backend_id": backend_id,
                      "reason": "backend absent or disabled in registry"})
            continue

        # W0-4: a backend that declares it needs authorization is NOT dispatched
        # without a per-run consent token (fail-closed, same token as paid/egress).
        if entry.get("requires_authorization") and not token:
            bus.emit("hetero_dispatch_skipped", run_id,
                     {"agent_id": agent_id, "backend_id": backend_id,
                      "reason": "requires-authorization"})
            continue

        # W3-2: if the backend declares an ordered `modes` list, arbitrate BEFORE
        # building it. A declared-but-unsupported mode is a loud skip, never a
        # silent downgrade to another transport.
        declared_modes = entry.get("modes")
        if declared_modes:
            from agent_bridge.negotiation import NoCommonMode, negotiate, platform_supported
            try:
                chosen = negotiate(declared_modes, platform_supported())
            except NoCommonMode as exc:
                bus.emit("hetero_dispatch_skipped", run_id,
                         {"agent_id": agent_id, "backend_id": backend_id,
                          "reason": "no-common-mode",
                          "supported": sorted(["-".join(map(str, k)) for k in exc.supported]),
                          "declared": ["-".join(map(str, k)) for k in exc.declared]})
                continue
            bus.emit("mode_negotiated", run_id,
                     {"agent_id": agent_id, "backend_id": backend_id,
                      "adapter": chosen.adapter, "transport": chosen.transport,
                      "protocol_version": chosen.protocol_version})

        cwd = _cwd_for(backend_id, entry, run_id)
        Path(cwd).mkdir(parents=True, exist_ok=True)
        spec = TaskSpec(run_id=run_id, platform_session_id=session_id or f"hetero-{run_id[:8]}",
                        task_text=task_text, cwd=cwd)
        budget = BudgetSpec(tool_calls=defaults.get("budget_tool_calls"),
                            wall_s=defaults.get("deadline_prompt_s"), turns=1)
        authz = RunAuthorization(consent_token=token, data_opt_in=opt_in,
                                 permission_mode="workspace-write")
        bus.emit("hetero_dispatch_started", run_id,
                 {"agent_id": agent_id, "backend_id": backend_id, "cwd": cwd})
        counters.register_active(backend_id)
        backend = None
        try:
            # M2: wire the bus into the backend so permission/session events reach
            # the trace+SSE instead of being dropped by the no-op fallback.
            _emit = (lambda ev_type, payload, _aid=agent_id, _bid=backend_id:
                     bus.emit(ev_type, run_id,
                              dict(payload or {}, agent_id=_aid, backend_id=_bid)))
            backend = build_backend(backend_id, store=resolved_store,
                                    emit=_emit,
                                    overrides=base_overrides)
            if backend is None:
                raise SpiError(f"backend {backend_id!r} unreachable (factory gate)",
                               kind="dispatch", backend=backend_id,
                               code="backend.unreachable")
            counters.record_dispatch(backend_id)
            result = backend.execute_task(spec, budget=budget, authz=authz)
            results[agent_id] = result
            bus.emit("hetero_dispatch_result", run_id, {
                "agent_id": agent_id, "backend_id": backend_id,
                "stop_reason": getattr(result, "stop_reason", None),
                "final_text": (getattr(result, "final_text", "") or "")[:400],
                "audit_refs": getattr(result, "audit_refs", {}) or {},
                "error": getattr(result, "error", None)})
        except Exception as exc:
            import traceback
            # Reviewer finding S8: keep a traceback tail so reconciliation can
            # locate the root cause, not just the message.
            tail = traceback.format_exc().strip().splitlines()[-3:]
            bus.emit("hetero_dispatch_error", run_id,
                     {"agent_id": agent_id, "backend_id": backend_id,
                      "error": f"{type(exc).__name__}: {exc}",
                      # F11: keep the machine-readable code reachable
                      "code": getattr(exc, "code", None),
                      "traceback_tail": tail})
            if strict:
                raise
        finally:
            # W4-1-2c: a leaf's backend owns an AcpClient -> node process; it
            # must not outlive the dispatch (the old code never shut it down).
            if backend is not None:
                shutdown = getattr(backend, "shutdown", None)
                if callable(shutdown):
                    try:
                        shutdown()
                    except Exception as exc:
                        bus.emit("hetero_dispatch_error", run_id,
                                 {"agent_id": agent_id, "backend_id": backend_id,
                                  "error": f"shutdown failed: {type(exc).__name__}: {exc}",
                                  "code": "backend.shutdown_failed"})
            counters.unregister_active(backend_id)
    return results
