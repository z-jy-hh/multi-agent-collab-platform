"""Local-corpus search — the DEFAULT demo retrieval tool (C9).

Offline and reproducible: scans data/corpus/*.md, ranks documents by keyword
overlap with the query, returns top-k snippets. No network involved.
"""

from __future__ import annotations

from pathlib import Path

from core.paths import corpus_dir


def search_local(query: str, top_k: int = 3) -> list[dict]:
    """Return [{doc, score, snippet}] ranked by keyword overlap."""
    terms = [t for t in query.replace("，", " ").replace(",", " ").split() if t]
    results: list[dict] = []
    corpus = Path(corpus_dir())
    if not corpus.is_dir():
        return results
    for path in sorted(corpus.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        score = sum(text.count(t) for t in terms)
        if score <= 0:
            continue
        snippet = _best_paragraph(text, terms)
        results.append({"doc": path.name, "score": score, "snippet": snippet})
    results.sort(key=lambda r: (-r["score"], r["doc"]))
    return results[:top_k]


def _best_paragraph(text: str, terms: list[str]) -> str:
    best, best_hits = "", -1
    for para in (p.strip() for p in text.split("\n\n")):
        hits = sum(para.count(t) for t in terms)
        if hits > best_hits:
            best, best_hits = para, hits
    return best[:300]


if __name__ == "__main__":
    import json
    import sys

    q = " ".join(sys.argv[1:]) or "多Agent 协同"
    print(json.dumps(search_local(q), ensure_ascii=False, indent=2))
