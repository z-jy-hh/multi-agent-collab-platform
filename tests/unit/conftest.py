"""Network-ban fixture (task book 三·D11, verbatim reference implementation).

Every unit test runs with socket creation blocked: a unit test that tries to
reach the network fails loudly instead of passing on a flaky connection.
"""

import socket

import pytest


@pytest.fixture(autouse=True)
def _ban_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise RuntimeError("network access is banned in unit tests")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    yield
