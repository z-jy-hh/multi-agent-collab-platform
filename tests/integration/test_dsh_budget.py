import pytest

"""R16-D5 tests: budget gate accumulation + trip conditions (offline pure)."""

from dsh_bridge.budget import BudgetGate


def test_accumulates_across_usage_events():
    gate = BudgetGate(budget_tokens=10000)
    gate.accumulate({"inputTokens": 100, "outputTokens": 20})
    state = gate.accumulate({"inputTokens": 30, "outputTokens": 5})
    assert state.input_tokens == 130
    assert state.output_tokens == 25
    assert state.events == 2
    assert state.exceeded is False


def test_token_budget_trips():
    gate = BudgetGate(budget_tokens=150)
    gate.accumulate({"inputTokens": 100})
    state = gate.accumulate({"outputTokens": 60})       # 160 > 150
    assert state.exceeded is True
    assert "token budget" in state.exceeded_reason
    # stays exceeded (latching — a tripped budget must not un-trip)
    state = gate.accumulate({"inputTokens": 1})
    assert state.exceeded is True


def test_money_budget_uses_platform_price_table():
    # platform's own price table: ¥ per 1k tokens (in, out)
    gate = BudgetGate(model="deepseek-v4-flash",
                      price_table={"deepseek-v4-flash": (0.001, 0.002)},
                      budget_money=0.5)
    gate.accumulate({"inputTokens": 100_000, "outputTokens": 50_000})
    # 100 * 0.001 + 50 * 0.002 = 0.2
    state = gate.accumulate({"inputTokens": 200_000, "outputTokens": 100_000})
    # + 200*0.001 + 100*0.002 = 0.4 → total 0.6 > 0.5
    assert state.money == pytest.approx(0.6)
    assert state.exceeded is True
    assert "money budget" in state.exceeded_reason


def test_unknown_usage_fields_count_as_other_tokens():
    gate = BudgetGate(budget_tokens=50)
    state = gate.accumulate({"cachedTokens": 80})       # preview field drift
    assert state.other_tokens == 80
    assert state.exceeded is True                       # still gated, not dropped


def test_non_numeric_and_non_token_fields_ignored():
    gate = BudgetGate(budget_tokens=10)
    state = gate.accumulate({"model": "deepseek-v4-flash", "sessionId": "s",
                             "inputTokens": 1})
    assert state.input_tokens == 1
    assert state.exceeded is False


import pytest  # noqa: E402  (used above; kept last like the bridge modules)


def test_f7_context_pressure_budget_trips_on_real_frame_shape():
    """F7: the REAL usage frame is {used, size} (context pressure) — the
    old extractor counted it as zero and the gate never tripped."""
    gate = BudgetGate(budget_context_used=10000)
    state = gate.accumulate({"used": 10575, "size": 1000000})
    assert state.context_used == 10575
    assert state.context_size == 1000000
    assert state.exceeded is True
    assert "context budget" in state.exceeded_reason


def test_f7_gauge_semantics_max_not_sum():
    gate = BudgetGate(budget_context_used=10000)
    gate.accumulate({"used": 9000, "size": 1000000})
    state = gate.accumulate({"used": 5000, "size": 1000000})   # went DOWN
    assert state.context_used == 9000                          # gauge, not sum
    assert state.exceeded is False


def test_f7_unknown_usage_shape_warns_not_silently_zero():
    gate = BudgetGate(budget_context_used=10000)
    state = gate.accumulate({"foo": "bar", "whatever": 1})
    assert state.unrecognized_count == 1
    assert state.unrecognized_examples[0] == {"foo": "bar", "whatever": 1}
    assert state.exceeded is False                             # nothing gated


def test_f7_token_family_still_supported():
    gate = BudgetGate(budget_tokens=150)
    gate.accumulate({"inputTokens": 100, "outputTokens": 60})
    assert gate.state.exceeded is True
