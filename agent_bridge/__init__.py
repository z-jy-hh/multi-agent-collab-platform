"""R17 agent_bridge: the SPI home for multi-backend agent access (design v3 §2).

P0 scope (owner work order, hard rollback constraint) is ONLY:
- ``spi.py``        — signature-level types/Protocols (W1, no behaviour);
- ``acp_client.py`` — byte-identical move of ``dsh_bridge/acp_client.py`` (W2,
  criterion: sha256 == baseline ``8c4fbd7ac159ef1f…``);
- the ``dsh_bridge/acp_client.py`` ``sys.modules`` alias shim (W3, private
  names included) so every existing importer keeps working unchanged.

Backends (dsh W5 / wb W6), registry (P1), reconcile (W7) come later — none of
them may change dsh behaviour. Dependency direction: agent_bridge -> core,
NEVER core -> agent_bridge (layercheck red line).
"""
