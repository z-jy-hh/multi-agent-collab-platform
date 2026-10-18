"""Deterministic idempotency ids (C6).

op_id MUST be derived via uuid5 from stable inputs — never uuid4() inside a
node body, because LangGraph retry / time-travel replay re-executes nodes and
a fresh random id per execution would defeat deduplication entirely.

``new_run_id`` is different: it is generated exactly once, by the outer layer
at run creation, then travels inside the state.
"""

import uuid

OP_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "multi-agent-collab/ops/v1")


def derive_op_id(run_id: str, node: str, key: str, version_index: int) -> str:
    """Stable op_id: same (run, node, key, version) always yields the same id."""
    return str(uuid.uuid5(OP_NAMESPACE, f"{run_id}|{node}|{key}|{version_index}"))


def new_run_id() -> str:
    """Called once per run by the entrypoint, not inside graph nodes."""
    return str(uuid.uuid4())
