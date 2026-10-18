"""W1b: `GET /api/backends` — read-only inventory, order-backed, secret-free.

Order: D:\\dsh-work\\2026-10-17\\workorder_W1B_backends_endpoint.md (sha16 ba628e32ee892135)
The endpoint is authoritative ONLY because it is registered in
docs/endpoint_authority.md; ledgercheck enforces that separately.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from api.routes import app

FORBIDDEN = ("command", "command_env", "cwd", "env", "api_key", "token",
             "password", "secret", "credentials")


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def test_lists_the_declared_backends():
    r = TestClient(app).get("/api/backends")
    assert r.status_code == 200
    ids = {b["backend_id"] for b in r.json()["backends"]}
    assert ids == {"dsh", "wb"}


def test_never_leaks_launch_command_env_or_credentials():
    body = TestClient(app).get("/api/backends").text.lower()
    for bad in FORBIDDEN:
        assert f'"{bad}"' not in body, f"endpoint must not expose {bad!r}"


def test_wb_is_disabled_and_needs_authorization():
    rows = {b["backend_id"]: b for b in
            TestClient(app).get("/api/backends").json()["backends"]}
    assert rows["wb"]["enabled"] is False
    assert rows["wb"]["requires_authorization"] is True
    assert rows["dsh"]["enabled"] is True


def test_dsh_reports_the_real_transport_and_capabilities_without_evidence():
    rows = {b["backend_id"]: b for b in
            TestClient(app).get("/api/backends").json()["backends"]}
    dsh = rows["dsh"]
    assert dsh["adapter"] == "acp"
    assert dsh["transport"] == "stdio"
    assert dsh["protocol_version"] == 1
    assert len(dsh["capabilities"]) == 17
    # Values must be projected (evidence stays internal). Checking the key name
    # alone is vacuous: returning the raw {value, evidence} dicts kept the old
    # test green, so assert the shape recursively.
    assert all(not isinstance(v, dict) for v in dsh["capabilities"].values()), \
        "capability entries must be projected to values, not raw dicts"
    assert "evidence" not in set(_walk_keys(dsh)), \
        "no `evidence` key may appear anywhere in the payload"


def test_modes_projects_the_protocol_version_from_the_registry():
    """W0-2: /api/modes must expose the version it can actually speak."""
    body = TestClient(app).get("/api/modes").json()
    assert body["protocol_version"] == 1
    assert body["protocol_versions"] == [1]


def test_modes_reads_the_version_instead_of_hardcoding_it(monkeypatch):
    import copy

    import agent_bridge.registry as backend_registry

    data = copy.deepcopy(backend_registry.load_registry())
    data["backends"]["dsh"]["protocol_version"] = 3
    # F5: a DISABLED backend must not widen the advertised set.
    future = copy.deepcopy(data["backends"]["dsh"])
    future["enabled"] = False
    future["protocol_version"] = 2
    data["backends"]["future"] = future
    monkeypatch.setattr(backend_registry, "load_registry", lambda: data)
    body = TestClient(app).get("/api/modes").json()
    assert body["protocol_versions"] == [3], \
        "the projection must follow the registry, not a constant"
    assert body["protocol_version"] == 3


def test_backends_endpoint_fails_loudly_when_the_registry_is_broken(monkeypatch):
    import agent_bridge.registry as backend_registry

    def broken():
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(backend_registry, "load_registry", broken)
    r = TestClient(app).get("/api/backends")
    assert r.status_code == 503, "a broken registry must not look like an empty list"


def test_read_only_endpoints_are_exempt_from_the_registry_gate(monkeypatch):
    """F4 (decision, not accident): display endpoints stay readable with the
    gate OFF, because a blank UI would be worse; dispatch still obeys the gate.
    Pinning both halves keeps the exception explicit instead of implicit."""
    monkeypatch.delenv("AGENT_REGISTRY", raising=False)
    assert TestClient(app).get("/api/backends").status_code == 200
    assert TestClient(app).get("/api/modes").status_code == 200

    from agent_bridge.factory import build_backend
    assert build_backend("dsh") is None, \
        "dispatch construction must still refuse when the gate is off"
