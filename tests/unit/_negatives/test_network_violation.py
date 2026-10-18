"""Negative probe for the network-ban fixture (判据 2a reverse branch).

Skipped in normal runs (NETWORK_NEGATIVE!=1); run explicitly:
    NETWORK_NEGATIVE=1 pytest tests/unit/_negatives -q
Expected: the test FAILS (exit 1) with RuntimeError("network access is banned
in unit tests") — proving the ban is active.
"""

import os
import socket

import pytest

requires_negative_mode = pytest.mark.skipif(
    os.getenv("NETWORK_NEGATIVE") != "1",
    reason="negative probe — only meaningful when run explicitly",
)


@requires_negative_mode
def test_socket_creation_must_be_blocked():
    socket.socket()  # must raise RuntimeError from the ban fixture
