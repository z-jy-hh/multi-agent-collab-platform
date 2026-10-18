"""R16-D8: the ``dsh-check`` startup self-check (taskbook D8 / 判据 8).

Verifies, on the REAL host, everything the dispatch path depends on — and
separates ENVIRONMENT failures from CHECK failures, per the owner work order
(the sandbox-ACL defect class must never be reported as a task failure):

- ``dispatch_cwd_smoke``   — the dispatch root accepts create+delete;
- ``acp_handshake``        — initialize; protocolVersion==1 and (optionally)
                             agentInfo.version; MISMATCH = FAIL, spawn/exit =
                             ENV-FAIL (loud either way, never skipped);
- ``session_roundtrip``    — session/new → session/close;
- ``session_list``         — session/list usable;
- full tier (REAL model turns — gated behind ``full=True`` / ``--full``, the
  owner must authorize the token spend):
  ``permission_roundtrip`` — a prompt that must trigger
                             session/request_permission; the self-check answers
                             REJECT-once (fail-closed) and the turn must END;
  ``cancel_roundtrip``     — a running turn cancelled via the NOTIFICATION —
                             must end with stopReason:"cancelled";
  ``update_kind_counts``   — the session/update kinds actually observed.

Every row is machine-checkable; exit code 0 iff all rows PASS.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from dsh_bridge.acp_client import (
    AcpClient,
    AcpProcessDied,
    AcpProtocolError,
    AcpRemoteError,
    AcpTimeout,
)
from dsh_bridge.session import DshEnvironmentError, cwd_smoke
from dsh_bridge.session import dispatch_root as _default_dispatch_root


@dataclass
class DshCheckRow:
    name: str
    status: str                     # PASS | FAIL | ENV-FAIL | NOT-RUN
    details: str


# F3/F8(复核): exit-code semantics are now DISTINCT per class (the old code
# returned 0 with a "PASS full_tier skipped…" row — an unverified permission/
# cancel round-trip looked exactly like a verified one):
#   0 = everything ran and passed;  1 = at least one FAIL;
#   2 = nothing failed but something was NOT-RUN (e.g. basic tier);
#   3 = environment failure (ENV-FAIL — sandbox ACL / spawn / version lock).
def aggregate_exit(rows: list[DshCheckRow]) -> tuple[int, str]:
    statuses = [r.status for r in rows]
    if "ENV-FAIL" in statuses:
        return 3, "ENV-FAIL(环境不可用)"
    if "FAIL" in statuses:
        return 1, "FAIL"
    if "NOT-RUN" in statuses:
        return 2, "NOT-RUN(部分未验证)"
    return 0, "PASS"


def run_dsh_check(*, command_builder=None, dispatch_root: Path | None = None,
                  full: bool = False, timeout: float = 90.0,
                  expected_agent_version: str | None = None,
                  node_bin: str = "node",
                  dsh_bin_js: str | None = None) -> tuple[list[DshCheckRow], int]:
    """Run the self-check rows. ``command_builder() -> list[str]`` spawns one
    ACP subprocess per connection-needing check (production: the real dsh;
    tests: the scriptable fake). Returns (rows, exit_code) — see
    ``aggregate_exit`` for the semantics (F3/F8: a NOT-RUN full tier is its
    own third state and its own exit code, never a silent PASS)."""
    rows: list[DshCheckRow] = []

    def add(name: str, status: str, details: str) -> None:
        rows.append(DshCheckRow(name=name, status=status, details=details))

    def default_builder() -> list[str]:
        if dsh_bin_js is None:
            raise DshEnvironmentError(
                "dsh binary path unknown — set DSH_BIN (the npm-cache path to "
                "@deepseek-ai/dsh/lib/bin.js)")
        return [node_bin, dsh_bin_js, "--profile", "acp"]

    builder = command_builder or default_builder

    # A. environment: the dispatch root must be preparable (sandbox-ACL sentry)
    root = (Path(dispatch_root) if dispatch_root
            else _default_dispatch_root()) / "selfcheck"
    try:
        cwd_smoke(root)
        add("dispatch_cwd_smoke", "PASS", f"create/delete probe ok at {root}")
    except DshEnvironmentError as exc:
        add("dispatch_cwd_smoke", "ENV-FAIL", str(exc))
        add("acp_handshake", "ENV-FAIL", "skipped: dispatch root unusable "
            "(environment, not a check failure)")
        return rows, 3

    # B. handshake
    try:
        client = AcpClient(builder(),
                           env_extra={"DSH_PERMISSION_MODE": "workspace-write"})
    except (OSError, DshEnvironmentError) as exc:
        add("acp_handshake", "ENV-FAIL",
            f"cannot spawn dsh acp subprocess: {exc}")
        return rows, 3
    try:
        init = client.initialize(timeout=timeout)
        agent_info = (init or {}).get("agentInfo") or {}
        if expected_agent_version and \
                agent_info.get("version") != expected_agent_version:
            raise AcpProtocolError(
                f"agentInfo.version {agent_info.get('version')!r} != "
                f"{expected_agent_version!r} (version lock)")
        add("acp_handshake", "PASS", json.dumps(agent_info, ensure_ascii=False))
    except AcpProtocolError as exc:
        add("acp_handshake", "FAIL", str(exc))     # version/protocol mismatch
        client.close()
        return rows, 1
    except (AcpProcessDied, AcpTimeout, AcpRemoteError) as exc:
        add("acp_handshake", "ENV-FAIL",
            f"handshake did not complete: {exc}")
        client.close()
        return rows, 3

    # C. session roundtrip (new → close) — no model call
    try:
        new = client.request("session/new",
                             {"cwd": str(root), "mcpServers": []},
                             timeout=timeout)
        sid = (new or {}).get("sessionId")
        if not sid:
            raise AcpProtocolError(f"session/new returned no sessionId: {new!r}")
        client.request("session/close", {"sessionId": sid}, timeout=timeout)
        add("session_roundtrip", "PASS", f"session {sid} created and closed")
    except Exception as exc:
        add("session_roundtrip", "FAIL", f"{type(exc).__name__}: {exc}")

    # D. session/list
    try:
        listing = client.request("session/list", {"cwd": str(root)},
                                 timeout=timeout)
        sessions = (listing or {}).get("sessions", [])
        add("session_list", "PASS", f"{len(sessions)} sessions visible")
    except Exception as exc:
        add("session_list", "FAIL", f"{type(exc).__name__}: {exc}")

    # E. full tier — REAL model turns; only with explicit authorization
    if full:
        rows_full = _full_tier(builder, root, timeout)
        rows.extend(rows_full)
    else:
        add("full_tier", "NOT-RUN",
            "BY CONFIGURATION (no token spend) — 本次未验证权限/取消真机往返;"
            " run `main.py dsh-check --full` with owner authorization")

    client.close(grace=10.0)
    code, _ = aggregate_exit(rows)
    return rows, code


def _full_tier(builder, root: Path, timeout: float) -> tuple[list[DshCheckRow], bool]:
    rows: list[DshCheckRow] = []

    def add(name, status, details):
        rows.append(DshCheckRow(name=name, status=status, details=details))

    def outside_write_prompt() -> str:
        outside = str(root.parent / "selfcheck_outside")
        return ("Use your file tool to create the file " + outside +
                "\\probe.txt containing the word probe. Then reply DONE.")

    # E1. permission roundtrip: expect a request_permission, answer REJECT,
    # turn must END (measured V2: end_turn, no crash)
    saw_permission = {"seen": False}
    turn_ended = {"ended": False}
    kinds: dict[str, int] = {}
    usage_keys: set = set()          # F7③: version-drift sentinel (field census)
    frame_keys: set = set()          # the permission frame's own field names

    def count_updates(method, params):
        if method == "session/update":
            upd = (params.get("update") or {})
            kind = upd.get("sessionUpdate")
            kinds[kind] = kinds.get(kind, 0) + 1
            if kind == "usage_update":
                usage_keys.update(k for k in upd.keys() if k != "sessionUpdate")
        return None

    def on_permission(method, params):
        if method == "session/request_permission":
            saw_permission["seen"] = True
            frame_keys.update((params or {}).keys())
            return {"outcome": {"outcome": "selected", "optionId": "reject-once"}}
        return None

    client = AcpClient(builder(),
                       env_extra={"DSH_PERMISSION_MODE": "workspace-write"},
                       on_notification=count_updates,
                       on_server_request=on_permission)
    try:
        client.initialize(timeout=timeout)
        new = client.request("session/new", {"cwd": str(root), "mcpServers": []},
                             timeout=timeout)
        sid = new["sessionId"]
        result = client.request(
            "session/prompt",
            {"sessionId": sid,
             "prompt": [{"type": "text", "text": outside_write_prompt()}]},
            timeout=timeout * 3)
        stop = (result or {}).get("stopReason")
        turn_ended["ended"] = stop in ("end_turn", "cancelled")
        add("permission_roundtrip", "PASS" if (saw_permission["seen"] and
            turn_ended["ended"]) else "FAIL",
            f"request_permission seen={saw_permission['seen']} "
            f"answered reject-once; turn stopReason={stop}")
    except Exception as exc:
        add("permission_roundtrip", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        client.close(grace=10.0)               # SR3: never leak the subprocess

    # E2. cancel roundtrip: notification-shaped cancel ends the turn
    client = AcpClient(builder(),
                       env_extra={"DSH_PERMISSION_MODE": "workspace-write"})
    try:
        client.initialize(timeout=timeout)
        new = client.request("session/new", {"cwd": str(root), "mcpServers": []},
                             timeout=timeout)
        sid = new["sessionId"]

        def count_updates(method, params):
            if method == "session/update":
                kind = (params.get("update") or {}).get("sessionUpdate")
                kinds[kind] = kinds.get(kind, 0) + 1
            return None

        client.set_handlers(on_notification=count_updates)
        import threading
        t = threading.Timer(3.0, lambda: client.notify(
            "session/cancel", {"sessionId": sid}))
        t.daemon = True
        t.start()
        result = client.request(
            "session/prompt",
            {"sessionId": sid,
             "prompt": [{"type": "text",
                         "text": "Count slowly from one to one hundred, "
                                 "one number per sentence."}]},
            timeout=timeout * 3)
        stop = (result or {}).get("stopReason")
        add("cancel_roundtrip", "PASS" if stop == "cancelled" else "FAIL",
            f"stopReason={stop} (notification-shaped cancel)")
    except Exception as exc:
        add("cancel_roundtrip", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        client.close(grace=10.0)               # SR3: never leak the subprocess

    # E3. update-kind census + F7③ field census (version-drift sentinel:
    # rc-phase field names may change again — the printed keys ARE the record)
    add("update_kind_counts", "PASS" if kinds else "FAIL",
        json.dumps(kinds, ensure_ascii=False))
    add("field_census", "PASS",
        f"usage keys={sorted(usage_keys)}; permission frame keys={sorted(frame_keys)}")
    return rows
