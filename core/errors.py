"""Central error taxonomy (task book appendix A: core/errors.py).

All errors raised by the engine live here so upper layers can catch them
without importing anything beyond ``core``.
"""


class CollabError(RuntimeError):
    """Base class for every engine error."""


class NamespaceViolation(CollabError):
    """C10: a private-memory access crossed its (session_id, agent_id) namespace."""


class NoCapabilityMatch(CollabError):
    """C7: coverage/min_score hard gate failed — dispatch stops, no hard assignment."""

    def __init__(self, missing: list[str], detail: str = "") -> None:
        self.missing = list(missing)
        msg = "no agent covers required capabilities: " + ", ".join(missing)
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)


class NoEligibleAgent(CollabError):
    """C7: capabilities are coverable in principle, but no SINGLE agent meets
    min_strength on every required capability of the subtask (and passes
    min_score) — dispatching to a partial-fit agent is forbidden."""


class ConfigError(CollabError):
    """Configuration missing or invalid (e.g. OPENAI_API_KEY not set)."""


class GuardViolation(CollabError):
    """C8: evolver guard refused a write outside prompts/ and configs/ whitelist."""


class StorageError(CollabError):
    """Persistence backend failure."""


class CancelledRun(CollabError):
    """Cooperative cancellation (C12/C18): checked between nodes; Python
    cannot kill a running thread, so this is the honest cancel semantics."""
