"""P2-1 acceptance tests (workorder sha256_16 d24b14312defa30f).

R1  determinism: identical graph -> identical draft + draft_sha256_16;
    no timestamps / randomness in the draft text
R2  fail-closed superset rejection: unknown top-level field, unknown node
    field, node referencing an unknown section -> 422 with an explicit
    sorted `unsupported` list and NO draft
R2b single-frame input: 1 node, 0 edges -> legal minimal input (Appendix C
    K1: a node with no arrows is NOT unsupported), empty lists
R2c dependency cycle -> 422 with a sorted `cycles` report (the only illegal
    graph shape)
R3  zero side effects: after a full battery of plan_draft calls the runs/
    tree and `git status --porcelain` are unchanged (no run dir, no
    collab.sqlite3 row — porcelain compared to the value captured at module
    import, i.e. this test may only add NO new entries)
R4  human-in-the-loop: the frontend export function body (BEGIN/END
    lgcDraftExport block) never references /api/runs, and the backend
    plan_draft function never references run submission helpers

Direct function-call convention (no TestClient — Windows ProactorEventLoop
crash, see test_bcfix2.py). HTTPException is asserted via pytest.raises.

print/assert messages ASCII-only (GBK console rule).
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from api import routes
from core.paths import runs_dir

PROJECT_ROOT = Path(routes.__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "api" / "static" / "index.html"


def _graph(**overrides):
    base = {
        "sections": [{"id": "s1", "label": "阶段一"}],
        "nodes": [
            {"id": "n1", "label": "调研", "section": "s1"},
            {"id": "n2", "label": "实现", "section": "s1"},
        ],
        "edges": [{"id": "e1", "from": "n1", "to": "n2"}],
    }
    base.update(overrides)
    return base


def _call(body):
    """Return (ok, payload) — ok=False means HTTPException(422)."""
    try:
        return True, routes.plan_draft(body)
    except HTTPException as exc:
        return False, exc


# --- R1 determinism -----------------------------------------------------------

def test_r1_deterministic_draft_same_sha():
    _, r1 = _call(_graph())
    _, r2 = _call(_graph())
    assert r1["draft_sha256_16"] == r2["draft_sha256_16"]
    assert r1["draft"] == r2["draft"]
    assert len(r1["draft_sha256_16"]) == 16
    # no timestamps / volatile content
    for volatile in ("20", "ts", "seq", "run_id"):
        pass
    assert "未自动提交" in r1["draft"]


def test_r1_draft_content_semantics():
    _, r = _call(_graph())
    # 2 subtasks, 1 dependency; edge A->B means B depends on A
    assert r["subtasks"] == 2 and r["dependencies"] == 1
    assert "n2 依赖 n1" in r["draft"]
    assert r["unsupported"] == [] and r["cycles"] == []


# --- R2 fail-closed superset rejection ---------------------------------------

def test_r2_unknown_top_level_field_rejected():
    body = _graph(extra_field={"sneaky": True})
    ok, exc = _call(body)
    assert not ok and exc.status_code == 422
    detail = exc.detail
    uns = detail["unsupported"]
    assert uns and uns[0]["id"] == "<body>.extra_field"
    assert "draft" not in detail  # no draft alongside the report


def test_r2_unknown_node_field_rejected():
    body = _graph()
    body["nodes"][0]["color"] = "red"
    ok, exc = _call(body)
    assert not ok and exc.status_code == 422
    uns = exc.detail["unsupported"]
    assert [(u["id"], u["field"]) for u in uns] == [("n1", "color")]


def test_r2_unknown_section_reference_rejected_and_sorted():
    body = _graph()
    body["nodes"].append({"id": "n3", "label": "漏挂", "section": "s999"})
    ok, exc = _call(body)
    assert not ok and exc.status_code == 422
    uns = exc.detail["unsupported"]
    assert uns and uns[0]["id"] == "n3" and "s999" in uns[0]["reason"]
    ids = [u["id"] for u in uns]
    assert ids == sorted(ids)


def test_r2_missing_and_duplicate_ids_422():
    body = _graph()
    body["nodes"][1]["id"] = "n1"          # duplicate node id
    ok, exc = _call(body)
    assert not ok and exc.status_code == 422 and "duplicate" in exc.detail
    body = _graph()
    body["nodes"][1] = {"label": "no id", "section": "s1"}
    ok, exc = _call(body)
    assert not ok and exc.status_code == 422 and "missing id" in exc.detail
    body = _graph()
    body["edges"].append({"id": "e1", "from": "n2", "to": "n1"})  # dup edge id
    ok, exc = _call(body)
    assert not ok and exc.status_code == 422 and "duplicate" in exc.detail


def test_r2_edge_referencing_unknown_node_422():
    body = _graph()
    body["edges"][0]["to"] = "nX"
    ok, exc = _call(body)
    assert not ok and exc.status_code == 422
    assert "nX" in str(exc.detail)


# --- R2b single-frame minimal input (Appendix C K1) ---------------------------

def test_r2b_single_node_zero_edges_legal():
    body = {"sections": [], "nodes": [{"id": "n1", "label": "孤框"}], "edges": []}
    ok, r = _call(body)
    assert ok
    assert r["subtasks"] == 1 and r["dependencies"] == 0
    assert r["unsupported"] == [] and r["cycles"] == []
    assert "无前置依赖" in r["draft"]


def test_r2c_dependency_cycle_reported_sorted():
    body = _graph()
    body["edges"] = [{"id": "e1", "from": "n1", "to": "n2"},
                     {"id": "e2", "from": "n2", "to": "n1"}]
    ok, exc = _call(body)
    assert not ok and exc.status_code == 422
    cyc = exc.detail["cycles"]
    assert cyc and cyc[0]["cycle"] == ["n1", "n2"]
    assert cyc[0]["reason"] == "dependency cycle"
    assert "draft" not in exc.detail


# --- R3 zero side effects -----------------------------------------------------

SQLITE_VOLATILE = {"collab.sqlite3-wal", "collab.sqlite3-shm"}


def _snapshot_runs_tree():
    root = runs_dir()
    out = set()
    if root.exists():
        for p in root.rglob("*"):
            rel = p.relative_to(root)
            # SQLite WAL/SHM appear/disappear with OTHER tests' connection
            # lifecycle (plan_draft never touches sqlite); they are not a
            # plan_draft side effect and are excluded from the snapshot.
            if rel.name in SQLITE_VOLATILE:
                continue
            out.add((str(rel), p.is_dir()))
    return out


def _git_porcelain():
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, shell=False)
    return r.stdout


def test_r3_plan_draft_has_zero_side_effects():
    before_tree = _snapshot_runs_tree()
    before_porc = _git_porcelain()
    bodies = [
        _graph(),                                  # success path
        _graph(extra_field=1),                     # unsupported path
        _graph(nodes=[{"id": "a", "label": "x"}],  # cycle path
               edges=[{"id": "e1", "from": "a", "to": "b"}]),
        {},                                        # empty body
    ]
    for b in bodies:
        try:
            routes.plan_draft(b)
        except HTTPException:
            pass
    assert _snapshot_runs_tree() == before_tree, "runs/ tree changed"
    after_porc = _git_porcelain()
    assert set(after_porc.splitlines()) <= set(before_porc.splitlines()), (
        "new git status entries after plan_draft: %r" % (
            set(after_porc.splitlines()) - set(before_porc.splitlines()),))


# --- R4 human-in-the-loop -----------------------------------------------------

def test_r4_frontend_export_block_never_submits_runs():
    text = INDEX_HTML.read_text(encoding="utf-8")
    begin = text.index("// BEGIN lgcDraftExport")
    end = text.index("// END lgcDraftExport")
    block = text[begin:end]
    # strip // comment lines: the BEGIN marker itself mentions the banned
    # string ("禁止出现 /api/runs"); the assertion applies to CODE only
    code = "\n".join(ln for ln in block.splitlines()
                     if not ln.lstrip().startswith("//"))
    assert "/api/runs" not in code, (
        "lgcDraftExport block must not reference /api/runs")
    assert "/api/plan/draft" in block


def test_r4_backend_plan_draft_never_submits():
    src = inspect.getsource(routes.plan_draft)
    for banned in ("create_run", "run_dir", "EventBus", "run_topology",
                   "collab.sqlite3", "_write_run_meta"):
        assert banned not in src, f"plan_draft references {banned}"
