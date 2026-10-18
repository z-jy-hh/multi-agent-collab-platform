"""D12: decide() branch coverage, including the exact-threshold boundary
(KEEP iff delta > threshold; == threshold → ROLLBACK), and purity (no
file/socket access during the call).

Honest note per task book 三·D12: this is a plain unit test — no dedicated
fixture was built for it; the pytest path was proven by pre-run E4.
"""

import builtins
import socket

import pytest

from evolver.versions import Metrics, composite_score, decide


def test_better_candidate_keeps():
    base = Metrics(quality=50, cost=1000, latency_s=2.0)
    better = Metrics(quality=70, cost=900, latency_s=1.5)
    assert decide(base, better, threshold=0.01) == "KEEP"


def test_worse_candidate_rolls_back():
    base = Metrics(quality=50, cost=1000, latency_s=2.0)
    worse = Metrics(quality=30, cost=2000, latency_s=4.0)
    assert decide(base, worse, threshold=0.001) == "ROLLBACK"


def test_exact_threshold_is_rollback():
    # only quality moves: delta = 0.6 * (q' - q)/100
    base = Metrics(quality=50, cost=1000, latency_s=2.0)
    cand = Metrics(quality=51, cost=1000, latency_s=2.0)
    delta = composite_score(cand) - composite_score(base)
    assert delta == pytest.approx(0.6 * 0.01)
    assert decide(base, cand, threshold=delta) == "ROLLBACK"        # == → ROLLBACK
    assert decide(base, cand, threshold=delta - 1e-9) == "KEEP"     # just below → KEEP
    assert decide(base, cand, threshold=0.0061) == "ROLLBACK"       # above → ROLLBACK


def test_mixed_dimensions_use_weights():
    # quality up but cost explodes: weighted composite decides
    base = Metrics(quality=50, cost=500, latency_s=2.0)
    cand = Metrics(quality=52, cost=20000, latency_s=2.0)
    assert decide(base, cand, threshold=0.001) == "ROLLBACK"


def test_decide_is_pure_no_file_no_socket(monkeypatch):
    def _no_open(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("decide() must not touch the filesystem")

    def _no_socket(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("decide() must not touch the network")

    monkeypatch.setattr(builtins, "open", _no_open)
    monkeypatch.setattr(socket, "socket", _no_socket)
    base = Metrics(quality=50, cost=1000, latency_s=2.0)
    cand = Metrics(quality=60, cost=900, latency_s=1.0)
    assert decide(base, cand, threshold=0.01) == "KEEP"
