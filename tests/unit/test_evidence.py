"""W3-4: evidence change control — the four rules, pinned."""
from __future__ import annotations

import pytest

from agent_bridge.evidence import (apply_approved, classify_change,
                                   evidence_rank, propose)


def test_rank_understands_exactly_three_prefixes():
    assert evidence_rank("measured@x") == 2
    assert evidence_rank("static@x") == 1
    assert evidence_rank("assumed-fail-closed") == 0
    assert evidence_rank("probably@x") is None


def test_upgrade_and_same_and_downgrade():
    assert classify_change("static@a", "measured@b") == "upgrade"
    assert classify_change("measured@a", "measured@b") == "same"
    assert classify_change("measured@a", "static@b") == "downgrade"


def test_downgrade_is_refused_in_a_proposal():
    caps = {"egress": {"value": True, "evidence": "measured@真模型"}}
    rows = propose(caps, {"egress": {"value": None, "evidence": "assumed-fail-closed"}})
    assert rows[0]["action"] == "downgrade"
    assert rows[0]["status"] == "pending-approval"
    assert "refused" in rows[0]["reason"]


def test_unknown_capability_and_missing_evidence_are_refused():
    rows = propose({}, {"not_a_capability": {"value": 1, "evidence": "measured@x"}})
    assert rows[0]["action"] == "refused"
    rows = propose({}, {"paid": {"value": True}})
    assert rows[0]["action"] == "refused"


def test_proposal_is_never_auto_approved():
    rows = propose({"paid": {"value": False, "evidence": "measured@old"}},
                   {"paid": {"value": False, "evidence": "measured@new"}})
    assert rows[0]["status"] == "pending-approval"
    assert rows[0]["approved_by"] is None
    assert rows[0]["order_ref"] is None


def test_apply_requires_an_approval_source():
    rows = propose({}, {"paid": {"value": True, "evidence": "measured@account"}})
    with pytest.raises(PermissionError):
        apply_approved(rows[0])


def test_apply_of_a_refused_action_is_impossible_even_with_approval():
    rows = propose({}, {"not_a_capability": {"value": 1, "evidence": "measured@x"}})
    rows[0]["approved_by"] = "owner"
    rows[0]["order_ref"] = "order-1"
    with pytest.raises(PermissionError):
        apply_approved(rows[0])
