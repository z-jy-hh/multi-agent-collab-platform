"""HTTP routes. Mode list is backend-driven (C14); SSE per run (C12); every
long run is cancellable (cooperative, C18); HTTP probes all run on the Python
stack server-side (R26 applies to our own checks, not to clients)."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from api.deps import STATE
from api.events import event_stream
from core import ids
from core.errors import CancelledRun
from core.paths import PROJECT_ROOT, run_dir, storage_path as default_storage_path

app = FastAPI(title="multi_agent_collab", version="1.0")
# S1: serve the split UI assets (tokens/components). This is a MOUNT, not an
# @app route, so the endpoint authority ledger (20 routes) is unchanged.
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "api" / "static")),
          name="static")
bus = STATE.bus


def _write_run_meta(rd, meta: dict) -> None:
    """N2: run_meta.json goes through the same pre-persistence redaction as
    every other artifact, embeds the rules version, and carries the trust
    channel fields (source_trust / derived_from) for the library side."""
    from core.redact import (RULES_VERSION, record_run_redaction,
                             redact_strings_deep, redaction_enabled)
    meta = {"derived_from": None, **meta, "redaction_rules": RULES_VERSION}
    counts: dict = {}
    if redaction_enabled():
        meta, counts = redact_strings_deep(meta)
        if counts:
            record_run_redaction(rd, counts)
    (Path(rd) / "run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")


class RunRequest(BaseModel):
    mode: str
    task: str
    session_id: str | None = None
    llm: str | None = None  # "fake" forces the offline scripted LLM (tests/demo)
    source_trust: str = "human"  # C1: closed set {human, platform, external-agent, untrusted}
    profiles: dict | None = None  # P0: optional per-agent composition (additive)


@app.get("/api/modes")
def list_modes():
    # C14: rendered from the registry (configs/topologies/*.yaml), never hardcoded
    # P1-2a (logic canvas): additive key "topologies" — skeleton nodes/edges
    # parsed from the SAME YAML the runner uses, so the canvas graph can never
    # drift from the real topology (C1). Edges are NEVER derived from events.
    topologies: dict = {}
    for name in STATE.registry.topology_names():
        cfg = STATE.registry.get_topology(name) or {}
        topologies[name] = {
            "nodes": [{"id": n.get("id"), "kind": n.get("kind"),
                       "agent": n.get("agent")} for n in cfg.get("nodes", [])],
            "edges": [{"from": e.get("from"), "to": e.get("to"),
                       "conditional": bool(e.get("conditional")),
                       "rule": e.get("rule")} for e in cfg.get("edges", [])],
        }
    # W0-2: project the protocol version the platform can speak, from the
    # backend registry (never hardcoded). Additive key; route count unchanged.
    protocol_versions: list[int] = []
    protocol_error = None
    try:
        from agent_bridge.registry import load_registry as _load_backends
        _entries = (_load_backends() or {}).get("backends") or {}
        # F5: only ENABLED backends define what the platform can actually speak;
        # a disabled future backend must not widen the advertised set.
        protocol_versions = sorted({
            e["protocol_version"] for e in _entries.values()
            if e.get("enabled") and isinstance(e.get("protocol_version"), int)})
    except Exception as exc:      # F3: never pretend "no version declared"
        protocol_error = type(exc).__name__
    # P0 (identity-model plan, approved additive widening): expose the profile
    # catalog and skills read-only. No new route; failures are explicit.
    agents: list[dict] = []
    profile_rows: list[dict] = []
    skills: list[dict] = []
    rule_rows: list[dict] = []
    profile_error = None
    try:
        from graphs.profiles import (
            load_presets, load_rule_checks, match_check, parse_user_profile,
            skills_catalog,
        )
        presets = load_presets()
        for p in presets.values():
            agents.append({"id": p.id, "name": p.name,
                           "version": p.version, "status": p.status})
            profile_rows.append({
                "id": p.id, "version": p.version, "status": p.status,
                "name": p.name, "capabilities": p.capabilities,
                "output_schema": p.output_schema, "tools": list(p.tools),
                "cost": p.cost,
            })
        skills = skills_catalog()
        entries, _version = parse_user_profile()
        checks = load_rule_checks()
        for r in entries:
            rc = match_check(r, checks)
            rule_rows.append({
                "id": r.id, "title": r.title, "text": r.text,
                "tags": list(r.tags), "text_sha256": r.text_sha256,
                "enforcement": rc.enforcement if rc else "soft",
                "check": ({"type": rc.check.type, "value": rc.check.value}
                          if rc else None),
            })
    except Exception as exc:      # loud in the payload, never a silent empty set
        profile_error = f"{type(exc).__name__}: {exc}"
    return {"modes": STATE.registry.topology_names(),
            "protocol_version": protocol_versions[0] if len(protocol_versions) == 1 else None,
            "protocol_versions": protocol_versions,
            "protocol_error": protocol_error,
            "topologies": topologies,
            "agents": agents,
            "profiles": profile_rows,
            "skills": skills,
            "rules": rule_rows,
            "profile_error": profile_error}


@app.post("/api/runs")
def create_run(req: RunRequest):
    # C1: source_trust closed set validation (C2 low-trust guard)
    _TRUST_LEVELS = {"human", "platform", "external-agent", "untrusted"}
    if req.source_trust not in _TRUST_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=f"source_trust {req.source_trust!r} not in {sorted(_TRUST_LEVELS)}")
    # C2: external-agent/untrusted require a consent token
    if req.source_trust in ("external-agent", "untrusted"):
        if not os.environ.get("DISPATCH_CONSENT_TOKEN"):
            raise HTTPException(
                status_code=422,
                detail=f"source_trust {req.source_trust!r} requires a consent token "
                       f"(set DISPATCH_CONSENT_TOKEN) — dispatch refused (C2 low-trust guard)")
    if req.mode not in STATE.registry.topology_names():
        raise HTTPException(status_code=404, detail=f"unknown mode: {req.mode}")
    # R17-P4F1 / R17-P4FIX-3: dispatch gate consultation (thin adapter, design
    # §7-P4). At DISPATCH_HETERO != 1 pre_run_gate returns None (the old path
    # runs untouched). At 1 it ACTUALLY runs the gate chain - real usage caps
    # from gate_counters, a platform-performed B1 health read, and the N13
    # workspace mutex - and returns a token whose locks MUST be released when
    # the run ends. Any failure propagates as SpiError (the run does NOT start).
    from agent_bridge.dispatch_hooks import pre_run_gate, release_run_gate
    from graphs.runner import run_topology

    run_id = ids.new_run_id()
    session_id = req.session_id or f"web-{run_id[:8]}"
    gate_token = pre_run_gate(req.mode, run_id=run_id)
    # R17-BCFIX2 (B-5/C4): persist observable trust fields where the unified
    # view and audits read them (runs/ is platform-owned; core/ untouched).
    # Written after the gate so a rejected run leaves no orphan artifacts.
    _rd = run_dir(run_id)
    _rd.mkdir(parents=True, exist_ok=True)
    _write_run_meta(_rd, {"run_id": run_id, "source_trust": req.source_trust,
                          "independent_review": None})
    cancel = STATE.register_cancel(run_id)

    def _work() -> None:
        try:
            llm = None
            if req.llm == "fake":
                from core.llm import FakeLLM
                from graphs.runner import default_llm_script

                llm = FakeLLM(script=default_llm_script)
            run_topology(req.mode, req.task, run_id=run_id, session_id=session_id,
                         bus=bus, persistent=True, cancel_event=cancel, llm=llm,
                         params={"profiles": req.profiles} if req.profiles else None)
        except CancelledRun:  # runner already recorded the cancelled state
            pass
        except Exception as exc:
            bus.emit("run_error", run_id,
                     {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            release_run_gate(gate_token)

    STATE.executor.submit(_work)
    return {"run_id": run_id, "session_id": session_id, "mode": req.mode}


@app.get("/api/runs/{run_id}/events")
def stream_events(run_id: str, request: Request):
    return EventSourceResponse(event_stream(run_id, request, bus), ping=15)


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    ev = STATE.cancels.get(run_id)
    if ev is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    ev.set()  # cooperative: takes effect between nodes (C12/C18)
    return {"run_id": run_id, "cancelling": True}


# --- R17-W9b: unified run view (N8: three-source aggregation) ----------------

@app.get("/api/runs/{run_id}/unified")
def get_unified_view(run_id: str):
    """Aggregate the same run_id across THREE sources. A missing source is
    explicitly labelled "MISSING" (never silently empty).

    Uses ``DSH_HOME`` and ``WB_AUDIT_DIR`` env vars for source paths so tests
    can redirect them (no monkeypatching of local imports needed)."""
    import json as _json

    rd = run_dir(run_id)
    view: dict = {"run_id": run_id, "sources": {}}

    # source 1: platform (trace + health + state)
    platform = {}
    for fname in ("trace.jsonl", "health.json", "state.json"):
        fp = rd / fname
        if fp.exists():
            try:
                platform[fname] = _json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                platform[fname] = "PARSE_ERROR"
        else:
            platform[fname] = "MISSING"
    view["sources"]["platform"] = platform

    # source 4 (P1-2a): host annotations (append-only annotations.json) —
    # additive key, same MISSING/PARSE_ERROR convention as the other sources.
    ann_fp = rd / "annotations.json"
    if ann_fp.exists():
        try:
            view["sources"]["annotations"] = _json.loads(
                ann_fp.read_text(encoding="utf-8"))
        except ValueError:
            view["sources"]["annotations"] = "PARSE_ERROR"
    else:
        view["sources"]["annotations"] = "MISSING"

    # source 2: dsh session logs (F5: full run_id match, not prefix[:8])
    dsh_home = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
    dsh_sess_dir = os.path.join(dsh_home, "sessions")
    dsh_status: object = "MISSING"
    if os.path.isdir(dsh_sess_dir):
        matches = [d for d in os.listdir(dsh_sess_dir) if run_id in d]
        if matches:
            dsh_status = {"session_dirs": matches,
                          "path": os.path.join(dsh_sess_dir, matches[0])}
    view["sources"]["dsh_session"] = dsh_status

    # source 3: backend audit (F4 fix: Path type + visible errors)
    wb_audit_dir = os.environ.get("WB_AUDIT_DIR",
                                  os.path.expanduser("~/.workbuddy/audit-log"))
    wb_audit: object = "MISSING"
    read_warnings: list[dict] = []
    if os.path.isdir(wb_audit_dir):
        from pathlib import Path as _Path
        run_entries = []
        for fname in os.listdir(wb_audit_dir):
            fp = _Path(wb_audit_dir) / fname
            if not fname.endswith(".jsonl"):
                continue
            try:
                content = fp.read_text(encoding="utf-8")
                if run_id in content:
                    run_entries.append(fname)
            except UnicodeDecodeError as exc:
                read_warnings.append({"file": fname,
                                      "error": f"not UTF-8: {exc}"})
            except OSError as exc:
                read_warnings.append({"file": fname,
                                      "error": f"read failed: {exc}"})
        if run_entries:
            wb_audit = {"matching_files": run_entries}
    view["sources"]["backend_audit"] = wb_audit
    if read_warnings:
        view["read_warnings"] = read_warnings

    # missing-source summary (explicit, never silent)
    view["missing"] = [k for k, v in view["sources"].items() if v == "MISSING"]

    # R17-BCFIX2 (B-3): liveness block — derived from EXISTING artifacts by
    # the same read-only machinery the bounded recovery uses (no shadow
    # state, nothing caller-supplied).
    from agent_bridge.recovery import derive_liveness
    view["liveness"] = derive_liveness(run_id)

    # R17-BCFIX2 (B-5/C4): observable trust/review fields. Defaults are
    # explicit ("unspecified" / None), never a silent absence.
    meta: dict = {}
    meta_fp = rd / "run_meta.json"
    if meta_fp.exists():
        try:
            meta = _json.loads(meta_fp.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    view["source_trust"] = meta.get("source_trust", "unspecified")
    view["independent_review"] = meta.get("independent_review")
    return view


# --- R17-BCFIX2 (B-2.iv): bounded recovery production entry -------------------

@app.post("/api/runs/{run_id}/recover")
def recover_run(run_id: str):
    """Production caller of the bounded recovery (review B-2.iv).

    Operator-triggered, same trust level as cancel: derives liveness from
    existing artifacts and advances the bounded state machine
    (none -> queued -> action_open -> escalated). Never cancels the run;
    repeated calls on an unchanged situation are no-ops (fingerprint match
    + max_attempts cap)."""
    if not run_dir(run_id).exists():
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    from agent_bridge.recovery import assess_and_recover
    rec = assess_and_recover(run_id)
    return {"run_id": run_id, "recovery": rec}


@app.post("/api/runs/{run_id}/review")
def record_review(run_id: str, body: dict = None):
    """C4/B-5: record an independent-review verdict for a run. Persisted to
    run_meta.json so the unified view can surface it (observable, auditable)."""
    body = body or {}
    reviewer = str(body.get("reviewer", ""))
    verdict = str(body.get("verdict", ""))
    if not reviewer or verdict not in ("pass", "fail"):
        raise HTTPException(status_code=422,
                            detail="body must include reviewer (non-empty) "
                                   "and verdict in {'pass','fail'}")
    rd = run_dir(run_id)
    if not rd.exists():
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    meta_fp = rd / "run_meta.json"
    try:
        meta = json.loads(meta_fp.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    meta["independent_review"] = {"reviewer": reviewer, "verdict": verdict}
    _write_run_meta(rd, meta)
    return {"run_id": run_id, "independent_review": meta["independent_review"]}


# --- R17-W9c: permission card API ---------------------------------------------

@app.post("/api/runs/{run_id}/permission")
def answer_permission(run_id: str, body: dict = None):
    """R17-P0a: permission card endpoint — delivers a REAL answer through
    the session handle's answer_permission face (not a stub).

    body: {"tool_call_id": str, "option_id": str}
    - the option_id must be one the SERVER offered in the original request
    - the answer is delivered via the session registry (run_id -> handle)
    - if no session or no pending request, returns 404
    - if the option_id is not offered, returns 422 (fail-closed)
    """
    from fastapi import HTTPException as _HE
    if body is None:
        raise _HE(status_code=422, detail="request body required")
    option_id = body.get("option_id")
    tool_call_id = body.get("tool_call_id")
    if not option_id:
        raise _HE(status_code=422, detail="option_id is required")

    from agent_bridge.session_registry import get
    handle = get(run_id)
    if handle is None:
        raise _HE(status_code=404,
                  detail=f"no active session handle for run {run_id!r}")

    pending = getattr(handle, "_pending", None)
    if not pending:
        raise _HE(status_code=404,
                  detail=f"no active session handle state for run {run_id!r}")
    if pending.get("answered_at") is not None:
        raise _HE(status_code=409,
                  detail=f"permission request for run {run_id!r} was already "
                         f"answered at {pending['answered_at']:.0f} "
                         f"(option_id={pending.get('decision', '') and getattr(pending.get('decision'), 'option_id', '?')}) "
                         f"— duplicate answers are rejected (N1)")
    if pending.get("request") is None:
        raise _HE(status_code=404,
                  detail=f"no pending permission request for run {run_id!r}")

    request = pending["request"]
    if tool_call_id and request.tool_call_id != tool_call_id:
        raise _HE(status_code=422,
                  detail=f"tool_call_id mismatch: expected {request.tool_call_id!r}, "
                         f"got {tool_call_id!r}")
    if option_id not in request.options:
        raise _HE(status_code=422,
                  detail=f"option_id {option_id!r} was not offered by the server "
                         f"(offered: {list(request.options)}) — "
                         f"fail-closed: invented optionIds are rejected")

    from agent_bridge.spi import PermissionDecision
    answered_by = body.get("answered_by", "unspecified")
    # C2: look up the run's source_trust from state.json (default: human)
    run_state_path = os.path.join(str(run_dir(run_id)), "state.json")
    req_source_trust = "human"
    if os.path.exists(run_state_path):
        try:
            state = json.loads(Path(run_state_path).read_text(encoding="utf-8"))
            req_source_trust = state.get("source_trust", "human")
        except Exception:
            pass
    # C2: external-agent/untrusted runs forbid auto answers
    if req_source_trust in ("external-agent", "untrusted") and answered_by == "auto":
        raise _HE(status_code=422,
                  detail=f"source_trust {req_source_trust!r} forbids auto answers "
                         f"(C2 low-trust guard) — answered_by must be 'human'")
    decision = PermissionDecision(
        option_id=option_id, reason="dispatch console permission card",
        basis=answered_by)
    # F7: the pending slot can be cleared by a timeout between the read above
    # and this call; answer_permission re-checks and raises SpiError. Map that
    # to 409 instead of letting it escape as an uncaught 500.
    try:
        handle.answer_permission(request, decision)
    except Exception as exc:            # SpiError: "no matching ... pending"
        raise _HE(status_code=409,
                  detail=f"permission request can no longer be answered: {exc}")
    return {"run_id": run_id, "status": "answered",
            "option_id": option_id, "tool_call_id": request.tool_call_id}


@app.get("/api/evolve/versions")
def evolve_versions():
    """Read-only evolution history (rectification A2/N2): version records in
    ascending order, each carrying the candidate diff. NEVER triggers an
    evolution run and NEVER writes (K5)."""
    import json as _json

    from core.paths import prompt_versions_dir, version_dirs

    base = prompt_versions_dir()
    records: list[dict] = []
    for vdir in version_dirs(base):
        record_path = vdir / "record.json"
        if not record_path.exists():
            continue
        rec = _json.loads(record_path.read_text(encoding="utf-8"))
        records.append({
            "version": rec.get("version"),
            "reason": rec.get("reason"),
            "decision": rec.get("decision"),
            "score_delta": rec.get("score_delta"),
            "created_at": rec.get("created_at"),
            "files": rec.get("files", {}),
            "diff": rec.get("diff"),
            "diff_unavailable_reason": rec.get("diff_unavailable_reason"),
        })
    records.sort(key=lambda r: (r["version"] is None, r["version"] or 0))
    return records


@app.get("/api/runs")
def list_runs():
    conn = sqlite3.connect(str(default_storage_path()))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT run_id, mode, session_id, task, status, created_at"
            " FROM runs ORDER BY created_at DESC LIMIT 50").fetchall()
        return {"runs": [dict(r) for r in rows]}
    except sqlite3.Error:
        return {"runs": []}
    finally:
        conn.close()


@app.get("/api/runs/{run_id}/state")
def get_state(run_id: str):
    path = run_dir(run_id) / "state.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="run not finished yet")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/runs/{run_id}/health")
def get_health(run_id: str):
    from inspector.report import load_health

    health = load_health(run_id)
    if health is None:
        raise HTTPException(status_code=404, detail="health not ready")
    return health


@app.get("/api/doctor")
def run_doctor_endpoint():
    """M1/U3: read-only system doctor — same nine checks as `main.py doctor`.
    The only artifacts it ever leaves are the gitignored runs/doctor-smoke-*
    smoke outputs the doctor itself produces; no config, prompt, report or
    live file is written (K5)."""
    from inspector.runner import run_doctor

    return run_doctor().model_dump()


@app.get("/api/runs/{run_id}/trace")
def get_trace(run_id: str):
    """R15-M4 (owner-directed UI redesign): read-only replay of this run's
    persisted trace.jsonl, so a FINISHED run can rebuild its agent tree and
    per-agent output view without the in-memory bus (which forgets on
    restart). Never modified: read-only parse, torn last line tolerated;
    response is capped at the last 5000 events with the true total."""
    path = run_dir(run_id) / "trace.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="trace not found")
    events: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue  # a torn tail line must not kill the endpoint
    total = len(events)
    return {"run_id": run_id, "total": total,
            "events": events[-5000:], "truncated": total > 5000}


# --- P1-2a: host annotations (V1.1 §C1-A': the ONLY write endpoint) ----------

@app.post("/api/runs/{run_id}/annotations")
def add_annotation(run_id: str, body: dict = None):
    """Append one host annotation to runs/<run_id>/annotations.json (array,
    append-only, {ts, author, text, anchor}). Never touches run state, trace,
    health or the archive snapshot (H1 A7 idempotence anchor preserved).
    run_id must be a single safe path segment; the run dir must exist.

    N2 (DSH review finding F1): host text is free text, so it goes through the
    SAME pre-persistence redaction as run_meta.json -- mask first, record the
    rule version and the per-kind counts. Without this the file would be the
    only persisted free-text channel bypassing the pre-write masking invariant."""
    body = body or {}
    if (not run_id or "/" in run_id or "\\" in run_id or ".." in run_id
            or run_id in (".", "..")):
        raise HTTPException(status_code=422, detail="invalid run_id")
    rd = run_dir(run_id)
    if not rd.is_dir():
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=422, detail="annotation text required")
    if len(text) > 2000:
        raise HTTPException(status_code=422,
                            detail="annotation too long (max 2000 chars)")
    from core.redact import (RULES_VERSION, record_run_redaction,
                             redact_strings_deep, redaction_enabled)
    # P1-HOSTPICK: entry must NOT carry its own "ts" — _append_json_array
    # owns the ts (prepending {"ts": ..., **entry}; a leftover ts in entry
    # would silently overwrite the helper's).
    entry = {"author": str(body.get("author", "host"))[:64],
             "text": text,
             "anchor": str(body.get("anchor") or "")[:200],
             "redaction_rules": RULES_VERSION}
    counts: dict = {}
    if redaction_enabled():
        entry, counts = redact_strings_deep(entry)
        if counts:
            record_run_redaction(rd, counts)
    path = rd / "annotations.json"
    if path.exists():
        # P1-FIX F5: per-run entry-count cap (single-entry 2000-char cap
        # already existed). 500th entry still accepted, 501st refused.
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            current = []
        if isinstance(current, list) and len(current) >= 500:
            raise HTTPException(status_code=422,
                                detail="annotation limit reached (max 500)")
    items = _append_json_array(path, entry)
    return {"run_id": run_id, "annotations_count": len(items),
            "appended": items[-1]}


def _append_json_array(path, entry) -> list:
    """P1-HOSTPICK (B6): the ONE atomic append primitive shared by the two
    constrained write endpoints (annotations / allocation). Deliberately
    DUMB: no masking here (each endpoint masks its own entry), just
    tmp+os.replace atomic append of {ts, ...entry} to a JSON array. Returns
    the resulting item list."""
    import time as _time

    full = {"ts": round(_time.time(), 3), **entry}
    items: list = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                items = loaded
        except ValueError:
            if path.name == "allocation_decision.json":
                # B6: a torn decision file must be LOUD — a corrupted record
                # may hold a HUMAN decision; silently resetting would drop it
                raise HTTPException(
                    status_code=409,
                    detail="allocation_decision.json is corrupt; refusing to "
                           "append (human decision must not be dropped)")
            items = []  # annotations: start a fresh array, never crash
    items.append(full)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)
    return items


# --- P1-HOSTPICK: the ONLY second constrained write endpoint -----------------

@app.post("/api/runs/{run_id}/allocation")
def add_allocation_decision(run_id: str, body: dict = None):
    """Record ONE host allocation decision for a pending
    allocation_choice_requested. Appends to
    runs/<run_id>/allocation_decision.json via the shared atomic-append
    helper. Never touches run state, trace, health or snapshots.
    409: no pending request for the subtask / already answered / corrupt
    decision file. 422: invalid run_id, missing fields, answer outside the
    request's candidate pool or in `taken` (hard gates were applied when
    the candidate list was built — the pool cannot bypass them)."""
    body = body or {}
    if (not run_id or "/" in run_id or "\\" in run_id or ".." in run_id
            or run_id in (".", "..")):
        raise HTTPException(status_code=422, detail="invalid run_id")
    rd = run_dir(run_id)
    if not rd.is_dir():
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    sid = str(body.get("subtask_id", "")).strip()
    agent_id = str(body.get("agent_id", "")).strip()
    if not sid or not agent_id:
        raise HTTPException(status_code=422,
                            detail="subtask_id and agent_id required")
    dec_path = rd / "allocation_decision.json"
    answered: set = set()
    if dec_path.exists():
        try:
            loaded = json.loads(dec_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                answered = {e.get("subtask_id") for e in loaded
                            if isinstance(e, dict)}
        except ValueError:
            raise HTTPException(
                status_code=409,
                detail="allocation_decision.json is corrupt; refusing to "
                       "append (human decision must not be dropped)")
    # pending = requested(sid) not closed by answer/conflict/fallback/terminal
    trace = rd / "trace.jsonl"
    requested: dict = {}
    closed: set = set()
    terminal_after_last_request = False
    if trace.exists():
        try:
            lines = trace.read_text(encoding="utf-8").splitlines()
        except (OSError, ValueError):  # ValueError: UnicodeDecodeError (B7)
            lines = []
        for line in lines:
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t = str(ev.get("type", ""))
            p = ev.get("payload") or {}
            if t == "allocation_choice_requested":
                requested[str(p.get("subtask_id"))] = p
            elif t == "allocation_answer":
                closed.add(str(p.get("subtask_id")))
            elif t == "allocation_conflict":
                for pair in p.get("pairs") or []:
                    if isinstance(pair, dict) and pair.get("subtask_id"):
                        closed.add(str(pair["subtask_id"]))
            elif t == "allocation_fallback":
                for fsid in p.get("subtask_ids") or []:
                    closed.add(str(fsid))
            elif t in ("run_finished", "run_error", "run_cancelled"):
                if requested:
                    terminal_after_last_request = True
    pending = {sid_: pl for sid_, pl in requested.items()
               if sid_ not in closed and sid_ not in answered}
    if sid not in pending or terminal_after_last_request:
        raise HTTPException(status_code=409,
                            detail=f"no pending allocation request for "
                                   f"subtask {sid!r}")
    pl = pending[sid]
    cands = {c.get("agent_id") for c in pl.get("candidates", [])}
    if agent_id not in cands:
        raise HTTPException(status_code=422,
                            detail=f"agent {agent_id!r} not in the candidate "
                                   f"pool for subtask {sid!r}")
    if agent_id in (pl.get("taken") or []):
        raise HTTPException(status_code=422,
                            detail=f"agent {agent_id!r} is already taken "
                                   f"(pinned elsewhere)")
    items = _append_json_array(
        dec_path, {"subtask_id": sid, "agent_id": agent_id,
                   "chosen_by": "host",
                   "author": str(body.get("author", "host"))[:64]})
    return {"run_id": run_id, "decision_count": len(items),
            "appended": items[-1]}


@app.post("/api/plan/draft")
def plan_draft(body: dict = None):
    """P2-1 (canvas -> task-book draft): the ONE computation-type endpoint.
    PURE FUNCTION: writes nothing (no run dir, no run row, no trace, no
    decision/annotation files), starts no run, and never submits anything —
    the host submits a draft manually via the existing POST /api/runs.

    Minimal semantic mapping (workorder P2 + Appendix C K1):
      frame(node) = subtask (label = description), arrow(edge) = dependency
      (A -> B: B depends on A), section = stage/swimlane (grouping only).
    Node/edge/section ids are generated by the FRONTEND and only VALIDATED
    here (missing/duplicate id, edge referencing an unknown node => 422;
    the backend never fabricates ids — that would drift between POSTs and
    break R1 determinism).
    Anything outside the set is reported EXPLICITLY (fail-closed: a non-empty
    report means NO draft is produced, never a silent ignore):
      - unknown fields  -> unsupported[{id, field, reason}]  (sorted by id)
      - unknown section -> unsupported[{id, field, reason}]
      - dependency cycle-> cycles[{cycle: [ids...], reason}] (K1: a node with
        no arrows is LEGAL — zero dependencies; only cycles are invalid).
    """
    body = body or {}
    allowed_top = {"sections", "nodes", "edges"}
    unsupported: list[dict] = []
    cycles: list[dict] = []

    def _unsupported(eid, field, reason):
        unsupported.append({"id": str(eid), "field": str(field),
                            "reason": reason})

    # --- structural validation (explicit 422, never silent) ---------------
    sections = body.get("sections") or []
    nodes = body.get("nodes") or []
    edges = body.get("edges") or []
    for key, coll in (("top-level", body),):
        for k in coll:
            if k not in allowed_top:
                _unsupported(f"<body>.{k}", k, "unknown top-level field")
    spec = (("sections", sections, {"id", "label"}),
            ("nodes", nodes, {"id", "label", "section"}),
            ("edges", edges, {"id", "from", "to"}))
    for name, coll, allowed in spec:
        if not isinstance(coll, list):
            raise HTTPException(status_code=422,
                                detail=f"{name} must be a list")
        seen_ids: set = set()
        for item in coll:
            if not isinstance(item, dict):
                raise HTTPException(status_code=422,
                                    detail=f"{name} items must be objects")
            iid = item.get("id")
            if not isinstance(iid, str) or not iid.strip():
                raise HTTPException(status_code=422,
                                    detail=f"{name} item missing id "
                                           f"(frontend must generate ids)")
            if iid in seen_ids:
                raise HTTPException(status_code=422,
                                    detail=f"{name}: duplicate id {iid!r} "
                                           f"(frontend must ensure unique ids)")
            seen_ids.add(iid)
            for k in item:
                if k not in allowed:
                    _unsupported(iid, k, f"unknown field on {name}")
    node_ids = {n["id"] for n in nodes}
    section_ids = {s["id"] for s in sections}
    edge_ids = set()
    for e in edges:
        eid = e["id"]
        if eid in edge_ids:
            raise HTTPException(status_code=422,
                                detail=f"edges: duplicate id {eid!r}")
        edge_ids.add(eid)
        for endpoint_key in ("from", "to"):
            ref = e.get(endpoint_key)
            if not isinstance(ref, str) or ref not in node_ids:
                raise HTTPException(
                    status_code=422,
                    detail=f"edge {eid!r}: {endpoint_key} must reference "
                           f"an existing node id (got {ref!r})")
    for n in nodes:
        sec = n.get("section")
        if sec is not None and sec not in section_ids:
            _unsupported(n["id"], "section",
                         f"references unknown section {sec!r}")

    # --- dependency cycle detection (K1: the ONLY illegal graph shape) ----
    adj: dict[str, list[str]] = {}
    for e in edges:                       # from -> to: 'to' depends on 'from'
        adj.setdefault(e["from"], []).append(e["to"])
    WHITE, GREY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}
    stack: list[str] = []

    def dfs(u):
        color[u] = GREY
        stack.append(u)
        for v in sorted(adj.get(u, [])):
            if color[v] == GREY:
                i = stack.index(v)
                cycles.append({"cycle": sorted(stack[i:]),
                               "reason": "dependency cycle"})
            elif color[v] == WHITE:
                dfs(v)
        stack.pop()
        color[u] = BLACK

    for nid in sorted(node_ids):
        if color[nid] == WHITE:
            dfs(nid)

    if unsupported or cycles:
        unsupported.sort(key=lambda x: (x["id"], x["field"], x["reason"]))
        cycles.sort(key=lambda c: c["cycle"])
        raise HTTPException(
            status_code=422,
            detail={"unsupported": unsupported, "cycles": cycles,
                    "note": "fail-closed: no draft is produced while any "
                            "element is outside the supported set"})

    # --- deterministic draft (R1: no randomness, no timestamps) -----------
    sec_order = {s["id"]: i for i, s in enumerate(sections)}
    ordered = sorted(
        nodes,
        key=lambda n: (sec_order.get(n.get("section"), len(sections)),
                       n["id"]))
    dep_lines = sorted(f"- {e['to']} 依赖 {e['from']}" for e in edges)
    lines = ["# 任务书草稿（画布导出 · 未自动提交）",
             "",
             "由宿主确认后用既有 POST /api/runs 手工提交。",
             "",
             f"## 子任务（{len(ordered)}）"]
    for i, n in enumerate(ordered, 1):
        sec = n.get("section")
        tag = f" [{sec}]" if sec else ""
        label = str(n.get("label", "")).strip() or n["id"]
        lines.append(f"{i}. {n['id']}{tag}: {label}")
    lines.append("")
    lines.append(f"## 依赖（{len(dep_lines)}）")
    lines.extend(dep_lines if dep_lines else ["（无 — 各子任务无前置依赖）"])
    import hashlib
    draft = "\n".join(lines)
    return {"draft": draft,
            "draft_sha256_16": hashlib.sha256(
                draft.encode("utf-8")).hexdigest()[:16],
            "subtasks": len(ordered),
            "dependencies": len(dep_lines),
            "unsupported": [],
            "cycles": []}


@app.get("/api/runs/{run_id}/artifacts")
def get_artifacts(run_id: str):
    """R15-M4 (owner-directed UI redesign): read-only blackboard artifacts of
    the session this run belongs to — the agents' published outputs ("产出").
    Read-only: opens the store read-only via its own connection, writes
    nothing. Long values are capped per entry at 100 000 chars with an
    explicit truncated flag (never silently clipped)."""
    conn = sqlite3.connect(f"file:{default_storage_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT session_id FROM runs WHERE run_id=?",
                           (run_id,)).fetchone()
    except sqlite3.Error:
        row = None  # store absent/unreadable → same contract as unknown run
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    session_id = row["session_id"]
    from core.storage import SQLiteOpStore

    store = SQLiteOpStore(default_storage_path())
    artifacts = []
    for op in store.ops_for_session(session_id):
        text = op.value if isinstance(op.value, str) else json.dumps(
            op.value, ensure_ascii=False, default=str)
        artifacts.append({
            "op_id": op.op_id, "agent_id": op.agent_id, "key": op.key,
            "ts": op.ts, "scope": op.scope,
            "value": text[:100_000], "value_chars": len(text),
            "truncated": len(text) > 100_000,
        })
    artifacts.sort(key=lambda a: a["ts"])
    return {"run_id": run_id, "session_id": session_id,
            "artifacts": artifacts}


@app.get("/api/sessions")
def list_sessions():
    conn = sqlite3.connect(str(default_storage_path()))
    conn.row_factory = sqlite3.Row
    try:
        sessions = conn.execute(
            "SELECT s.session_id, s.created_at,"
            " (SELECT COUNT(*) FROM ops o WHERE o.session_id = s.session_id) AS op_count"
            " FROM sessions s ORDER BY s.created_at DESC LIMIT 50").fetchall()
        return {"sessions": [dict(r) for r in sessions]}
    except sqlite3.Error:
        return {"sessions": []}
    finally:
        conn.close()


@app.get("/api/backends")
def list_backends():
    """W1b (order `workorder_W1B_backends_endpoint.md`): read-only backend
    inventory for the UI.

    SECURITY: never returns `command` / `command_env` / `cwd` / credentials.
    Failure is LOUD (503), never a silently empty list.
    """
    from fastapi import HTTPException as _HE

    from agent_bridge.registry import load_registry
    try:
        data = load_registry() or {}
    except Exception as exc:                      # loud, never silent
        raise _HE(status_code=503,
                  detail=f"registry unavailable: {type(exc).__name__}")
    out = []
    for bid, entry in (data.get("backends") or {}).items():
        caps = entry.get("capabilities") or {}
        out.append({
            "backend_id": bid,
            "enabled": bool(entry.get("enabled")),
            "requires_authorization": bool(entry.get("requires_authorization")),
            "adapter": entry.get("adapter"),
            "transport": entry.get("transport"),
            "protocol_version": entry.get("protocol_version"),
            # values only — evidence stays internal
            "capabilities": {k: (v or {}).get("value") for k, v in caps.items()},
        })
    return {"backends": out}


@app.get("/")
def index():
    return FileResponse(PROJECT_ROOT / "api" / "static" / "index.html")


# `server.py` calls this
def get_app():
    return app
