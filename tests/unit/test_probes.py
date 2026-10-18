"""W3-3: probe contracts — the four capabilities the platform actually consumes.

No backend needed: these tests feed OBSERVATIONS and assert the decision.
The point is that an unknown observation must never become a `true`.
"""
from __future__ import annotations

from agent_bridge.probes import (ASSUMED, MEASURED, STATIC, evaluate_egress,
                                 evaluate_paid, evaluate_resume, evaluate_usage_shape)


def test_paid_unknown_is_fail_closed():
    assert evaluate_paid(None) == (False, ASSUMED)
    assert evaluate_paid(False) == (False, f"{MEASURED}no usable account")
    assert evaluate_paid(True) == (True, f"{MEASURED}account reachable")


def test_egress_unknown_or_unreachable_is_false():
    assert evaluate_egress(None, data_opt_in=True)[0] is False
    assert evaluate_egress(False, data_opt_in=True)[0] is False


def test_egress_true_requires_opt_in():
    value, evidence = evaluate_egress(True, data_opt_in=False)
    assert value is False
    assert evidence.startswith(STATIC) and "data_opt_in" in evidence
    assert evaluate_egress(True, data_opt_in=True)[0] is True


def test_resume_needs_all_three_steps():
    assert evaluate_resume(True, True, False)[0] is False
    assert evaluate_resume(True, False, True)[0] is False
    assert evaluate_resume(False, True, True)[0] is False
    assert evaluate_resume(True, True, True)[0] is True


def test_usage_shape_comes_from_the_frame():
    assert evaluate_usage_shape({"context_used": 123})[0] == "context_used_size"
    assert evaluate_usage_shape({"tokens": 5})[0] == "token_family"
    assert evaluate_usage_shape(None) == (None, ASSUMED)
    assert evaluate_usage_shape({"weird": 1})[0] is None


def test_evidence_prefixes_are_the_three_registry_accepts():
    from agent_bridge.registry import EVIDENCE_PREFIXES

    for prefix in (MEASURED, STATIC, ASSUMED):
        assert prefix in EVIDENCE_PREFIXES
