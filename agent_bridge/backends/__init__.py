"""R17-W5: backend adapters implementing the SPI (design v3 section 2).

Each adapter COMPOSES its native stack and translates to/from the SPI - it
never re-implements protocol, budget, permission, or cancellation semantics:

- ``dsh``  (W5)  composes ``dsh_bridge`` (DshSessionManager + DshTurnRunner);
- ``wb``   (W6)  will compose the CodeBuddy CLI over ACP (P3, offline first).

Adapters read capability flags and configuration from the registry (P1);
flags inconsistent with what the composition can actually honour are a
fail-closed constructor error (C5), never a silent downgrade.
"""
