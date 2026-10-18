"""Registry: agent capability profiles + checker/topology registration (C14).

The Web UI's mode list MUST come from here — scanning
``configs/topologies/*.yaml`` — so "new file + one YAML entry" automatically
adds a mode. The frontend never hardcodes modes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml

from core.errors import ConfigError
from core.schemas import AgentProfileSpec


class Registry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentProfileSpec] = {}
        self._topologies: dict[str, dict] = {}
        self._checkers: dict[str, dict] = {}
        self._topology_factories: dict[str, Callable] = {}

    # -- agents ----------------------------------------------------------------
    def register_agent(self, spec: AgentProfileSpec) -> None:
        self._agents[spec.agent_id] = spec

    def get_agent(self, agent_id: str) -> AgentProfileSpec:
        if agent_id not in self._agents:
            raise ConfigError(f"unknown agent_id: {agent_id}")
        return self._agents[agent_id]

    def agents(self) -> dict[str, AgentProfileSpec]:
        return dict(self._agents)

    # -- topologies (C14: backend-driven modes) ---------------------------------
    def register_topology(self, name: str, config: dict,
                          factory: Callable | None = None) -> None:
        self._topologies[name] = config
        if factory is not None:
            self._topology_factories[name] = factory

    def topology_names(self) -> list[str]:
        return sorted(self._topologies)

    def get_topology(self, name: str) -> dict:
        if name not in self._topologies:
            raise ConfigError(f"unknown topology: {name}")
        return self._topologies[name]

    # -- inspector checkers ------------------------------------------------------
    def register_checker(self, name: str, meta: dict | None = None) -> None:
        self._checkers[name] = meta or {}

    def checker_names(self) -> list[str]:
        return sorted(self._checkers)


def load_registry(configs_dir: Path) -> Registry:
    """Cold-start loader: agents.yaml + every topologies/*.yaml on disk."""
    reg = Registry()
    agents_file = Path(configs_dir) / "agents.yaml"
    if agents_file.exists():
        data = yaml.safe_load(agents_file.read_text(encoding="utf-8")) or {}
        for item in data.get("agents", []):
            reg.register_agent(AgentProfileSpec(**item))
    topo_dir = Path(configs_dir) / "topologies"
    if topo_dir.is_dir():
        for path in sorted(topo_dir.glob("*.yaml")):
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            name = cfg.get("name") or path.stem
            reg.register_topology(name, cfg)
    return reg


__all__ = ["Registry", "load_registry", "Any"]
