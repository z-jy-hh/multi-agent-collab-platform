"""W2-1: backend construction is DATA (registration), not branches in the factory.

The wb double gate and the loud unknown-id failure must survive the refactor.
"""
from __future__ import annotations

import pytest

import agent_bridge.factory as factory
from agent_bridge.errors import SpiError


@pytest.fixture()
def isolated(monkeypatch):
    """Fresh registry view with the gate open; restore _BACKENDS afterwards."""
    saved = dict(factory._BACKENDS)
    monkeypatch.setattr(factory, "registry_gate", lambda: True)
    # load_registry is imported INSIDE build_backend, so patch it at the source.
    monkeypatch.setattr("agent_bridge.registry.load_registry",
                        lambda: {"backends": {"dsh": {"enabled": True},
                                              "wb": {"enabled": False},
                                              "alias": {"enabled": True},
                                              "gated": {"enabled": True}},
                                 "defaults": {}})
    try:
        yield
    finally:
        factory._BACKENDS.clear()
        factory._BACKENDS.update(saved)


def test_builtin_backends_are_registered():
    assert "dsh" in factory.registered_backends()
    assert "wb" in factory.registered_backends()


def test_registration_is_visible(isolated):
    factory.register_backend("alias", "agent_bridge.backends.dsh", "DshBackend")
    assert "alias" in factory.registered_backends()
    # The constructible path itself is covered by the real dsh/wb suites, which
    # now go through the same data-driven branch.


def test_unregistered_backend_still_fails_loudly(isolated):
    with pytest.raises(SpiError) as exc:
        factory.build_backend("mystery")
    assert "no adapter registered" in str(exc.value) or "not in registry" in str(exc.value)


def test_registered_but_undeclared_backend_fails_loudly(isolated):
    factory.register_backend("ghost", "agent_bridge.backends.dsh", "DshBackend")
    with pytest.raises(SpiError) as exc:
        factory.build_backend("ghost")
    assert "not in registry" in str(exc.value)


def test_code_level_gate_still_wins(isolated):
    factory.register_backend("gated", "agent_bridge.backends.dsh", "DshBackend",
                             gate=lambda: False)
    assert factory.build_backend("gated") is None, \
        "a closed code-level gate must make the backend unreachable"


def test_wb_stays_behind_its_double_gate(isolated, monkeypatch):
    monkeypatch.setattr(factory, "wb_gate", lambda: False)
    assert factory.build_backend("wb") is None
