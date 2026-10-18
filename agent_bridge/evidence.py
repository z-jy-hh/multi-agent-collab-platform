"""W3-4: evidence change control (pure).

The registry's `capabilities.*.evidence` is a claim about reality. This module
decides what a fresh observation is allowed to do to that claim:

  upgrade   measured beats static beats assumed  -> a proposal may be raised
  same      same rank, possibly fresher detail   -> refresh, still needs approval
  downgrade measured -> static/assumed            -> REFUSED unless an explicit
                                                     order says so (never silent)
  invalid   missing/unknown evidence prefix,
            unknown capability, or no observation -> refused

Approval itself is NOT in this module: a proposal is `pending-approval` and a
human writes the approval row. The module never writes the registry.
"""
from __future__ import annotations

RANK = {"assumed-fail-closed": 0, "static": 1, "measured": 2}

KNOWN_CAPABILITIES = frozenset({
    "protocol_version", "transport", "permission_has_path", "permission_options",
    "usage_shape", "cancel_is_notification", "server_times_out", "supports_resume",
    "supports_background", "paid", "single_prompt_per_session", "single_root_cwd",
    "single_handler_slot", "audit_hash_chain", "sandbox_semantics",
    "supports_elicitation", "egress",
})


def evidence_rank(evidence: str) -> int | None:
    """Rank of an evidence string, or None when it is not one of the three."""
    text = str(evidence or "")
    for prefix, rank in RANK.items():
        if text.startswith(prefix + "@") or text == prefix:
            return rank
    return None


def classify_change(old_evidence: str, new_evidence: str) -> str:
    old_rank = evidence_rank(old_evidence)
    new_rank = evidence_rank(new_evidence)
    if old_rank is None or new_rank is None:
        return "invalid"
    if new_rank > old_rank:
        return "upgrade"
    if new_rank == old_rank:
        return "same"
    return "downgrade"


def propose(capabilities: dict, observations: dict) -> list[dict]:
    """Turn observed capabilities into pending proposals.

    ``observations`` maps capability -> {"value": ..., "evidence": "measured@..."}.
    Anything not observed is simply not proposed (no invented entries).
    """
    out: list[dict] = []
    for name, obs in (observations or {}).items():
        if name not in KNOWN_CAPABILITIES:
            out.append({"capability": name, "action": "refused",
                        "reason": "unknown capability"})
            continue
        obs = obs or {}
        new_evidence = str(obs.get("evidence") or "")
        if not str(new_evidence).strip():
            out.append({"capability": name, "action": "refused",
                        "reason": "observation carries no evidence"})
            continue
        old = (capabilities or {}).get(name) or {}
        action = classify_change(old.get("evidence"), new_evidence)
        entry = {
            "capability": name,
            "old": {"value": old.get("value"), "evidence": old.get("evidence")},
            "new": {"value": obs.get("value"), "evidence": new_evidence},
            "action": action,
            "status": "pending-approval",
            "approved_by": None,
            "order_ref": None,
        }
        if action == "downgrade":
            entry["reason"] = ("evidence would weaken (measured -> lower); "
                               "refused without an explicit order")
        if action == "invalid":
            entry["reason"] = "evidence prefix is not one the registry accepts"
        out.append(entry)
    return out


def apply_approved(proposal: dict) -> dict:
    """Apply ONE approved proposal to a capability dict (pure copy)."""
    if not proposal.get("approved_by") or not proposal.get("order_ref"):
        raise PermissionError("proposal has no approval source (approved_by/order_ref)")
    if proposal.get("action") not in ("upgrade", "same"):
        raise PermissionError(f"action {proposal.get('action')!r} cannot be applied")
    name = proposal["capability"]
    return {name: {"value": proposal["new"]["value"],
                   "evidence": proposal["new"]["evidence"]}}
