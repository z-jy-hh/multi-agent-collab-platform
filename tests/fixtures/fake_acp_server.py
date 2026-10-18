"""Scriptable fake ACP server for OFFLINE tests of the platform's ACP client
(R16-D1/D9, taskbook red line 9: tests must not touch a real model or the
real dsh binary).

Usage: python fake_acp_server.py <scenario> [--delay-permission N]
       [--receipt <jsonl>] [--random-session]

Speaks newline-delimited JSON-RPC on stdio exactly like `dsh --profile acp`
(measured shapes from r16_acp_integration_taskbook.md §1.1/§1.2).

Scenarios:
  happy            — initialize + session/new + close all succeed
  bad_protocol     — initialize answers protocolVersion=99 (handshake must fail)
  die_after_init   — initialize ok, then the process exits with code 3 on the
                     next request (client must fail loudly, not restart)
  updates_then_end — session/prompt emits all five measured session/update
                     kinds, then stopReason:"end_turn"
  text_then_end    — session/prompt emits ONE agent_message_chunk whose text is
                     `ok:<len(prompt)>`, then stopReason:"end_turn" (W4-1)
  permission_roundtrip — session/prompt triggers a server→client
                     session/request_permission; records the client's answer
                     verbatim; answers the prompt with
                     {stopReason:"end_turn", got_answer, answer_was_error}
  slow_policy <answer_file> — sends a session/request_permission BEFORE
                     answering initialize, then immediately replies initialize
                     and emits three session/update notifications; writes the
                     client's eventual permission answer to <answer_file>.
  cancel_shape     — session/prompt, then the server waits for a
                     session/cancel NOTIFICATION; reports whether it carried
                     an id (it must NOT — taskbook D6, measured V5')
  silent           — answers initialize, then never answers anything
  unknown_update   — the five measured kinds PLUS one unknown sessionUpdate
  usage_burst      — usage_update trips the platform context budget
  writes_result    — writes <path>.json with <json> then end_turn
  die_mid_turn     — process exits (9) during session/prompt
  close_hangs      — answers initialize/new/prompt but NEVER answers
                     session/close (W4-1 close-bound test)
  hold_prompt      — session/prompt is NOT answered until the test sends the
                     notification `test/release` (W4-1 queue-overflow test)

W4-1 additions:
  --random-session   session/new returns a fresh uuid4 per process (default
                     stays "sess-1" so the pre-W4 tests are untouched)
  --receipt <path>   append-only JSONL with {pid,seq,event,method,params,result}
                     for every received method and every server reply
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

# W4-1: the child env is a whitelist (no PYTHONIOENCODING), so on Windows the
# default stdio codec is GBK while the client speaks UTF-8. A non-ASCII prompt
# then fails json.loads and is silently skipped (=> client timeout). The fake
# must speak UTF-8 like the real dsh, regardless of the parent locale.
for _stream in (sys.stdin, sys.stdout):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(rid, result: dict) -> None:
    send({"jsonrpc": "2.0", "id": rid, "result": result})


def main() -> None:
    args = sys.argv[1:]
    known = {"happy", "bad_protocol", "die_after_init", "updates_then_end",
             "text_then_end", "permission_roundtrip", "slow_policy",
             "cancel_shape", "silent", "usage_burst", "writes_result",
             "die_mid_turn", "unknown_update", "close_hangs", "hold_prompt"}
    scenario = next((a for a in args if a in known), "happy")
    delay_permission = 0.0
    if "--delay-permission" in args:
        delay_permission = float(args[args.index("--delay-permission") + 1])
    receipt_path = None
    if "--receipt" in args:
        receipt_path = args[args.index("--receipt") + 1]
    random_session = "--random-session" in args

    sid = str(uuid.uuid4()) if random_session else "sess-1"
    seq = 0
    pending_prompt = None       # rid of an in-flight session/prompt
    released: set = set()
    state: dict = {"sid": sid}

    def log(event: str, method: str | None = None, params=None, result=None) -> None:
        nonlocal seq
        if not receipt_path:
            return
        seq += 1
        row = {"pid": os.getpid(), "seq": seq, "event": event,
               "method": method, "params": params, "result": result}
        with open(receipt_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def answer_prompt(rid, payload: dict) -> None:
        log("send", "session/prompt.reply", None, payload)
        reply(rid, payload)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method = msg.get("method")
        rid = msg.get("id")
        if method is not None:
            log("recv", method, msg.get("params"))

        # client responses to server→client requests
        if method is None and rid is not None:
            if rid in state.get("await", {}):
                what = state["await"].pop(rid)
                if what == "permission":
                    result = msg.get("result")
                    state["answer"] = {"was_error": msg.get("error") is not None,
                                       "result": result}
                    if state.get("answer_file"):
                        with open(state["answer_file"], "w", encoding="utf-8") as fh:
                            json.dump(state["answer"], fh, ensure_ascii=False)
                    if pending_prompt is not None:
                        answer_prompt(pending_prompt, {
                            "stopReason": "end_turn",
                            "got_answer": True,
                            "answer_was_error": state["answer"]["was_error"],
                            "answer": state["answer"]["result"]})
                        pending_prompt = None
                continue
            continue

        if method == "test/release":
            # W4-1: deterministic release for the hold_prompt scenario.
            if pending_prompt is not None:
                answer_prompt(pending_prompt, {"stopReason": "end_turn"})
                pending_prompt = None
            continue

        if method == "initialize":
            if scenario == "bad_protocol":
                result = {"protocolVersion": 99,
                          "agentInfo": {"name": "fake-acp", "version": "0.0.1"}}
            elif scenario == "slow_policy":
                # A2 shape: a permission request lands BEFORE the initialize
                # answer; the client must still deliver the initialize result
                # and every subsequent update while its policy is thinking.
                answer_file = args[1] if len(args) > 1 else ""
                state["answer_file"] = answer_file
                req_id = "perm-slow"
                state.setdefault("await", {})[req_id] = "permission"
                send({"jsonrpc": "2.0", "id": req_id,
                      "method": "session/request_permission",
                      "params": {"sessionId": sid,
                                 "options": [{"optionId": "allow-once",
                                              "kind": "allow_once"},
                                             {"optionId": "reject-once",
                                              "kind": "reject_once"}]}})
                result = {"protocolVersion": 1,
                          "agentInfo": {"name": "fake-acp", "version": "0.0.1"}}
                log("send", "slow_policy.request_permission", None, None)
                for kind in ("agent_message_chunk", "tool_call", "usage_update"):
                    send({"jsonrpc": "2.0", "method": "session/update",
                          "params": {"sessionId": sid,
                                     "update": {"sessionUpdate": kind}}})
            else:
                result = {"protocolVersion": 1,
                          "agentInfo": {"name": "fake-acp", "version": "0.0.1"}}
            log("send", "initialize.reply", None, result)
            reply(rid, result)
        elif method == "session/new":
            if scenario == "silent":
                continue                     # never answer: client must hit its deadline
            if scenario == "die_after_init":
                sys.exit(3)
            result = {"sessionId": sid, "configOptions": []}
            log("send", "session/new.reply", None, result)
            reply(rid, result)
        elif method == "session/prompt":
            pending_prompt = rid
            prompt_text = ""
            try:
                prompt_text = (msg.get("params") or {}).get("prompt", [{}])[0].get("text", "")
            except Exception:
                prompt_text = ""
            if scenario in ("updates_then_end", "text_then_end", "close_hangs"):
                if scenario == "updates_then_end":
                    for kind in ("agent_thought_chunk", "agent_message_chunk",
                                 "tool_call", "tool_call_update", "usage_update"):
                        send({"jsonrpc": "2.0", "method": "session/update",
                              "params": {"sessionId": sid,
                                         "update": {"sessionUpdate": kind}}})
                else:
                    send({"jsonrpc": "2.0", "method": "session/update",
                          "params": {"sessionId": sid,
                                     "update": {"sessionUpdate": "agent_message_chunk",
                                                "content": {"text": f"ok:{len(prompt_text)}"}}}})
                answer_prompt(rid, {"stopReason": "end_turn"})
                pending_prompt = None
            elif scenario == "unknown_update":
                for upd in ({"sessionUpdate": "agent_message_chunk",
                             "content": {"text": "before"}},
                            {"sessionUpdate": "future_kind",
                             "detail": {"x": 1}},
                            {"sessionUpdate": "agent_message_chunk",
                             "content": {"text": "after"}}):
                    send({"jsonrpc": "2.0", "method": "session/update",
                          "params": {"sessionId": sid, "update": upd}})
                answer_prompt(rid, {"stopReason": "end_turn"})
                pending_prompt = None
            elif scenario == "usage_burst":
                send({"jsonrpc": "2.0", "method": "session/update",
                      "params": {"sessionId": sid,
                                 "update": {"sessionUpdate": "usage_update",
                                            "used": 10575, "size": 1000000}}})
            elif scenario == "die_mid_turn":
                sys.exit(9)
            elif scenario == "writes_result":
                target = next((a for a in args if a.endswith(".json")), None)
                content = next((a for a in args if a.startswith("{")), '{"nope": 0}')
                if target:
                    with open(target, "w", encoding="utf-8") as fh:
                        fh.write(content)
                answer_prompt(rid, {"stopReason": "end_turn"})
                pending_prompt = None
            elif scenario == "permission_roundtrip":
                send({"jsonrpc": "2.0", "method": "session/update",
                      "params": {"sessionId": sid,
                                 "update": {"sessionUpdate": "agent_message_chunk",
                                            "content": {"text": "working..."}}}})
                if delay_permission:
                    time.sleep(delay_permission)
                req_id = f"perm-{int(time.time() * 1000)}"
                state.setdefault("await", {})[req_id] = "permission"
                send({"jsonrpc": "2.0", "id": req_id,
                      "method": "session/request_permission",
                      "params": {"sessionId": sid,
                                 "toolCall": {"toolCallId": f"call-{req_id}"},
                                 "options": [{"optionId": "allow-once",
                                              "name": "Allow once",
                                              "kind": "allow_once"},
                                             {"optionId": "reject-once",
                                              "name": "Reject",
                                              "kind": "reject_once"}]}})
            elif scenario == "cancel_shape":
                state["prompt_open"] = True
                # no reply yet — the client must cancel (notification)
            elif scenario == "hold_prompt":
                pass                          # answered by test/release
            # scenario silent: never answer
        elif method == "session/cancel":
            if pending_prompt is not None:
                answer_prompt(pending_prompt, {
                    "stopReason": "cancelled",
                    "cancel_had_id": "id" in msg})
                pending_prompt = None
        elif method == "session/list":
            cwd = (msg.get("params") or {}).get("cwd", "")
            result = {"sessions": [{"sessionId": "sess-9", "cwd": cwd}]}
            log("send", "session/list.reply", None, result)
            reply(rid, result)
        elif method == "session/resume":
            result = {"configOptions": []}
            log("send", "session/resume.reply", None, result)
            reply(rid, result)
        elif method == "session/close":
            if scenario == "close_hangs":
                # W4-1: a server that never answers session/close — the client
                # must still bound its own close (C10c).
                continue
            if pending_prompt is not None and scenario == "cancel_shape":
                answer_prompt(pending_prompt, {
                    "stopReason": "cancelled",
                    "cancel_had_id": "id" in msg})
                pending_prompt = None
            log("send", "session/close.reply", None, {})
            reply(rid, {})


if __name__ == "__main__":
    main()
