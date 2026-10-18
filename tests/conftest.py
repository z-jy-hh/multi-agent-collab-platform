"""Round-5 S1 / round-6 D1: apply the CrewAI storage redirect BEFORE any
crewai import in the test process.

Integration tests contain bare crewai imports (e.g.
tests/integration/test_g0_min_crew.py imports crewai directly). Without this
conftest those imports bypass the storage redirect and crewai creates/writes
directories under %LOCALAPPDATA%\\CrewAI at import time (dsh round-4 P3,
empirically reproduced with a PermissionError).

The redirect's single implementation lives in core/crewai_storage.py; the
main.py / server.py / tests-conftest.py entry points all call it before any
crewai import (round-6 D1/K3).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.crewai_storage import redirect_crewai_storage  # noqa: E402

redirect_crewai_storage()  # noqa: E402
