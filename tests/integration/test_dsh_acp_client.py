"""R16-D1 tests: the platform's ACP v1 stdio client against a SCRIPTABLE FAKE
ACP server (offline — no real model, no real dsh binary; taskbook red line 9).

Covers the acceptance-relevant semantics of taskbook §六 判据 1/4/5/7 at the
protocol layer:
- handshake validation (protocolVersion mismatch fails LOUDLY, 判据 1);
- request/response correlation + deadline (server never times itself out);
- server→client request answering (permission round-trip; a raising policy
  is answered with a JSON-RPC error object — never left unanswered);
- notification dispatch (all five measured session/update kinds, in order);
- process exit with pending work fails LOUDLY (no silent restart, D1);
- cancel is sent as a NOTIFICATION without id (D6/判据 7 — the request-shaped
  form is silently ignored, measured V5').
"""

import os
import subprocess
import sys
import threading

import pytest

from dsh_bridge.acp_client import (
    AcpClient,
    AcpError,
    AcpProcessDied,
    AcpProtocolError,
    AcpTimeout,
)

FAKE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                    "fixtures", "fake_acp_server.py")


def make_client(scenario: str, extra_args: list[str] | None = None,
                **kwargs) -> AcpClient:
    notifications: list[tuple[str, dict]] = []
    kwargs.setdefault("on_notification",
                      lambda m, p: notifications.append((m, p)))
    client = AcpClient(
        [sys.executable, FAKE, scenario] + (extra_args or []),
        **kwargs)
    return client


def test_handshake_ok_and_version_check():
    client = make_client("happy")
    try:
        result = client.initialize(expected_agent_version="0.0.1")
        assert result["agentInfo"]["name"] == "fake-acp"
    finally:
        client.close()


def test_handshake_bad_protocol_version_fails_loudly():
    client = make_client("bad_protocol")
    try:
        with pytest.raises(AcpProtocolError, match="protocolVersion mismatch"):
            client.initialize()
    finally:
        client.close()


def test_request_timeout_when_server_never_answers():
    client = make_client("silent")
    try:
        client.initialize()                       # fake answers initialize…
        with pytest.raises(AcpTimeout):
            client.request("session/new", {"cwd": "x"}, timeout=0.3)
        # A1(复核 FINDING): the abandoned pending entry must be cleaned up —
        # a long-lived bridge must not accumulate residue on every timeout.
        assert len(client._pending) == 0
    finally:
        client.close(grace=1.0)


def test_slow_policy_does_not_stall_inbound_channel():
    """A2(复核 FINDING) 回归：权限策略在自有线程里慢决策（等宿主批准的合法
    形态），reader 必须继续吞下 initialize 应答与全部 session/update——
    不允许"策略一慢，SSE 停更、usage 断流"的假死回归。"""
    import json as _json
    import tempfile
    import time as _time

    answer_file = os.path.join(tempfile.gettempdir(), "r16_a2_answer.json")
    if os.path.exists(answer_file):
        os.remove(answer_file)
    times: dict = {"policy_done": None}

    def slow_policy(method, params):
        _time.sleep(1.2)                          # "human is deciding"
        times["policy_done"] = _time.perf_counter()
        return {"outcome": {"outcome": "selected", "optionId": "allow-once"}}

    client = make_client("slow_policy", extra_args=[answer_file],
                         on_server_request=slow_policy)
    seen: list[str] = []
    client._on_notification = lambda m, p: seen.append(
        p["update"]["sessionUpdate"])
    try:
        t0 = _time.perf_counter()
        result = client.initialize(timeout=10)    # must NOT wait for the policy
        init_latency = _time.perf_counter() - t0
        assert result["agentInfo"]["name"] == "fake-acp"
        assert not policy_done_set(times), "policy should still be thinking"
        assert init_latency < 1.0, (
            f"initialize stalled {init_latency:.2f}s behind the slow policy — "
            "server-request handling regressed back onto the reader thread")
        # the three updates the fake pushed right after the permission request
        # must arrive BEFORE the policy finishes thinking
        deadline = _time.perf_counter() + 1.1
        while len(seen) < 3 and _time.perf_counter() < deadline:
            _time.sleep(0.02)
        assert len(seen) == 3, f"updates did not arrive during slow policy: {seen}"
        assert times["policy_done"] is None or \
            times["policy_done"] > _time.perf_counter() - 0.001, \
            "updates must beat the slow policy"
        # the slow answer must still reach the server (via the answer file)
        deadline = _time.perf_counter() + 5.0
        while not os.path.exists(answer_file) and _time.perf_counter() < deadline:
            _time.sleep(0.05)
        assert os.path.exists(answer_file), "slow policy answer never reached server"
        with open(answer_file, encoding="utf-8") as fh:
            answer = _json.load(fh)
        assert answer["was_error"] is False
        assert answer["result"]["outcome"]["optionId"] == "allow-once"
    finally:
        client.close(grace=1.0)


def policy_done_set(times: dict) -> bool:
    return times.get("policy_done") is not None


def test_notifications_dispatched_in_order():
    client = make_client("updates_then_end")
    seen: list[str] = []
    client._on_notification = lambda m, p: seen.append(
        p["update"]["sessionUpdate"])
    try:
        client.initialize()
        result = client.request("session/prompt",
                                {"sessionId": "sess-1",
                                 "prompt": [{"type": "text", "text": "go"}]})
        assert result["stopReason"] == "end_turn"
        # F4: notifications dispatch asynchronously — drain the queue first
        client._notif_queue.join()
        assert seen == ["agent_thought_chunk", "agent_message_chunk",
                        "tool_call", "tool_call_update", "usage_update"]
    finally:
        client.close()


