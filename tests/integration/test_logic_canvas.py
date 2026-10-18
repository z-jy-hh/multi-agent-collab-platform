"""P1 工单2 (logic canvas) integration criteria C1-C6.

C1  /api/modes "topologies" edges/nodes == the SAME topology YAML the runner
    uses (canvas graph can never drift from the real topology).
C2  single-mode topology has exactly 1 node; frontend ships the canvas
    skeleton renderer and its empty-state reset path.
C3  every SSE event carries a strictly increasing seq (canvas taps the same
    stream via routeEvent).
C4  read/write boundary: annotations.json is created ONLY by
    POST /api/runs/{run_id}/annotations; validation rejects invalid run_id,
    unknown run, empty/overlong text; appends are append-only.
C6  index.html keeps every pre-existing DOM id and has 0 hits for the
    forbidden words (canvas must not smuggle in a supervisor-like rewrite).
(C5 mutation discriminator runs as a delivery-time probe, not a pytest:
 tests/tools/mut_c5_discriminator.py — mutating the skeleton source must
 turn C1 red, then the source is restored byte-identically.)
"""

import json
import shutil

import httpx
import pytest
import uvicorn
import yaml

from api.routes import get_app
from core.paths import PROJECT_ROOT

YAML_DIR = PROJECT_ROOT / "configs" / "topologies"


@pytest.fixture(scope="module")
def server():
    config = uvicorn.Config(get_app(), host="127.0.0.1", port=8131,
                            log_level="warning")  # 8127 taken by r15_bus_resilience
    srv = uvicorn.Server(config)
    import threading
    import time
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = "http://127.0.0.1:8131"
    for _ in range(50):
        try:
            httpx.get(base + "/api/modes", timeout=1.0)
            break
        except Exception:
            time.sleep(0.2)
    yield base
    srv.should_exit = True
    thread.join(timeout=5)


