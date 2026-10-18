"""Import guard for the layering contract (task book 三·D11 / U2, with two
documented refinements required to make the task book's own requirements
coexist — both logged in reports/G1_report.md 未决清单 and G2_report.md):

1. R20/三·D12 requires tests/unit/evolver to import the evolver package,
   and pytest collects tests/unit/core (installing this guard) first, so a
   naively process-global guard would break that combined run.
2. D8/D9 (topology / api integration tests) are explicitly outside the import
   blacklist ("测试落点:无 import 黑名单约束"), yet the verbatim guard would
   block their `import crews/graphs/api` in the same pytest process.

Encoded intent: the layering violation exists when an upper-layer import
ORIGINATES from a core UNIT TEST. The guard raises only when the import chain
contains a frame under tests/unit/core/. Core-package runtime code is NOT
treated as a guarding origin here: upper-layer modules reach core through
injected factories (C4's dependency-injection design), and crewai lazily
imports tool modules while a core frame is on the stack — a real-mode false
positive. core's own C4 discipline is enforced by layercheck's trimmed env
(import-all step), not by this guard. All four E2E violation shapes from the
pre-run baseline (top-level / deferred / importlib-dynamic / transitive)
still stay RED — they all originate in tests/unit/core files.
"""

import sys
from pathlib import Path

FORBIDDEN = {"crews", "graphs", "inspector", "evolver", "api"}

_ROOT = Path(__file__).resolve().parents[3]          # project root
_CORE_UNIT_TESTS = _ROOT / "tests" / "unit" / "core"  # the guarded test dir


class LayeringViolation(ImportError):
    pass


def _origin_is_guarded() -> bool:
    """True when some frame in the import chain lives under tests/unit/core."""
    frame = sys._getframe(2)
    while frame is not None:
        fname = frame.f_globals.get("__file__", "")
        if fname:
            path = Path(fname).resolve()
            if path.is_relative_to(_CORE_UNIT_TESTS):
                return True
        frame = frame.f_back
    return False


class _LayerGuard:
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root in FORBIDDEN and _origin_is_guarded():
            raise LayeringViolation(
                "layering violation: core unit tests imported '%s'" % fullname
            )
        return None


sys.meta_path.insert(0, _LayerGuard())
