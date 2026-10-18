"""GraphState + pure reducers (C5).

Reducer discipline:
- pure functions: same input → same output, no side effects, no logging,
  no conflict *decisions*, no LLM;
- merge blackboard ops by vector clock without dropping values: concurrent
  entries all survive (MV semantics), strictly dominated entries are subsumed;
- conflict *marking* is a separate pure helper (``conflict_keys``); the
  arbitration node after fan-in decides what to do about it.
"""

from typing import Annotated, Any, TypedDict

# NOTE: no `from __future__ import annotations` here — LangGraph and the
# purity tests need runtime (non-string) Annotated metadata on GraphState.

from core.crdt import HLC, MVEntry, clock_compare, mv_merge
from core.crdt import ClockOrder


def merge_blackboard(existing: dict[str, list[dict]] | None,
                     incoming: list[dict] | None) -> dict[str, list[dict]]:
    """Channel reducer for the blackboard. Pure function.

    ``existing`` maps key -> list of serialized MVEntry dicts; ``incoming``
    is a list of serialized MVEntry dicts produced during this step.
    """
    result: dict[str, list[dict]] = {k: list(v) for k, v in (existing or {}).items()}
    for item in incoming or []:
        result.setdefault(item["key"], [])
        if item not in result[item["key"]]:
            result[item["key"]].append(dict(item))
    # MV-merge each slot: dedupe by op_id, drop dominated, keep concurrent
    merged: dict[str, list[dict]] = {}
    for key, raw in result.items():
        entries = [MVEntry.from_dict(d) for d in raw]
        merged[key] = [e.to_dict() for e in mv_merge(entries)]
    return merged


def conflict_keys(blackboard: dict[str, list[dict]]) -> list[str]:
    """Keys whose surviving entries are mutually concurrent = conflict found.
    Pure helper — the arbitration node consumes this; the reducer never does."""
    out: list[str] = []
    for key, raw in (blackboard or {}).items():
        entries = [MVEntry.from_dict(d) for d in raw]
        if any(
            clock_compare(a.clock, b.clock) is ClockOrder.CONCURRENT
            for i, a in enumerate(entries)
            for b in entries[i + 1:]
        ):
            out.append(key)
    return sorted(out)


def resolve_blackboard(blackboard: dict[str, list[dict]]) -> dict[str, Any]:
    """LWW pick (stable HLC total order) per key — used after arbitration
    resolved a conflict or for the final read-down. Pure function."""
    out: dict[str, Any] = {}
    for key, raw in (blackboard or {}).items():
        entries = [MVEntry.from_dict(d) for d in raw]
        if entries:
            winner = max(entries, key=lambda e: e.hlc)
            out[key] = winner.value
    return out


def merge_subtasks(existing: list[dict] | None, incoming: list[dict] | None) -> list[dict]:
    """Channel reducer for subtask lists. Dedup by subtask_id; the INCOMING
    version wins (newer assignment info must not be shadowed by the plan
    stage's earlier copies). Content-wise identical regardless of which
    parallel branch delivered the value (C5)."""
    out: dict[str, dict] = {d["subtask_id"]: d for d in (existing or [])}
    for d in incoming or []:
        out[d["subtask_id"]] = d
    return [out[k] for k in sorted(out)]


def merge_notes(existing: list[str] | None, incoming: list[str] | None) -> list[str]:
    """Ordered merge for parallel agent outputs. Send branches arrive in
    nondeterministic order (C5), so the merged result is SORTED — the merged
    set is identical regardless of arrival order."""
    seen: set[str] = set()
    for s in (existing or []) + (incoming or []):
        seen.add(s)
    return sorted(seen)


def merge_dicts(existing: dict | None, incoming: dict | None) -> dict:
    """Channel reducer for plain dict state (e.g. debate_arguments).
    Pure function; right side wins per key."""
    out = dict(existing or {})
    out.update(incoming or {})
    return out


class GraphState(TypedDict, total=False):
    run_id: str
    session_id: str
    task: str
    mode: str
    subtasks: Annotated[list[dict], merge_subtasks]
    blackboard: Annotated[dict[str, list[dict]], merge_blackboard]
    worker_notes: Annotated[list[str], merge_notes]
    conflicts: list[str]           # set by the arbitration node (overwrite)
    arbitration: dict[str, Any]    # overwrite
    review_round: int              # overwrite
    review_verdict: str            # overwrite
    review_comments: str           # overwrite (sequential rework loop)
    debate_round: int              # overwrite
    debate_arguments: Annotated[dict, merge_dicts]
    final_report: str              # overwrite
    status: str                    # overwrite
    assignment_events: list[dict]  # matcher reason records (merge)
