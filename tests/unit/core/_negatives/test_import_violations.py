"""Negative probe (U2 evidence 2): importing an upper layer from a core unit
test MUST fail with LayeringViolation.

Skipped in normal runs (LAYERCHECK_NEGATIVE!=1); run explicitly:
    LAYERCHECK_NEGATIVE=1 pytest tests/unit/core/_negatives -q
Expected: this test FAILS (exit 1) — that failure IS the evidence that the
guard works.
"""

import os

import pytest

requires_negative_mode = pytest.mark.skipif(
    os.getenv("LAYERCHECK_NEGATIVE") != "1",
    reason="negative probe — only meaningful when run explicitly",
)


@requires_negative_mode
def test_top_level_import_of_graphs_must_fail():
    import graphs  # noqa: F401  — must raise LayeringViolation


@requires_negative_mode
def test_deferred_import_of_api_must_fail():
    def _sneaky():
        import api  # noqa: F401  — deferred import is still caught

    _sneaky()  # must raise LayeringViolation → this test goes RED


@requires_negative_mode
def test_dynamic_import_of_inspector_must_fail():
    import importlib

    importlib.import_module("inspector")  # must raise → RED


@requires_negative_mode
def test_evolver_import_is_exempt_only_for_evolver_tests():
    # This file lives under tests/unit/core — the exemption must NOT apply.
    import evolver  # noqa: F401  — must raise LayeringViolation
