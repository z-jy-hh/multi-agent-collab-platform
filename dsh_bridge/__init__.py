r"""R16 dsh_bridge: platform-side integration with DeepSeek Harness (dsh).

Layers (taskbook D1-D8, D:\dsh-work\2026-09-25\r16_acp_integration_taskbook.md):
- acp_client  — ACP v1 JSON-RPC over stdio, framing/correlation/server-request
                answering (D1);
- (D2-D8 build here: session lifecycle, event mapping, permission policy,
  budget gate, cancel semantics, structured result contract, self-check).

Red lines baked in: the child environment is a WHITELIST (no key leakage),
DSH_PERMISSION_MODE defaults to the narrowest tier the caller passes, and
every wait carries a deadline (the dsh server never times itself out).
"""
