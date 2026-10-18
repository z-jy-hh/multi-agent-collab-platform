"""R16-D5/F7: the budget gate, fed by ACP ``usage_update`` events.

F7(复核 第3轮 · 真机实测): the REAL usage_update frame is
``{"sessionUpdate": "usage_update", "used": 10575, "size": 1000000}`` —
ACP context-pressure semantics (used = context tokens currently used,
size = context window), NOT billing tokens. The previous extractor
("token" in key) counted real frames as ZERO — the budget gate never
tripped on the real machine. This extractor understands BOTH families:

- ``used`` / ``size``  → context pressure: a GAUGE (used is the current
  level, not a delta — max() is kept), gated by ``budget_context_used``;
- ``*token*`` fields   → billing-ish counts, accumulated, gated by
  ``budget_tokens`` and priced by the platform's own price table
  (dsh's own counter is heuristic 4-chars/token; the third-party ledger in
  the dsh host is explicitly NOT a contract);

and it WARNS (``unrecognized_count``) when a usage frame carries no
recognized numeric field at all — silent zero was the F7 root cause.

Money semantics: only the *token* family is priced (used/size are context
pressure, not billing tokens — documented; a billing-grade source would need
a separate feed).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BudgetState:
    input_tokens: int = 0
    output_tokens: int = 0
    other_tokens: int = 0
    context_used: int = 0            # gauge: max seen "used"
    context_size: int = 0            # latest seen "size" (window capacity)
    money: float = 0.0
    events: int = 0
    unrecognized_count: int = 0      # F7: usage frames with no known fields
    unrecognized_examples: list = field(default_factory=list)
    exceeded: bool = False
    exceeded_reason: str | None = None
    raw: list[dict] = field(default_factory=list)


def _extract_tokens(usage: dict) -> tuple[int, int, int]:
    """(input, output, other) billing-ish token counts."""
    inp = out = other = 0
    for key, value in usage.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if "token" not in key.lower():
            continue
        name = key.lower()
        if "input" in name or name in ("prompttokens", "prompt_tokens"):
            inp += int(value)
        elif "output" in name or name in ("completiontokens", "completion_tokens"):
            out += int(value)
        else:
            other += int(value)
    return inp, out, other


def _extract_context(usage: dict) -> tuple[int | None, int | None]:
    """(used, size) — ACP context-pressure semantics; None when absent."""
    used = usage.get("used")
    size = usage.get("size")
    used = int(used) if isinstance(used, (int, float)) and \
        not isinstance(used, bool) else None
    size = int(size) if isinstance(size, (int, float)) and \
        not isinstance(size, bool) else None
    return used, size


def _has_known_fields(usage: dict) -> bool:
    if _extract_context(usage) != (None, None):
        return True
    for key, value in usage.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) \
                and "token" in key.lower():
            return True
    return False


class BudgetGate:
    """Accumulates usage; flags (and reports) the moment a budget is crossed.

    Budgets (any subset): ``budget_context_used`` (context pressure — the
    real-machine gate), ``budget_tokens`` (billing-ish counts),
    ``budget_money`` (needs a price_table entry for the model).
    """

    def __init__(self, *, model: str = "default",
                 price_table: dict[str, tuple[float, float]] | None = None,
                 budget_tokens: int | None = None,
                 budget_money: float | None = None,
                 budget_context_used: int | None = None) -> None:
        self.model = model
        self.price_table = price_table or {}
        self.budget_tokens = budget_tokens
        self.budget_money = budget_money
        self.budget_context_used = budget_context_used
        self.state = BudgetState()

    def accumulate(self, usage: dict) -> BudgetState:
        usage = usage or {}
        inp, out, other = _extract_tokens(usage)
        used, size = _extract_context(usage)
        self.state.input_tokens += inp
        self.state.output_tokens += out
        self.state.other_tokens += other
        if used is not None:
            self.state.context_used = max(self.state.context_used, used)
        if size is not None:
            self.state.context_size = size
        if not _has_known_fields(usage):
            self.state.unrecognized_count += 1
            if len(self.state.unrecognized_examples) < 3:
                self.state.unrecognized_examples.append(dict(usage))
        self.state.events += 1
        self.state.raw.append(dict(usage))
        prices = self.price_table.get(self.model)
        if prices and (inp or out):
            per_in, per_out = prices
            self.state.money += (inp / 1000.0) * per_in + (out / 1000.0) * per_out
        self._check()
        return self.state

    def _check(self) -> None:
        if self.state.exceeded:
            return
        total = (self.state.input_tokens + self.state.output_tokens
                 + self.state.other_tokens)
        if self.budget_tokens is not None and total > self.budget_tokens:
            self.state.exceeded = True
            self.state.exceeded_reason = (
                f"token budget exceeded: {total} > {self.budget_tokens}")
        elif self.budget_context_used is not None and \
                self.state.context_used > self.budget_context_used:
            self.state.exceeded = True
            self.state.exceeded_reason = (
                f"context budget exceeded: used {self.state.context_used} > "
                f"{self.budget_context_used} (context-pressure semantics, F7)")
        elif self.budget_money is not None and \
                self.state.money > self.budget_money:
            self.state.exceeded = True
            self.state.exceeded_reason = (
                f"money budget exceeded: {self.state.money:.4f} > "
                f"{self.budget_money:.4f}")
