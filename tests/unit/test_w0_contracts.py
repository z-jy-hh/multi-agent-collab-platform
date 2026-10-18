"""W0-4 / W0-6: dispatch contracts, pinned (W0-2 lives in the API tests).

W0-4  a backend that requires authorization is skipped without a consent token.
W0-6  a permission timeout emits `spi_permission_expired` (distinct from a reject).
NOTE: this module must not import `api` — layercheck runs it in a temp copy.
"""
from __future__ import annotations

from agent_bridge.hetero_dispatch import dispatch_bound_leaves

from tests.unit.test_hetero_dispatch import FakeBackend, FakeBus, _registry


def _registry_with_authz(requires: bool):
    data = _registry()
    data["backends"]["dsh"]["requires_authorization"] = requires
    data["backends"]["dsh"]["enabled"] = True
    return data


def test_requires_authorization_is_skipped_without_token(monkeypatch):
    monkeypatch.delenv("DISPATCH_CONSENT_TOKEN", raising=False)
    monkeypatch.setattr("agent_bridge.hetero_dispatch._default_store", lambda: object())
    monkeypatch.setattr("agent_bridge.factory.build_backend",
                        lambda *a, **kw: FakeBackend())
    bus = FakeBus()
    dispatch_bound_leaves(run_id="r", session_id=None, task_text="t", bus=bus,
                          registry_data=_registry_with_authz(True))
    skipped = [p for t, p in bus.events if t == "hetero_dispatch_skipped"]
    assert any(p.get("reason") == "requires-authorization" for p in skipped)
    assert "hetero_dispatch_started" not in bus.types()


def test_requires_authorization_runs_with_a_token(monkeypatch):
    monkeypatch.setenv("DISPATCH_CONSENT_TOKEN", "tok-1")
    monkeypatch.setattr("agent_bridge.hetero_dispatch._default_store", lambda: object())
    monkeypatch.setattr("agent_bridge.factory.build_backend",
                        lambda *a, **kw: FakeBackend())
    bus = FakeBus()
    dispatch_bound_leaves(run_id="r", session_id=None, task_text="t", bus=bus,
                          registry_data=_registry_with_authz(True))
    assert "hetero_dispatch_started" in bus.types()


def test_permission_timeout_emits_expired():
    # Drive the gate directly: no answer -> wait() times out -> expired event,
    # which is how "nobody answered" stays distinguishable from "rejected".
    import threading

    from agent_bridge.backends.dsh import _DshSession

    events: list[tuple[str, dict]] = []

    class Backend:
        _permission_timeout = 0.05
        _emit = staticmethod(lambda t, p: events.append((t, p)))

    session = object.__new__(_DshSession)
    session._backend = Backend()
    session._spec = type("S", (), {"run_id": "r1"})()
    session._pending = {}
    session._pending_lock = threading.Lock()

    result = session._human_hook({"toolCall": {"toolCallId": "tc-1"},
                                  "options": [{"optionId": "allow-once"}]})
    assert result is None
    kinds = [t for t, _ in events]
    assert "spi_permission_request" in kinds
    assert "spi_permission_expired" in kinds
    expired = next(p for t, p in events if t == "spi_permission_expired")
    assert expired["tool_call_id"] == "tc-1"
    assert expired["timeout_s"] == 0.05


def test_timeout_does_not_wipe_a_newer_request():
    # F6: request A's late timeout must not clear request B's slot; otherwise B
    # is unanswerable and A's expired event clears B's UI card.
    import threading

    from agent_bridge.backends.dsh import _DshSession

    events: list[tuple[str, dict]] = []

    class Backend:
        _permission_timeout = 0.3
        _emit = staticmethod(lambda t, p: events.append((t, p)))

    session = object.__new__(_DshSession)
    session._backend = Backend()
    session._spec = type("S", (), {"run_id": "r2"})()

    def make(tc):
        return type("R", (), {"tool_call_id": tc, "options": ("allow-once",)})()

    session._pending = {}
    session._pending_lock = threading.Lock()

    t = threading.Thread(target=session._human_hook,
                         args=({"toolCall": {"toolCallId": "A"},
                                "options": [{"optionId": "allow-once"}]},))
    t.start()
    # A is now waiting; a newer request B replaces the slot.
    newer = make("B")
    session._pending = {"request": newer, "event": threading.Event(), "decision": None}
    t.join(timeout=2)

    assert session._pending.get("request") is newer, "A's timeout must not wipe B"
    assert not any(tt == "spi_permission_expired" for tt, _ in events), \
        "a superseded request must not emit an expired event"