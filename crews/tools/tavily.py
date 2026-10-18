"""Optional Tavily web search (C9): only usable when TAVILY_API_KEY is set.
HTTP via the Python stack (httpx) — R26 forbids curl/Schannel for checks and
we keep the discipline here too. The local corpus remains the default tool.
"""

from __future__ import annotations

import os


class TavilyNotConfigured(RuntimeError):
    pass


def tavily_search(query: str, top_k: int = 3) -> list[dict]:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        raise TavilyNotConfigured(
            "TAVILY_API_KEY not set — web search disabled; use search_local (C9)"
        )
    import httpx  # lazy import keeps unit tests import-light

    resp = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": top_k},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", [])
    ]
