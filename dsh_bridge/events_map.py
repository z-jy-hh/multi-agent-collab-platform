"""R16-D3: ACP ``session/update`` → platform blackboard/SSE event mapping.

Table-driven (taskbook D3): the five MEASURED update kinds map to platform
events; UNKNOWN ``sessionUpdate`` values are archived verbatim and counted —
never dropped, never guessed (taskbook: "原样存档 + 计数告警"). The mapper is
PURE: it turns one update dict into a :class:`MappedEvent`; pumping to the
platform bus/blackboard is the caller's job (D4/D5 wire it).

Field extraction is defensive: the raw update always travels in the payload
(``raw``), so a dsh payload change can never silently lose information.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MappedEvent:
    """One mapped ACP update, ready for the platform pump."""

    type: str                       # platform event type
    payload: dict                   # platform event payload (always carries raw)
    final_text_append: str | None = None    # agent_message_chunk only
    blackboard_entry: dict | None = None    # tool_call: the blackboard item
    usage: dict | None = None               # usage_update: budget-gate input
    unknown: bool = False


def _text_of(update: dict) -> str:
    content = update.get("content") or {}
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, str):
        return content
    return ""


def _map_message_chunk(update: dict) -> MappedEvent:
    text = _text_of(update)
    return MappedEvent("dsh_message_chunk", {"text": text, "raw": update},
                       final_text_append=text)


def _map_thought_chunk(update: dict) -> MappedEvent:
    text = _text_of(update)
    # thought NEVER enters the final text (D3): SSE-only, collapsible
    return MappedEvent("dsh_thought_chunk", {"text": text, "raw": update})


def _map_tool_call(update: dict) -> MappedEvent:
    entry = {"toolCallId": update.get("toolCallId"),
             "title": update.get("title"),
             "rawInput": update.get("rawInput")}
    return MappedEvent("dsh_tool_call", {**entry, "raw": update},
                       blackboard_entry=entry)


def _map_tool_call_update(update: dict) -> MappedEvent:
    # failed keeps the ORIGINAL error string (D3): never summarize away errors
    payload = {"toolCallId": update.get("toolCallId"),
               "status": update.get("status"),
               "raw": update}
    if update.get("status") == "failed":
        payload["error"] = update.get("rawOutput") or update.get("content") or ""
    return MappedEvent("dsh_tool_call_update", payload)


def _map_usage_update(update: dict) -> MappedEvent:
    usage = {k: v for k, v in update.items() if k not in ("sessionUpdate",)}
    return MappedEvent("dsh_usage_update", {"raw": update}, usage=usage)


_HANDLERS = {
    "agent_message_chunk": _map_message_chunk,
    "agent_thought_chunk": _map_thought_chunk,
    "tool_call": _map_tool_call,
    "tool_call_update": _map_tool_call_update,
    "usage_update": _map_usage_update,
}

KNOWN_KINDS = frozenset(_HANDLERS)


def map_update(update: dict) -> MappedEvent:
    """Map ONE ``session/update`` inner payload (the ``update`` object)."""
    kind = (update or {}).get("sessionUpdate")
    handler = _HANDLERS.get(kind)
    if handler is None:
        return MappedEvent("dsh_unknown_update", {"raw": update}, unknown=True)
    return handler(update)


class UpdatePump:
    """Wires ``AcpClient`` notifications for ONE dsh session to platform
    callbacks. Budget gating hooks into ``on_usage`` (D5); blackboard/SSE
    writes hook into ``on_event`` (platform wiring)."""

    def __init__(self, dsh_session_id: str, *,
                 on_event: Callable[[MappedEvent], None],
                 on_usage: Callable[[dict], None] | None = None) -> None:
        self._dsh_session_id = dsh_session_id
        self._on_event = on_event
        self._on_usage = on_usage or (lambda usage: None)
        self.final_text: list[str] = []
        self.unknown_count = 0
        self.unknown_kinds: dict[str, int] = {}

    def handle_notification(self, method: str, params: dict) -> None:
        if method != "session/update":
            return
        params = params or {}
        if params.get("sessionId") != self._dsh_session_id:
            return                       # another session's traffic
        mapped = map_update(params.get("update") or {})
        if mapped.final_text_append:
            self.final_text.append(mapped.final_text_append)
        if mapped.unknown:
            self.unknown_count += 1
            kind = (params.get("update") or {}).get("sessionUpdate")
            self.unknown_kinds[kind] = self.unknown_kinds.get(kind, 0) + 1
        if mapped.usage is not None:
            self._on_usage(mapped.usage)
        self._on_event(mapped)


# typing import (placed last to keep the dataclass section readable)
from collections.abc import Callable  # noqa: E402
