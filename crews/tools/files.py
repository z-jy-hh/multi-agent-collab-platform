"""File tool with a hard project-root fence (C1): reads/writes never leave
the project folder, whatever path string an agent produces."""

from __future__ import annotations

from pathlib import Path

from core.errors import CollabError
from core.paths import PROJECT_ROOT


class PathEscape(CollabError):
    pass


def _resolve(relative: str) -> Path:
    candidate = (PROJECT_ROOT / relative).resolve()
    root = PROJECT_ROOT.resolve()
    if root not in candidate.parents and candidate != root:
        raise PathEscape(f"path escapes project root: {relative!r}")
    return candidate


def read_file(relative: str) -> str:
    """读取项目内指定相对路径的文本文件,返回其内容(路径不得越出项目根)。"""
    return _resolve(relative).read_text(encoding="utf-8")


def write_file(relative: str, content: str) -> str:
    """写入文本到项目内 runs/ 或 data/ 下的相对路径,返回实际写入路径。"""
    target = _resolve(relative)
    allowed_roots = [(PROJECT_ROOT / "runs").resolve(), (PROJECT_ROOT / "data").resolve()]
    if not any(target.is_relative_to(r) for r in allowed_roots):
        raise PathEscape(f"writes allowed only under runs/ or data/: {relative!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)


if __name__ == "__main__":
    print(read_file("README.md")[:80] if Path(PROJECT_ROOT / "README.md").exists() else "README not written yet")
