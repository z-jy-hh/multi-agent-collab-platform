"""Checker plugins. Each module exposes ``run(ctx: dict) -> CheckResult``.
The context dict carries: run_id, session_id, mode, events (list[Event]),
final_state (dict), memory (MemoryStore), params (dict), registry (Registry).
All checks are offline and deterministic."""
