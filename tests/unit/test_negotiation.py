"""W3-1: `modes` is an ordered ADDITIVE declaration; negotiation never guesses."""
from __future__ import annotations

import copy

import pytest

from agent_bridge.negotiation import Mode, NoCommonMode, negotiate, platform_supported
from agent_bridge.registry import lint_registry, load_registry


def _base():
    return copy.deepcopy(load_registry())


def test_negotiation_respects_agent_order():
    sup = platform_supported()
    modes = [{"adapter": "acp", "transport": "stdio", "protocol_version": 1},
             {"adapter": "acp", "transport": "stdio", "protocol_version": 1,
              "endpoint": "https://fallback"}]
    chosen = negotiate(modes, sup)
    assert chosen.endpoint is None, "the FIRST supported mode must win"


def test_negotiation_picks_a_later_supported_mode():
    sup = {("acp", "stdio", 1)}
    modes = [{"adapter": "acp", "transport": "streamable-http", "protocol_version": 1},
             {"adapter": "acp", "transport": "stdio", "protocol_version": 1}]
    assert negotiate(modes, sup).transport == "stdio"


def test_no_common_mode_is_a_loud_refusal():
    sup = {("acp", "stdio", 1)}
    modes = [{"adapter": "acp", "transport": "streamable-http", "protocol_version": 1}]
    with pytest.raises(NoCommonMode) as exc:
        negotiate(modes, sup)
    assert "no-common-mode" in str(exc.value)
    assert exc.value.supported == sup


def test_protocol_v2_is_not_supported_by_default():
    with pytest.raises(NoCommonMode):
        negotiate([{"adapter": "acp", "transport": "stdio", "protocol_version": 2}],
                  platform_supported())


def test_mode_instances_are_accepted():
    assert negotiate([Mode("acp", "stdio", 1)], {("acp", "stdio", 1)}).adapter == "acp"


def test_lint_accepts_a_valid_modes_block():
    data = _base()
    data["backends"]["dsh"]["modes"] = [
        {"adapter": "acp", "transport": "stdio", "protocol_version": 1}]
    assert lint_registry(data) == []


def test_lint_rejects_unknown_key_inside_a_mode():
    data = _base()
    data["backends"]["dsh"]["modes"] = [
        {"adapter": "acp", "transport": "stdio", "protocol_version": 1,
         "command": ["rm", "-rf", "/"]}]
    errors = lint_registry(data)
    assert any("modes[0]" in e and "unknown-key" in e for e in errors)


def test_lint_rejects_empty_modes_and_bad_values():
    data = _base()
    data["backends"]["dsh"]["modes"] = []
    assert any("modes" in e for e in lint_registry(data))
    data["backends"]["dsh"]["modes"] = [
        {"adapter": "http", "transport": "stdio", "protocol_version": 1}]
    assert any("modes[0].adapter" in e for e in lint_registry(data))
