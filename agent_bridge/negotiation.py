"""W3-1: platform-side mode negotiation (the agent declares a preference order,
the platform arbitrates against what it actually supports).

NON-NEGOTIABLE:
  * the agent's order is respected (first mutually supported mode wins);
  * no downgrade, no guessing: no common mode is a loud refusal;
  * this module is pure (no env, no IO) so it is cheap to test.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    adapter: str
    transport: str
    protocol_version: int
    endpoint: str | None = None

    def key(self) -> tuple[str, str, int]:
        return (self.adapter, self.transport, self.protocol_version)


class NoCommonMode(Exception):
    """Raised when the platform supports none of the agent's declared modes."""

    def __init__(self, supported: set[tuple[str, str, int]],
                 declared: list[tuple[str, str, int]] | None = None) -> None:
        self.supported = set(supported)
        self.declared = list(declared or [])
        super().__init__(
            f"no-common-mode: platform supports {sorted(self.supported)}, "
            f"agent declared {self.declared}")


def platform_supported(adapters: set[str] | None = None,
                       transports: set[str] | None = None,
                       version: int = 1) -> set[tuple[str, str, int]]:
    """The platform's honest capability set (defaults mirror registry.py)."""
    a = adapters if adapters is not None else {"acp"}
    t = transports if transports is not None else {"stdio"}
    return {(x, y, version) for x in a for y in t}


def negotiate(modes, supported=None) -> Mode:
    """Return the first declared mode the platform supports (agent order wins).

    ``modes`` may be dicts (as parsed from YAML) or Mode instances.
    Raises NoCommonMode — never silently falls back to a different transport.
    """
    sup = set(supported) if supported is not None else platform_supported()
    normalized: list[Mode] = []
    for raw in modes or []:
        if isinstance(raw, Mode):
            normalized.append(raw)
        elif isinstance(raw, dict):
            normalized.append(Mode(
                adapter=str(raw.get("adapter")),
                transport=str(raw.get("transport")),
                protocol_version=raw.get("protocol_version"),  # type: ignore[arg-type]
                endpoint=raw.get("endpoint"),
            ))
        else:
            raise TypeError(f"mode must be a mapping or Mode, got {type(raw).__name__}")
    for mode in normalized:
        if mode.key() in sup:
            return mode
    raise NoCommonMode(sup, [m.key() for m in normalized])
