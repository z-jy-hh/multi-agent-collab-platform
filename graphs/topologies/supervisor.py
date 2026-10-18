"""supervisor topology (default): plan → capability match → Send fan-out →
arbitrate → summarize → review (rework loop, max 2 rounds).

The ``worker`` node is virtual (one Send per dispatched subtask, order-free
fan-in through the blackboard reducer, C5).
"""

from langgraph.graph import END
from langgraph.types import Send

from graphs.builder import make_execute_router, make_worker_node


def node_implementations(ctx):
    worker_cfg = {"id": "worker", "kind": "worker", "output_schema": {}}
    return {
        "node::worker": (worker_cfg, make_worker_node(ctx, worker_cfg)),
        "route::execute_fanout": make_execute_router(ctx),
        "route::review_gate": _review_gate(ctx),
    }


def _review_gate(ctx):
    def route(state: dict):
        max_rounds = int(ctx.params.get("max_review_rounds", 2))
        if state.get("review_verdict") == "PASS":
            return END
        if int(state.get("review_round", 0)) >= max_rounds:
            return END  # rounds exhausted — cooperative bound (C18)
        return "summarize"

    return route
