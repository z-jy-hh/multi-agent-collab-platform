"""R17-W5a (work order item 3): normalised SPI-layer errors.

New ADDITIVE module - no existing spi.py signature is touched (work order
section 2 item 5). Every backend translates its native exception families
into :class:`SpiError` so callers (conformance suite, dispatch, doctor) catch
ONE type and branch on ``kind`` instead of importing backend packages.

dsh mapping lives in ``agent_bridge/backends/dsh.py``:
  AcpProtocolError   -> kind="protocol"     (handshake/shape violation)
  AcpRemoteError     -> kind="remote"       (JSON-RPC error object)
  AcpProcessDied     -> kind="process-died" (LOUD, never silent restart)
  AcpTimeout         -> kind="timeout"      (deadline always platform-owned)
  AcpError           -> kind="error"        (other client-side failure)
  DshDispatchError   -> kind="dispatch"     (bad request / single-flight)
  DshEnvironmentError-> kind="environment"  (host cannot run dispatch)
"""

from __future__ import annotations

KINDS = ("protocol", "remote", "process-died", "timeout", "error",
         "dispatch", "environment", "budget", "authz")


class SpiError(RuntimeError):
    """One normalised error type for the whole SPI surface."""

    def __init__(self, message: str, *, kind: str, backend: str,
                 cause: BaseException | None = None,
                 code: str | None = None) -> None:
        super().__init__(f"[{backend}/{kind}] {message}")
        self.kind = kind
        self.backend = backend
        self.cause = cause
        # M4: stable machine-readable code so the UI never has to regex a message.
        self.code = code or f"{kind}.error"