def test_server_request_roundtrip_allow():
    answers: dict = {}

    def policy(method, params):
        answers[method] = params
        return {"outcome": {"outcome": "selected", "optionId": "allow-once"}}

    client = make_client("permission_roundtrip", on_server_request=policy)
    try:
        client.initialize()
        result = client.request("session/prompt",
                                {"sessionId": "sess-1",
                                 "prompt": [{"type": "text", "text": "go"}]})
        assert result["got_answer"] is True
        assert result["answer_was_error"] is False
        assert result["answer"]["outcome"]["optionId"] == "allow-once"
        assert "session/request_permission" in answers
        # the exact measured option shape must reach the server unchanged
        assert answers["session/request_permission"]["options"][0][
            "optionId"] == "allow-once"
    finally:
        client.close()


def test_server_request_policy_crash_answered_with_error():
    def broken_policy(method, params):
        raise RuntimeError("policy engine exploded")

    client = make_client("permission_roundtrip", on_server_request=broken_policy)
    try:
        client.initialize()
        result = client.request("session/prompt",
                                {"sessionId": "sess-1",
                                 "prompt": [{"type": "text", "text": "go"}]})
        # the client MUST answer the server request even when the policy
        # crashes — an unanswered request stalls the turn forever (measured V4)
        assert result["got_answer"] is True
        assert result["answer_was_error"] is True
    finally:
        client.close()


def test_process_death_fails_pending_loudly():
    client = make_client("die_after_init")
    try:
        client.initialize()
        with pytest.raises(AcpProcessDied, match="code=3"):
            client.request("session/new", {"cwd": "x"}, timeout=10)
    finally:
        client.close(grace=1.0)


def test_cancel_is_notification_without_id():
    client = make_client("cancel_shape")
    try:
        client.initialize()
        client._request_timeout = 1.0
        import time
        # fire the prompt on a thread; it will only complete after cancel
        holder: dict = {}

        def ask():
            try:
                holder["result"] = client.request(
                    "session/prompt", {"sessionId": "sess-1",
                                       "prompt": [{"type": "text", "text": "x"}]},
                    timeout=15)
            except Exception as exc:               # pragma: no cover
                holder["error"] = exc

        t = threading.Thread(target=ask)
        t.start()
        time.sleep(0.4)                            # let the fake register it
        client.notify("session/cancel", {"sessionId": "sess-1"})
        t.join(timeout=10)
        assert "error" not in holder, holder.get("error")
        assert holder["result"]["stopReason"] == "cancelled"
        # the frame the server received must NOT carry an id (D6/判据 7)
        assert holder["result"]["cancel_had_id"] is False
    finally:
        client.close(grace=1.0)


def test_f4_slow_notification_handler_does_not_block_responses():
    """F4(复核 第3轮) 回归：on_notification 慢（SSE/黑板/预算 emit 任一阻塞）
    不得停摆入站通道——响应照常返回，通知在派发线程里有序地慢慢消化。"""
    import time as _time

    seen: list[str] = []

    def slow_notif(method, params):
        _time.sleep(0.4)                       # "SSE/黑板写入很慢"
        if method == "session/update":
            seen.append(params["update"]["sessionUpdate"])

    client = make_client("updates_then_end", on_notification=slow_notif)
    try:
        t0 = _time.perf_counter()
        client.initialize(timeout=10)
        init_latency = _time.perf_counter() - t0
        assert init_latency < 0.4, (
            f"initialize stalled {init_latency:.2f}s behind the slow "
            "notification handler — notifications regressed onto the reader")
        result = client.request("session/prompt",
                                {"sessionId": "sess-1",
                                 "prompt": [{"type": "text", "text": "go"}]},
                                timeout=15)
        prompt_latency = _time.perf_counter() - t0 - init_latency
        assert result["stopReason"] == "end_turn"
        assert prompt_latency < 1.5, (
            f"prompt blocked {prompt_latency:.2f}s behind 5×0.4s slow "
            "notification handlers — F4 regression")
        # notifications are queued, ordered, and eventually ALL delivered
        client._notif_queue.join()
        assert seen == ["agent_thought_chunk", "agent_message_chunk",
                        "tool_call", "tool_call_update", "usage_update"]
    finally:
        client.close(grace=1.0)


def test_close_is_idempotent():
    client = make_client("happy")
    client.initialize()
    client.close()
    client.close()                                 # must not raise
    with pytest.raises(AcpError, match="closed"):
        client.request("initialize", {})


def test_real_dsh_smoke_skipped_without_binary():
    """真机冒烟（判据 8 的协议层部分）仅在 dsh 可用时运行——CI/无 dsh 环境跳过。"""
    dsh_bin = os.environ.get("DSH_BIN")
    if not dsh_bin or not os.path.exists(dsh_bin):
        pytest.skip("DSH_BIN not set — real dsh smoke not available")
    client = AcpClient(["node", dsh_bin, "--profile", "acp"])
    try:
        result = client.initialize(expected_protocol_version=1)
        assert result["agentInfo"]["name"] == "deepseek-harness-acp"
    finally:
        client.close(grace=10.0)
