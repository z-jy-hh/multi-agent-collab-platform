"""Web entry: uvicorn server.py --port 8000 (8001 if taken, C18/P7)."""

import argparse

# D1 (round-6): apply the CrewAI storage redirect BEFORE any project module
# (and therefore any crewai import) — single implementation in
# core/crewai_storage.py, shared with main.py and tests/conftest.py.
from core.crewai_storage import redirect_crewai_storage

redirect_crewai_storage()


def main() -> None:
    parser = argparse.ArgumentParser(description="multi_agent_collab web console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    from api.routes import get_app

    uvicorn.run(get_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
