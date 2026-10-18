"""R17-W3: ``dsh_bridge.acp_client`` is now an ALIAS of ``agent_bridge.acp_client``.

The verbatim move (W2; sha256 == baseline ``8c4fbd7ac159ef1f…``, byte-for-byte)
put the one true implementation in ``agent_bridge/acp_client.py``. This shim
replaces the ``dsh_bridge.acp_client`` entry in ``sys.modules`` with that
module object, so:

- ``from dsh_bridge.acp_client import X`` and ``import dsh_bridge.acp_client
  as m`` (Python >=3.7 resolves the ``as``-form via sys.modules) keep working
  for EVERY name — public (``AcpClient``/``AcpError``/``AcpProtocolError``/
  ``AcpRemoteError``/``AcpProcessDied``/``AcpTimeout``) AND private
  (``_ENV_KEEP``/``_Pending``/``_trace``/``_dispatch``/``_pending``);
- the two names are the SAME module object: identity, isinstance and
  monkeypatch symmetry hold (a patch on either name lands on both).

Measured semantics (R17_P0_report §3.2/§4.4, confirmed by the dsh-side P0
review F2 — both measured on CPython 3.11): after the swap
``__name__``/``__file__`` read as the agent_bridge module's, AND the
parent-package attribute ``dsh_bridge.acp_client`` also resolves to the same
module object — CPython's ``_load`` re-reads ``sys.modules`` AFTER exec and
``_find_and_load_unlocked`` rebinds the parent attribute with that result, so
``import dsh_bridge; dsh_bridge.acp_client.X`` works too. All four access
paths (from-import, ``import a.b as m``, parent attribute, monkeypatch) hit
the identical module object.
"""

import sys

from agent_bridge import acp_client as _impl

sys.modules[__name__] = _impl
