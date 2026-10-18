"""R16-D1: ACP v1 JSON-RPC over stdio client for DeepSeek Harness (dsh).

Framing/correlation layer only — session semantics live in D2+. Every
protocol semantic used here was machine-tested by the dsh side and recorded
in D:\\dsh-work\\2026-09-25\\r16_acp_integration_taskbook.md (§1.1/§1.2):

- transport: JSON-RPC 2.0 over stdio, newline-delimited frames;
- server→client REQUESTS (``session/request_permission``) MUST be answered —
  an unanswered one stalls the turn forever (measured: 240 s, experiment V4);
  hence the client answers them inline via ``on_server_request``, which the
  D4 policy keeps fast (it carries its own deadline and answers reject-once
  on timeout);
- cancellation is a NOTIFICATION without ``id`` — the request-shaped form is
  silently ignored (measured: V5' ran 35 tool calls after a "cancel");
- the server never times itself out: every platform-side wait carries a
  deadline;
- process exit with requests pending is a LOUD failure, never a silent
  restart (taskbook D1).

Child environment: WHITELIST only (no key leakage, taskbook red line 6);
``env_passthrough`` adds the explicitly allowed variables (default:
``DSH_PERMISSION_MODE`` + ``DSH_HOME``).
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

# Windows 里 node/子进程需要的最小系统环境 + 平台明确放行的变量
_ENV_KEEP = ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "PATH", "TEMP",
             "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE",
             "HOMEPATH", "PROGRAMFILES", "PROGRAMFILES(X86)", "WINDIR",
             "NUMBER_OF_PROCESSORS", "OS", "PROCESSOR_ARCHITECTURE")


class AcpError(RuntimeError):
    """Client-side protocol failure (closed client, malformed usage)."""


_TRACE_PATH = os.environ.get("R16_TRACE")


def _trace(line: str) -> None:
    if _TRACE_PATH:
        with open(_TRACE_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {line}\n")


class AcpProtocolError(AcpError):
    """Handshake/shape violation — loud by contract (D1), never degraded."""


class AcpRemoteError(AcpError):
    """The ACP server answered a request with a JSON-RPC error object."""

    def __init__(self, error: dict) -> None:
        super().__init__(f"acp remote error: {error}")
        self.error = error


class AcpProcessDied(AcpError):
    """The ACP subprocess exited (with pending work or unexpectedly)."""


class AcpTimeout(AcpError):
    """A request exceeded its deadline."""


class _Pending:
    """One in-flight client request."""

    def __init__(self, method: str) -> None:
        self.method = method
        self._ev = threading.Event()
        self._result: Any = None
        self._error: AcpError | None = None

    def set_result(self, result: Any) -> None:
        self._result = result
        self._ev.set()

    def set_error(self, error: AcpError) -> None:
        self._error = error
        self._ev.set()

    def wait(self, timeout: float) -> Any:
        if not self._ev.wait(timeout):
            raise AcpTimeout(f"timeout after {timeout:.1f}s waiting for {self.method}")
        if self._error is not None:
            raise self._error
        return self._result


class AcpClient:
    """Owns ONE ``dsh --profile acp`` subprocess; speaks ACP v1 with it.

    Callbacks (owned by upper layers):
    - ``on_notification(method, params)`` — server notifications
      (``session/update`` …); exceptions are contained and logged to stderr
      sink, never kill the reader (a broken subscriber must not kill the run).
    - ``on_server_request(method, params) -> result`` — server→client
      requests (``session/request_permission``); MUST return quickly (it runs
      on the reader thread; the D4 policy enforces its own deadline and
      answers reject-once on timeout). A raising callback is answered with a
      JSON-RPC error object (the server treats it as rejection).
    """

    def __init__(self, command: list[str], *,
                 env_passthrough: tuple[str, ...] = ("DSH_PERMISSION_MODE", "DSH_HOME"),
                 env_extra: dict[str, str] | None = None,
                 on_notification: Callable[[str, dict], None] | None = None,
                 on_server_request: Callable[[str, dict], Any] | None = None,
                 stderr_sink: Callable[[str], None] | None = None,
                 request_timeout: float = 60.0) -> None:
        self._command = list(command)
        self._on_notification = on_notification or (lambda m, p: None)
        self._on_server_request = on_server_request or (lambda m, p: None)
        self._stderr_sink = stderr_sink or (lambda s: None)
        self._request_timeout = request_timeout
        self._ids = itertools.count(1)
        self._lock = threading.RLock()
        self._wlock = threading.Lock()          # stdin writes come from many threads
        self._pending: dict[Any, _Pending] = {}
        self._closed = False
        self._turn_lock = threading.Lock()   # SR2: one turn per client
        self._stderr_tail: list[str] = []
        # A2(复核 FINDING): server→client 请求（权限申请等）**绝不在 reader 线程内
        # 内联处理**——策略可能合法地慢（等宿主批准/自带 deadline），内联会把整条
        # 入站通道堵死（实测：策略 sleep 1.5s ⇒ initialize 1.56s、SSE 停更、
        # usage_update 断流）。reader 只分帧派发，应答在工作线程里完成并回写。
        self._server_req_pool = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="acp-server-req")
        # F4(复核 第3轮): A2 的同类缺陷在通知方向同样成立——慢的 on_notification
        # （SSE/黑板/预算 emit 任一阻塞）也会停摆入站通道。通知改投**单 worker
        # 有序队列**：reader 永不阻塞，且 session/update 的顺序保持（消息块
        # 顺序不能乱）。队列无界是有意为之：丢弃会丢事件，慢消费的代价是
        # 延迟与内存，由运维指标暴露（slow 标志见 notif_lag 属性）。
        self._notif_queue: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()
        self._notif_thread = threading.Thread(target=self._notif_loop,
                                              daemon=True,
                                              name="acp-notif-dispatch")
        self._notif_backlog = 0
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
            env=self._child_env(env_passthrough, env_extra or {}))
        self._stderr_thread = threading.Thread(target=self._pump_stderr, daemon=True)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._stderr_thread.start()
        self._reader.start()
        self._notif_thread.start()

    # -- environment -----------------------------------------------------------

    @staticmethod
    def _child_env(passthrough: tuple[str, ...], extra: dict[str, str]) -> dict:
        env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
        for k in passthrough:
            if k in os.environ:
                env[k] = os.environ[k]
        env.update(extra)          # explicit per-dispatch values win (D2)
        return env

    # -- I/O -------------------------------------------------------------------

    def _send_raw(self, msg: dict) -> None:
        if self._closed:
            raise AcpError("acp client is closed")
        _trace(f">>> {json.dumps(msg)[:150]}")
        try:
            with self._wlock:
                self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise AcpProcessDied(f"acp stdin write failed: {exc}") from exc

    def _pump_stderr(self) -> None:
        try:
            for line in self._proc.stderr:
                text = line.rstrip("\n")
                self._stderr_tail.append(text)
                del self._stderr_tail[:-50]        # keep a tail for crash reports
                self._stderr_sink(text)
        except (OSError, ValueError):
            pass

    def _read_loop(self) -> None:
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    self._stderr_sink(f"[acp non-json frame] {line[:200]}")
                    continue
                self._dispatch(msg)
        except AcpProcessDied:
            pass
        finally:
            self._on_process_exit()

    def _dispatch(self, msg: dict) -> None:
        _trace(f"<<< {json.dumps(msg)[:150]}")
        method = msg.get("method")
        if method is None:
            pending = self._pending.pop(msg.get("id"), None)
            if pending is None:
                # late/unknown response — record, never crash (dsh exp saw these)
                self._stderr_sink(f"[acp late response] {json.dumps(msg)[:200]}")
                return
            if msg.get("error") is not None:
                pending.set_error(AcpRemoteError(msg["error"]))
            else:
                pending.set_result(msg.get("result"))
        elif "id" in msg:
            # server→client request: MUST be answered (unanswered ⇒ turn hangs).
            # A2: handled OFF the reader thread — the policy may legitimately
            # block (human approval, its own deadline); the reader keeps
            # draining updates/responses while the policy thinks.
            self._server_req_pool.submit(self._answer_server_request, msg)
        else:
            # F4: queued to the notification dispatcher (ordered, off the
            # reader thread) — a slow on_notification cannot stall responses
            self._notif_queue.put((method, msg.get("params") or {}))

    def _notif_loop(self) -> None:
        while True:
            item = self._notif_queue.get()
            try:
                if item is None:
                    return
                method, params = item
                try:
                    self._on_notification(method, params)
                except Exception as exc:
                    # a broken subscriber must not kill the run — but it must
                    # be SEEN: the default sink goes to sys.stderr (a silent
                    # sink is how a dead budget gate turned into an invisible
                    # hang)
                    import sys as _sys
                    import traceback
                    print("[acp notification handler failed] " +
                          traceback.format_exc(), file=_sys.stderr, flush=True)
                    self._stderr_sink(f"[acp notification handler failed] {exc}")
            finally:
                self._notif_queue.task_done()

    def _answer_server_request(self, msg: dict) -> None:
        try:
            result = self._on_server_request(msg["method"], msg.get("params") or {})
            self._send_raw({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        except Exception as exc:               # policy failure ⇒ loud reject
            self._stderr_sink(f"[acp server-request handler failed] {exc}")
            try:
                self._send_raw({"jsonrpc": "2.0", "id": msg["id"],
                                "error": {"code": -32603,
                                          "message": f"platform policy error: {exc}"}})
            except AcpProcessDied:
                pass

    def _on_process_exit(self) -> None:
        try:
            code = self._proc.wait(timeout=5)   # reap: poll() may race stdout EOF
        except subprocess.TimeoutExpired:
            code = self._proc.poll()
        tail = " | ".join(self._stderr_tail[-3:])
        with self._lock:
            pending, self._pending = list(self._pending.values()), {}
        for p in pending:
            if not p._ev.is_set():
                p.set_error(AcpProcessDied(
                    f"acp process exited (code={code}) while awaiting {p.method}"
                    + (f"; stderr tail: {tail}" if tail else "")))

    # -- public API ------------------------------------------------------------

    def set_handlers(self, on_notification: Callable[[str, dict], None] | None = None,
                     on_server_request: Callable[[str, dict], Any] | None = None) -> None:
        """Late-wire the dispatch handlers (D6: the runner binds per-session
        pump/gate before each turn). Dispatch reads them per message, so
        replacement is race-safe for practical purposes."""
        if on_notification is not None:
            self._on_notification = on_notification
        if on_server_request is not None:
            self._on_server_request = on_server_request

    def request(self, method: str, params: dict, *,
                timeout: float | None = None) -> Any:
        """Client→server request with correlation + deadline. A1(复核):
        the pending entry is ALWAYS cleaned up — timeout leaks made _pending
        grow without bound on long-running bridges."""
        with self._lock:
            if self._closed:
                raise AcpError("acp client is closed")
            rid = next(self._ids)
            pending = _Pending(method)
            self._pending[rid] = pending
        try:
            self._send_raw({"jsonrpc": "2.0", "id": rid, "method": method,
                            "params": params})
            return pending.wait(self._request_timeout if timeout is None
                                else timeout)
        finally:
            self._pending.pop(rid, None)

    def notify(self, method: str, params: dict) -> None:
        """Server notification — MUST be used for ``session/cancel`` (D6:
        the request-shaped form is silently ignored, measured V5')."""
        self._send_raw({"jsonrpc": "2.0", "method": method, "params": params})

    def initialize(self, *, expected_protocol_version: int = 1,
                   expected_agent_version: str | None = None,
                   timeout: float | None = None) -> dict:
        """D1 handshake + loud version checks (never degrade silently)."""
        result = self.request("initialize",
                              {"protocolVersion": 1, "clientCapabilities": {}},
                              timeout=timeout)
        version = (result or {}).get("protocolVersion")
        if version != expected_protocol_version:
            raise AcpProtocolError(
                f"acp protocolVersion mismatch: got {version!r}, "
                f"expected {expected_protocol_version!r}")
        info = (result or {}).get("agentInfo") or {}
        if expected_agent_version is not None and \
                info.get("version") != expected_agent_version:
            raise AcpProtocolError(
                f"acp agentInfo.version mismatch: got {info.get('version')!r}, "
                f"expected {expected_agent_version!r}")
        return result

    @property
    def exit_code(self) -> int | None:
        return self._proc.poll()

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr_tail)

    def close(self, grace: float = 5.0, notif_grace: float = 10.0) -> None:
        """Stop I/O and end the child: stdin EOF first (graceful), kill only
        after grace. Pending requests fail loudly via the reader's exit path.
        Drains queued notifications (up to notif_grace). Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._server_req_pool.shutdown(wait=False, cancel_futures=True)
        self._notif_queue.put(None)                # stop the dispatcher
        if not self._notif_thread.join(timeout=notif_grace):
            pass                                   # keep closing regardless
        try:
            self._proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self._proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
