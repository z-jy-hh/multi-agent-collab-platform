"""R16-D3 tests: the table-driven session/update mapping (offline, pure)."""

from dsh_bridge.events_map import (
    KNOWN_KINDS,
    MappedEvent,
    UpdatePump,
    map_update,
)

MEASURED_KINDS = ["agent_message_chunk", "agent_thought_chunk", "tool_call",
                  "tool_call_update", "usage_update"]


def test_all_five_measured_kinds_are_known():
    assert KNOWN_KINDS == set(MEASURED_KINDS)


def test_message_chunk_feeds_final_text():
    ev = map_update({"sessionUpdate": "agent_message_chunk",
                     "content": {"type": "text", "text": "你好"}})
    assert ev.type == "dsh_message_chunk"
    assert ev.final_text_append == "你好"
    assert ev.payload["raw"]["content"]["text"] == "你好"   # raw always travels


def test_thought_chunk_never_enters_final_text():
    ev = map_update({"sessionUpdate": "agent_thought_chunk",
                     "content": {"text": "thinking..."}})
    assert ev.type == "dsh_thought_chunk"
    assert ev.final_text_append is None


def test_tool_call_carries_blackboard_entry():
    update = {"sessionUpdate": "tool_call", "toolCallId": "call_00_1",
              "title": "Write file", "rawInput": {"path": "x.json"}}
    ev = map_update(update)
    assert ev.type == "dsh_tool_call"
    assert ev.blackboard_entry == {"toolCallId": "call_00_1",
                                   "title": "Write file",
                                   "rawInput": {"path": "x.json"}}


def test_tool_call_update_failed_keeps_original_error():
    update = {"sessionUpdate": "tool_call_update", "toolCallId": "call_00_1",
              "status": "failed",
              "rawOutput": "[sandbox: file access denied under workspace-write mode]"}
    ev = map_update(update)
    assert ev.type == "dsh_tool_call_update"
    assert ev.payload["status"] == "failed"
    assert "file access denied" in ev.payload["error"]


def test_usage_update_routed_to_usage_channel():
    update = {"sessionUpdate": "usage_update", "inputTokens": 10,
              "outputTokens": 5}
    ev = map_update(update)
    assert ev.type == "dsh_usage_update"
    assert ev.usage["inputTokens"] == 10


def test_unknown_kind_archived_verbatim_and_counted():
    ev = map_update({"sessionUpdate": "plan", "entries": [1, 2, 3]})
    assert ev.type == "dsh_unknown_update"
    assert ev.unknown is True
    assert ev.payload["raw"]["entries"] == [1, 2, 3]      # verbatim, not dropped
    assert ev.final_text_append is None


def test_pump_routes_by_session_and_accumulates():
    events: list[MappedEvent] = []
    usages: list[dict] = []
    pump = UpdatePump("sess-1", on_event=events.append,
                      on_usage=usages.append)
    pump.handle_notification("session/update", {
        "sessionId": "sess-2",                       # another session — ignored
        "update": {"sessionUpdate": "agent_message_chunk",
                   "content": {"text": "not mine"}}})
    pump.handle_notification("session/update", {
        "sessionId": "sess-1",
        "update": {"sessionUpdate": "agent_message_chunk",
                   "content": {"text": "line1 "}}})
    pump.handle_notification("session/update", {
        "sessionId": "sess-1",
        "update": {"sessionUpdate": "agent_message_chunk",
                   "content": {"text": "line2"}}})
    pump.handle_notification("session/update", {
        "sessionId": "sess-1",
        "update": {"sessionUpdate": "plan", "x": 1}})   # unknown → counted
    pump.handle_notification("session/cancel", {"sessionId": "sess-1"})  # ignored
    assert [e.type for e in events] == ["dsh_message_chunk",
                                        "dsh_message_chunk",
                                        "dsh_unknown_update"]
    assert "".join(pump.final_text) == "line1 line2"
    assert pump.unknown_count == 1
    assert pump.unknown_kinds == {"plan": 1}
    assert usages == []