def _expected_from_yaml(name: str) -> dict:
    cfg = yaml.safe_load((YAML_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    return {
        "nodes": [{"id": n.get("id"), "kind": n.get("kind"),
                   "agent": n.get("agent")} for n in cfg.get("nodes", [])],
        "edges": [{"from": e.get("from"), "to": e.get("to"),
                   "conditional": bool(e.get("conditional")),
                   "rule": e.get("rule")} for e in cfg.get("edges", [])],
    }


def test_c1_topologies_match_yaml(server):
    """C1: served skeleton == yaml parse result, for all four modes."""
    data = httpx.get(server + "/api/modes", timeout=5).json()
    assert data["modes"] == ["debate", "pipeline", "single", "supervisor"]
    for name in data["modes"]:
        assert data["topologies"][name] == _expected_from_yaml(name), name


def test_c2_single_mode_single_node_and_frontend_hooks(server):
    """C2: single topology = 1 node; frontend has renderer + empty-state."""
    data = httpx.get(server + "/api/modes", timeout=5).json()
    single = data["topologies"]["single"]
    assert len(single["nodes"]) == 1
    html = (PROJECT_ROOT / "api" / "static" / "index.html").read_text(
        encoding="utf-8")
    for marker in ("lgc-svg", "lgcReset", "lgcHandleEvent", "lgcSetTopos",
                   "lgc-section"):
        assert marker in html, marker


def test_c3_sse_seq_monotonic(server):
    """C3: every SSE event carries a strictly increasing seq (canvas taps
    the same stream via routeEvent, so it inherits this guarantee)."""
    r = httpx.post(server + "/api/runs",
                   json={"mode": "single", "task": "c3 seq probe",
                         "llm": "fake"}, timeout=10)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    seqs: list[int] = []
    with httpx.stream("GET", f"{server}/api/runs/{run_id}/events",
                      timeout=60) as resp:
        for line in resp.iter_lines():
            if line.startswith("data:") and '"seq"' in line:
                try:
                    payload = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(payload, dict) and "seq" in payload:
                    seqs.append(payload["seq"])
            if line.startswith("data:") and "run_finished" in line:
                break
    assert seqs, "no sequenced SSE events observed"
    assert all(b > a for a, b in zip(seqs, seqs[1:])), seqs


def test_c4_annotations_boundary(server):
    """C4: only POST writes annotations.json; validation is enforced."""
    from core.paths import run_dir

    probe_id = "p1c4_probe_20261010"
    rd = run_dir(probe_id)
    rd.mkdir(parents=True, exist_ok=True)
    ann = rd / "annotations.json"
    try:
        assert not ann.exists()
        base = server + f"/api/runs/{probe_id}/annotations"
        r = httpx.post(base, json={"author": "wb", "text": "c4 probe",
                                   "anchor": None}, timeout=5)
        assert r.status_code == 200
        assert r.json()["annotations_count"] == 1
        assert r.json()["appended"]["anchor"] == ""  # null anchor -> ""
        r2 = httpx.post(base, json={"author": "wb", "text": "second"},
                        timeout=5)
        assert r2.status_code == 200
        assert r2.json()["annotations_count"] == 2
        items = json.loads(ann.read_text(encoding="utf-8"))
        assert [i["text"] for i in items] == ["c4 probe", "second"]
        # read endpoints must not write: GET replay leaves the file as-is
        n_before = ann.stat().st_size
        httpx.get(server + f"/api/runs/{probe_id}/replay", timeout=5)
        assert ann.stat().st_size == n_before
        # validation
        assert httpx.post(base, json={"text": ""}).status_code == 422
        assert httpx.post(base, json={"text": "x" * 2001}).status_code == 422
        assert httpx.post(server + "/api/runs/unknown_run_xyz/annotations",
                          json={"text": "t"}).status_code == 404
        for bad in ("..", "../evil", "a\\b", "a/b", "."):
            assert httpx.post(
                server + f"/api/runs/{bad}/annotations",
                json={"text": "t"}).status_code in (404, 422), bad
    finally:
        shutil.rmtree(rd, ignore_errors=True)


def test_c4b_annotations_are_masked(server):
    """C4b (DSH review finding F1): host annotation text is free text, so it
    must go through the SAME pre-persistence redaction as every other artifact.
    Otherwise the only write endpoint becomes the platform's only unmasked
    persisted free-text channel (N2 invariant)."""
    from core.paths import run_dir

    probe_id = "p1c4b_mask_20261010"
    rd = run_dir(probe_id)
    rd.mkdir(parents=True, exist_ok=True)
    ann = rd / "annotations.json"
    secret, mail, phone = ("sk-abcdefghijklmnopqrstuvwx", "a.b@c.com",
                           "13800138000")
    try:
        base = server + f"/api/runs/{probe_id}/annotations"
        r = httpx.post(base, json={"author": "wb",
                                   "text": f"note {secret} mail={mail} "
                                           f"phone={phone}",
                                   "anchor": "node:plan"}, timeout=5)
        assert r.status_code == 200, r.text
        body = ann.read_text(encoding="utf-8")
        assert secret not in body, "raw key must never land"
        assert mail not in body, "raw email must never land"
        assert phone not in body, "raw phone must never land"
        assert "REDACTED" in body, "mask form expected"
        assert json.loads(body)[-1]["redaction_rules"], "rules version recorded"
        # the response echoes the masked entry, never the raw text
        assert secret not in r.text and mail not in r.text
    finally:
        shutil.rmtree(rd, ignore_errors=True)


def test_c4c_annotation_count_cap(server):
    """F5: per-run cap 500 — the 500th entry returns 200, the 501st 422 and
    the file stays at 500; the echoed `appended` equals the on-disk entry.
    (Pre-seeds 499 entries directly on disk: the endpoint's contract is
    'read existing array then append', so the boundary test exercises the
    real code path without 500 sequential O(n^2) HTTP posts.)"""
    from core.paths import run_dir

    probe_id = "p1c4c_cap_probe_20261011"
    rd = run_dir(probe_id)
    rd.mkdir(parents=True, exist_ok=True)
    ann = rd / "annotations.json"
    base = server + f"/api/runs/{probe_id}/annotations"
    try:
        seed = [{"ts": 1000.0 + i, "author": "seed", "text": f"seed {i}",
                 "anchor": "", "redaction_rules": "seed"} for i in range(499)]
        ann.write_text(json.dumps(seed, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        r = httpx.post(base, json={"author": "wb", "text": "entry 500"},
                       timeout=10)
        assert r.status_code == 200, r.status_code
        first_entry = r.json()["appended"]
        assert len(json.loads(ann.read_text(encoding="utf-8"))) == 500
        # 501st must be refused, file untouched
        r501 = httpx.post(base, json={"author": "wb", "text": "over cap"},
                          timeout=10)
        assert r501.status_code == 422, r501.status_code
        items = json.loads(ann.read_text(encoding="utf-8"))
        assert len(items) == 500, len(items)
        assert items[499]["text"] == "entry 500"
        # echoed form == on-disk form (same fields, same value)
        assert set(first_entry.keys()) == set(items[499].keys())
        assert first_entry["text"] == items[499]["text"]
        assert first_entry["ts"] == items[499]["ts"]
    finally:
        shutil.rmtree(rd, ignore_errors=True)


def test_c6_dom_ids_and_forbidden_words(server):
    """C6: pre-existing DOM ids intact; forbidden words 0 hits."""
    html = (PROJECT_ROOT / "api" / "static" / "index.html").read_text(
        encoding="utf-8")
    for dom_id in ("canvas-wrap", "canvas", "canvas-empty", "btn-run",
                   "btn-cancel", "errbar", "history-panel", "conv-section"):
        assert f'id="{dom_id}"' in html, dom_id
    low = html.lower()
    for word in ("supervisor", "codebuddy", "subprocess"):
        assert low.count(word) == 0, word
    # every lgc DOM id referenced by JS exists in the HTML
    import re
    refs = set(re.findall(r'\$\("lgc-[a-z0-9-]+"\)', html))
    for ref in refs:
        dom_id = ref[3:-2]
        assert f'id="{dom_id}"' in html, dom_id
