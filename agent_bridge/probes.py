"""W3-3: capability probe contracts (pure).

The runners that OBSERVE (ACP handshake, session/list, usage frames) live
outside this module; these functions only turn an observation into the
`(value, evidence)` pair the registry accepts. Keeping them pure means the
four capability decisions that actually matter can be tested without a backend.

Evidence prefixes are the only three the registry accepts:
  measured@..., static@..., assumed-fail-closed
"""
from __future__ import annotations

MEASURED = "measured@"
STATIC = "static@"
ASSUMED = "assumed-fail-closed"


def evaluate_paid(account_ok: bool | None) -> tuple[bool, str]:
    """True only when a paid account is actually usable."""
    if account_ok is None:
        return False, ASSUMED
    if account_ok:
        return True, f"{MEASURED}account reachable"
    return False, f"{MEASURED}no usable account"


def evaluate_egress(reachable: bool | None, *, data_opt_in: bool) -> tuple[bool, str]:
    """Egress is fail-closed: unreachable/unknown is False, and true requires
    the per-task opt-in (N3). Never let a declaration turn egress on."""
    if reachable is None:
        return False, ASSUMED
    if not reachable:
        return False, f"{MEASURED}no egress observed"
    if not data_opt_in:
        return False, f"{STATIC}egress requires data_opt_in (N3)"
    return True, f"{MEASURED}egress observed with opt-in"


def evaluate_resume(handshake_ok: bool, list_ok: bool, resume_ok: bool) -> tuple[bool, str]:
    """Resume needs all three steps; the handshake alone proves nothing."""
    if not handshake_ok:
        return False, f"{MEASURED}handshake failed"
    if not list_ok:
        return False, f"{MEASURED}session/list failed or unsupported"
    if not resume_ok:
        return False, f"{MEASURED}session/resume failed"
    return True, f"{MEASURED}handshake+list+resume"


def evaluate_usage_shape(frame: dict | None) -> tuple[str | None, str]:
    """Derive the usage shape from an observed usage frame (not from belief)."""
    if not frame:
        return None, ASSUMED
    if "context_used" in frame or "contextUsed" in frame:
        return "context_used_size", f"{MEASURED}context_used observed"
    if "tokens" in frame or "input_tokens" in frame:
        return "token_family", f"{MEASURED}tokens observed"
    return None, f"{MEASURED}unknown usage shape"
