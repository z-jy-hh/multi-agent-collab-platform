#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R17 ACCT1 three-ledger machine checker (ledgercheck).

Checks (workorder cd52cd325f8fb4bf, A1/A2/A3 + Appendix A):
  1. criteria  : docs/criteria_ledger.md module-default + function-exception
                 mapping vs `pytest --collect-only` (node-level exact).
                 0 unassigned / 0 ghost; bucket counts; count drift = WARN.
  2. endpoints : docs/endpoint_authority.md vs `@app.` decorators in
                 api/routes.py, exact key (method, path, handler).
                 0 unregistered / 0 ghost; every row must carry approved_by.
  3. index     : docs/order_report_index.md -- every reports/*.md path must
                 exist; every out-of-repo order file must exist and its
                 sha256_16 must match the registered value.
                 ACCT2 (workorder 09a74ff3b264f916): five-value reviewed enum
                 (yes|partial|no|unknown|n/a-legacy); yes AND partial both
                 need object evidence; partial additionally needs EXACTLY ONE
                 `不可证理由：<non-empty>` in the conclusion cell; unknown rows
                 must NOT carry that prefix. All hard failures (no WARN).

Usage (repo root or anywhere; repo resolved from __file__):
  ./.venv/Scripts/python.exe tests/tools/ledgercheck.py            # all checks
  ... tests/tools/ledgercheck.py --only criteria|endpoints|index
  ... tests/tools/ledgercheck.py --selftest                        # inline self-test

Note: this file's name does not match pytest's default `test_*.py` discovery,
so its self-test functions are NOT collected by the full suite. Run them via
--selftest (or explicit path). This is declared in docs/criteria_ledger.md S0.
"""
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "docs" / "criteria_ledger.md"
AUTHORITY = REPO / "docs" / "endpoint_authority.md"
INDEX = REPO / "docs" / "order_report_index.md"
ROUTES = REPO / "api" / "routes.py"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _read(p):
    return p.read_text(encoding="utf-8")


def _sha16(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _norm(p):
    return p.replace("\\", "/")


# ---------------------------------------------------------------- check 1 --
def collect_cases():
    """Return (cases, nodes): cases = list of (module, node, param_or_None)."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=600)
    lines = r.stdout.splitlines()
    cases = []
    pat = re.compile(r"^(tests/.+?\.py)::(.+?)(\[.*)?$")
    for ln in lines:
        ln = _norm(ln.strip())
        m = pat.match(ln)
        if m:
            cases.append((m.group(1), m.group(2), bool(m.group(3))))
    if not cases:
        raise RuntimeError("collect-only returned 0 parsed cases; "
                           "pytest rc=%d; tail: %s" % (r.returncode, lines[-5:]))
    return cases


def parse_groups(text):
    """Parse §1 group table: {group_id: (bucket, declared_count)}.
    Skips the header/separator and any non-group row (declared ids are
    ALLCAPS identifiers, not file paths)."""
    groups = {}
    in_groups = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("## 1"):
            in_groups = True
            continue
        if in_groups and s.startswith("## "):
            break
        if not in_groups or not s.startswith("|"):
            continue
        m = re.match(r"^\|\s*([A-Z][A-Z0-9_-]+)\s*\|\s*[^|]+\|\s*"
                     r"(named|infra)\s*\|\s*(\d+)\s*\|", s)
        if m:
            groups[m.group(1)] = (m.group(2), int(m.group(3)))
    return groups


def parse_ledger(text):
    """Return (module_rows, exception_keys). module_rows: {path: (group, bucket, nf, nc)}"""
    module_rows, exceptions = {}, {}
    in_exceptions = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("## 3"):
            in_exceptions = True
        elif s.startswith("## "):
            in_exceptions = False
        if in_exceptions:
            m = re.match(r"^\|\s*`(tests/[^`]+)`\s*\|\s*([A-Za-z0-9_-]+)\s*\|", s)
            if m and m.group(1) != "（空——无跨组模块）":
                exceptions[(m.group(1), m.group(2))] = m.group(3)
            continue
        m = re.match(r"^\|\s*`(tests/[^`]+)`\s*\|\s*([A-Za-z0-9_-]+)\s*\|\s*"
                     r"(named|infra)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", s)
        if m:
            module_rows[m.group(1)] = (m.group(2), m.group(3),
                                       int(m.group(4)), int(m.group(5)))
    return module_rows, exceptions


def check_criteria(verbose=True, ledger_text=None, cases=None):
    if cases is None:
        cases = collect_cases()
    if ledger_text is None:
        ledger_text = _read(LEDGER)
    module_rows, exceptions = parse_ledger(ledger_text)
    groups = parse_groups(ledger_text)
    problems, warns = [], []

    node_set = {(mod, node) for mod, node, _ in cases}
    unassigned = sorted({(mod, node) for mod, node, _ in cases
                         if mod not in module_rows
                         and (mod, node) not in exceptions})
    ghosts = sorted(k for k in exceptions if k not in node_set)
    missing_files = [p for p in module_rows if not (REPO / p).exists()]

    bucket_nodes = {"named": set(), "infra": set()}
    drift = []
    per_mod = {}
    for mod, node, _ in cases:
        per_mod.setdefault(mod, [set(), 0])
        per_mod[mod][0].add(node)
        per_mod[mod][1] += 1
    for mod, (grp, bucket, nf, nc) in module_rows.items():
        cur_f, cur_c = per_mod.get(mod, [set(), 0])
        bucket_nodes[bucket].update((mod, n) for n in cur_f)
        if cur_f and (len(cur_f) != nf or cur_c != nc):
            drift.append("  DRIFT %s ledger(f=%d,c=%d) now(f=%d,c=%d)"
                         % (mod, nf, nc, len(cur_f), cur_c))

    tot_nodes = len(node_set)
    named_n, infra_n = len(bucket_nodes["named"]), len(bucket_nodes["infra"])

    # A9 (F-1, workorder a7cd4654648d2bd5): three-way ledger reconciliation —
    # group-table sum == module-table sum == collect unique nodes; per-group
    # declared vs module-sum comparison. Any inequality => HARD red (the old
    # "0 unassigned / 0 ghost" cannot see this dimension).
    per_group_sum = {}
    for mod, (grp, bucket, nf, nc) in module_rows.items():
        per_group_sum[grp] = per_group_sum.get(grp, 0) + nf
    module_sum = sum(nf for (_g, _b, nf, _c) in module_rows.values())
    group_sum = sum(declared for (_b, declared) in groups.values())
    a9 = []
    if group_sum != module_sum:
        a9.append("A9 group-table sum (%d) != module-table sum (%d)"
                  % (group_sum, module_sum))
    if module_sum != tot_nodes:
        a9.append("A9 module-table sum (%d) != collect unique nodes (%d)"
                  % (module_sum, tot_nodes))
    for gid, (bucket, declared) in sorted(groups.items()):
        ms = per_group_sum.get(gid, 0)
        if ms != declared:
            a9.append("A9 group %s: declared=%d module-sum=%d"
                      % (gid, declared, ms))

    ok = (not unassigned and not ghosts and not missing_files and not a9)
    if verbose:
        print("[criteria] collected cases=%d unique-nodes=%d modules-collected=%d"
              % (len(cases), tot_nodes, len(per_mod)))
        print("[criteria] ledger module-rows=%d explicit-exceptions=%d"
              % (len(module_rows), len(exceptions)))
        print("[criteria] unassigned=%d ghost=%d missing-module-files=%d"
              % (len(unassigned), len(ghosts), len(missing_files)))
        print("[criteria] buckets: named=%d(%.1f%%) infra=%d(%.1f%%)"
              % (named_n, 100.0 * named_n / max(tot_nodes, 1),
                 infra_n, 100.0 * infra_n / max(tot_nodes, 1)))
        print("[criteria] A9 three-way: group-sum=%d module-sum=%d "
              "collect-nodes=%d -> %s"
              % (group_sum, module_sum, tot_nodes,
                 "OK" if not a9 else "MISMATCH"))
        for u in unassigned[:20]:
            print("  UNASSIGNED %s::%s" % u)
        for g in ghosts[:20]:
            print("  GHOST %s::%s" % g)
        for f in missing_files:
            print("  MISSING-FILE %s" % f)
        for a in a9:
            print("  " + a)
        for d in drift:
            print(d)
        if drift:
            print("[criteria] NOTE: count drift is WARN-only (ledger rows are "
                  "point-in-time registrations; append-only discipline).")
    if unassigned:
        problems.append("criteria: %d unassigned" % len(unassigned))
    if ghosts:
        problems.append("criteria: %d ghost" % len(ghosts))
    if missing_files:
        problems.append("criteria: %d missing module files" % len(missing_files))
    problems.extend(a9)
    return ok, problems, warns + drift


# ---------------------------------------------------------------- check 2 --
ROUTE_RE = re.compile(r'@app\.(get|post)\(\s*"([^"]+)"\s*\)\s*\n\s*def\s+(\w+)\s*\(')


def parse_authority(text):
    rows = {}
    for ln in text.splitlines():
        s = ln.strip()
        m = re.match(r"^\|\s*(\d+)\s*\|\s*(GET|POST)\s*\|\s*([^|]+?)\s*\|\s*"
                     r"`?(\w+)`?\s*\|", s)
        if m:
            cells = [c.strip() for c in s.split("|")][1:-1]
            approved = cells[9] if len(cells) >= 11 else ""
            rows[(m.group(2).upper(),
                  m.group(3).strip().strip("`").strip(),
                  m.group(4))] = approved
    return rows


def check_endpoints(verbose=True):
    src = _read(ROUTES)
    code = {(m.upper(), p, h) for m, p, h in ROUTE_RE.findall(src)}
    table = parse_authority(_read(AUTHORITY))
    problems = []
    unregistered = sorted(code - set(table))
    ghosts = sorted(set(table) - code)
    no_approve = sorted(k for k, v in table.items() if not v)
    ok = not unregistered and not ghosts and not no_approve
    if verbose:
        print("[endpoints] routes.py @app-routes=%d authority-rows=%d "
              "(POST=%d GET=%d)"
              % (len(code), len(table),
                 sum(1 for k in code if k[0] == "POST"),
                 sum(1 for k in code if k[0] == "GET")))
        print("[endpoints] unregistered=%d ghost=%d rows-without-approved_by=%d"
              % (len(unregistered), len(ghosts), len(no_approve)))
        for k in unregistered:
            print("  UNREGISTERED %s %s -> %s" % k)
        for k in ghosts:
            print("  GHOST %s %s -> %s" % k)
        for k in no_approve:
            print("  NO-APPROVED_BY %s %s -> %s" % k)
    if unregistered:
        problems.append("endpoints: %d unregistered" % len(unregistered))
    if ghosts:
        problems.append("endpoints: %d ghost" % len(ghosts))
    if no_approve:
        problems.append("endpoints: %d rows without approved_by" % len(no_approve))
    return ok, problems, []


# ---------------------------------------------------------------- check 3 --
REPORT_RE = re.compile(r"reports/[\w.\-/]+\.(?:md|MD)")
HEX16 = re.compile(r"^[0-9a-f]{16}$")

# B-8/F-5 (workorder 3086eb4d86ad5a96): ratchet baseline — the set of index
# row ids that were `reviewed=yes` WITHOUT object evidence at freeze time.
# All 26 were backfilled with the commit hashes their review files cite in
# the same change, so the tolerated set is now empty in practice; the
# baseline is frozen so any NEW row reproducing yes-without-evidence is a
# HARD failure (never a downgrade of the gate).
F5_RATCHET_BASELINE = frozenset({
    "A1", "A2", "A3", "A4", "A5", "A6", "B1", "B2",
    "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11",
    "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9",
})
F5_BASELINE_COUNT = 26


def f5_classify(yes_no_evidence_ids):
    """Split yes-without-evidence row ids into (new_violations, legacy_still).
    new_violations => hard problems; legacy_still => tolerated WARNs."""
    new_violations = sorted(r for r in yes_no_evidence_ids
                            if r not in F5_RATCHET_BASELINE)
    legacy_still = sorted(r for r in yes_no_evidence_ids
                          if r in F5_RATCHET_BASELINE)
    return new_violations, legacy_still


def _winpath(s):
    """Accept D:\\x\\y or /d/x/y forms; return existing Path or None."""
    s = s.strip().strip("`").strip()
    if re.match(r"^[A-Za-z]:[\\/]", s):
        p = Path(s)
        return p if p.exists() else None
    if re.match(r"^/[a-zA-Z]/", s):
        p = Path(s[1].upper() + ":/" + s[3:])
        return p if p.exists() else None
    return None


def check_index(verbose=True, index_text=None):
    problems, warns = [], []
    n_rows = n_report_refs = n_order_files = 0
    yes_no_evidence = []
    for ln in (index_text if index_text is not None
               else _read(INDEX)).splitlines():
        s = ln.strip()
        if not s.startswith("|") or s.startswith("|---") or s.startswith("| 行号"):
            continue
        cells = [c.strip() for c in s.split("|")][1:-1]
        if len(cells) != 9 or not re.match(r"^[A-E]\d+$", cells[0]):
            continue
        n_rows += 1
        order_cell, sha_cell = cells[1], cells[2]
        # only an absolute-path cell (D:\... or /d/...) is an anchored order
        # file; cells like "--(design-inline order: docs/... S6)" or commit
        # refs are informational and skipped (declared in the index header).
        bt = re.search(r"`([^`]+)`", order_cell)
        cand = bt.group(1) if bt else order_cell.strip("`")
        cand = cand.strip()
        order_plain = cand if re.match(r"^[A-Za-z]:[\\/]", cand) else ""
        if order_plain:
            p = _winpath(order_plain)
            n_order_files += 1
            if p is None:
                problems.append("index %s: order file not found: %s"
                                % (cells[0], order_plain))
            else:
                sha = sha_cell.strip("`")
                if HEX16.match(sha):
                    actual = _sha16(p)
                    if actual != sha:
                        problems.append("index %s: sha mismatch %s: "
                                        "registered=%s actual=%s"
                                        % (cells[0], p.name, sha, actual))
                elif not sha.startswith("—"):
                    problems.append("index %s: bad sha cell: %s"
                                    % (cells[0], sha))
        if cells[6] not in ("yes", "partial", "no", "unknown", "n/a-legacy"):
            problems.append("index %s: reviewed must be yes|partial|no|unknown|"
                            "n/a-legacy (ACCT2 five-value = B-5 three-state + "
                            "B-8 legacy downgrade + partial), got %r"
                            % (cells[0], cells[6]))
        elif cells[6] == "yes":
            # B-5: every yes row must carry OBJECT evidence (the commit hash
            # cited by the review) — pairing must not rely on the file name.
            # B-8/F-5: a yes row WITHOUT evidence is a ratchet violation —
            # hard if the row id is not in the frozen baseline, tolerated
            # WARN only for baseline members (legacy, being dispositioned).
            if not re.search(r"\b[0-9a-f]{7,40}\b", cells[4] + " " + cells[5]):
                yes_no_evidence.append(cells[0])
        elif cells[6] == "partial":
            # ACCT2 (workorder 09a74ff3b264f916, A-1): partial = the audited
            # object exists with a review file, but the delivery chain or the
            # commit anchors are not provable. All three requirements are
            # HARD failures (never WARN):
            #  (a) object evidence: >=1 hash anchor in review+conclusion
            #      cells (invalidated anchors still count as references);
            #  (b) conclusion cell contains EXACTLY ONE `不可证理由：` prefix
            #      with non-empty text after it (0 or >=2 => hard fail);
            #  (c) review cell carries a reports/ path (existence enforced
            #      by the path-ref scan below).
            if not re.search(r"\b[0-9a-f]{7,40}\b", cells[4] + " " + cells[5]):
                problems.append("index %s: partial row without object-evidence "
                                "hash anchor (A-1: >=1 anchor required, "
                                "invalidated anchors count)" % cells[0])
            n_reason = cells[5].count("不可证理由：")
            tail = cells[5].split("不可证理由：")[-1].strip() if n_reason else ""
            if n_reason != 1 or not tail:
                problems.append("index %s: partial conclusion cell must contain "
                                "EXACTLY ONE `不可证理由：<non-empty>` (A-1), "
                                "found %d occurrence(s), non-empty tail=%s"
                                % (cells[0], n_reason, bool(tail)))
            if not REPORT_RE.search(cells[4]):
                problems.append("index %s: partial row without a review-piece "
                                "path in the review cell (A-1)" % cells[0])
        elif cells[6] == "unknown" and "不可证理由：" in s:
            problems.append("index %s: unknown row must NOT contain the "
                            "`不可证理由：` prefix (A-1 anti-state-crossing)"
                            % cells[0])
        elif cells[6] == "n/a-legacy" and "降级" not in cells[5]:
            warns.append("index %s: n/a-legacy row without downgrade reason "
                         "(B-8 requires the reason in the conclusion cell)"
                         % cells[0])
        for col, name in ((3, "report"), (4, "review")):
            for ref in REPORT_RE.findall(cells[col]):
                n_report_refs += 1
                if not (REPO / ref).exists():
                    problems.append("index %s: %s not found: %s"
                                    % (cells[0], name, ref))
    # B-8/F-5 ratchet (hard gate): new yes-without-evidence rows fail;
    # frozen baseline members are tolerated as WARNs only.
    new_violations, legacy_still = f5_classify(yes_no_evidence)
    for r in new_violations:
        problems.append("index %s: F-5 RATCHET VIOLATION — reviewed=yes "
                        "without object evidence and row id not in the "
                        "frozen baseline" % r)
    for r in legacy_still:
        warns.append("index %s: reviewed=yes without object evidence "
                     "(F-5 frozen baseline member — being dispositioned)"
                     % r)
    ratchet_margin = F5_BASELINE_COUNT - len(legacy_still)
    ok = not problems
    if verbose:
        print("[index] rows=%d report/review path-refs=%d order-files-hashed=%d"
              % (n_rows, n_report_refs, n_order_files))
        print("[index] F-5 ratchet: baseline=%d legacy-still-violating=%d "
              "new-violations=%d margin(已收紧行数)=%d"
              % (F5_BASELINE_COUNT, len(legacy_still), len(new_violations),
                 ratchet_margin))
        print("[index] problems=%d yes-rows-without-evidence=%d"
              % (len(problems), len(yes_no_evidence)))
        for p in problems:
            print("  " + p)
        for w in warns:
            print("  WARN " + w)
    if problems:
        problems.insert(0, "index: %d problem(s)" % len(problems))
    return ok, problems, warns


# -------------------------------------------------------------- emit summary --
def emit_summary():
    """F-1: generate the §1 bucket-count line + per-group reconciliation
    table from the MODULE table (replaces hand-written totals — paste the
    output into docs/criteria_ledger.md §1)."""
    text = _read(LEDGER)
    module_rows, _ = parse_ledger(text)
    groups = parse_groups(text)
    per_bucket = {"named": 0, "infra": 0}
    per_group_sum = {}
    for mod, (grp, bucket, nf, nc) in module_rows.items():
        per_bucket[bucket] += nf
        per_group_sum[grp] = per_group_sum.get(grp, 0) + nf
    named_n, infra_n = per_bucket["named"], per_bucket["infra"]
    total = named_n + infra_n
    print("**桶计数（由 `ledgercheck.py --emit-summary` 生成；模块表求和；禁止手写）**："
          "named = %d 节点（%.1f%%）/ infra = %d 节点（%.1f%%）；合计 %d。"
          % (named_n, 100.0 * named_n / max(total, 1),
             infra_n, 100.0 * infra_n / max(total, 1), total))
    print()
    print("| 组 ID | 登记数 | 模块表求和 | 一致 |")
    print("|---|---|---|---|")
    for gid, (bucket, declared) in sorted(groups.items()):
        ms = per_group_sum.get(gid, 0)
        print("| %s | %d | %d | %s |"
              % (gid, declared, ms, "Y" if ms == declared else "**N**"))


# -------------------------------------------------------------- self test --
def test_ledgercheck_criteria_zero_unassigned_zero_ghost():
    ok, problems, _ = check_criteria(verbose=False)
    assert ok, problems


def test_ledgercheck_endpoints_bidirectional_zero():
    ok, problems, _ = check_endpoints(verbose=False)
    assert ok, problems


def test_ledgercheck_index_paths_and_hashes_consistent():
    ok, problems, _ = check_index(verbose=False)
    assert ok, problems


def test_ledgercheck_selftest_format_drift_is_loud():
    """F-4 (workorder a7cd4654648d2bd5): a deliberately corrupted table
    shape must be LOUD — never silently skipped."""
    cases = collect_cases()
    text = _read(LEDGER)
    # drift A: group declared count off by one -> A9 must go red
    m = re.search(r"\|\s*M-SPI-BACKEND\s*\|[^|]+\|\s*(named|infra)\s*\|"
                  r"\s*(\d+)\s*\|", text)
    assert m, "group row for M-SPI-BACKEND not found"
    corrupted = text[:m.start(2)] + str(int(m.group(2)) + 1) + text[m.end(2):]
    ok, problems, _ = check_criteria(verbose=False, ledger_text=corrupted,
                                     cases=cases)
    assert not ok, "drift A (group count +1) must be loud"
    assert any(p.startswith("A9") for p in problems), problems
    # drift B: a module row loses a pipe -> row unparsable -> unassigned
    anchor = "`tests/unit/test_resume.py`"
    i = text.find(anchor)
    assert i > 0, "anchor module row not found"
    j = text.find("|", i + len(anchor))
    corrupted2 = text[:j] + " " + text[j + 1:]
    ok2, problems2, _ = check_criteria(verbose=False, ledger_text=corrupted2,
                                       cases=cases)
    assert not ok2, "drift B (broken module row) must be loud"
    assert any("unassigned" in p for p in problems2), problems2


def test_ledgercheck_f5_ratchet_is_hard_for_new_rows():
    """B-8/F-5 (workorder 3086eb4d86ad5a96): a NEW reviewed=yes row without
    object evidence is a HARD problem (ratchet); a frozen-baseline member is
    only a tolerated WARN."""
    text = _read(INDEX)
    # baseline semantics (pure function)
    new_v, legacy = f5_classify(["A1", "ZZ9"])
    assert new_v == ["ZZ9"] and legacy == ["A1"]
    # A8 (NOT in baseline): flip no -> yes without evidence => HARD fail
    anchor = "本令交付（待 DSH 复核） | no |"
    assert text.count(anchor) == 1, "A8 anchor not unique"
    flipped = text.replace(anchor, "本令交付（待 DSH 复核） | yes |")
    ok, problems, _ = check_index(verbose=False, index_text=flipped)
    assert not ok, "new yes-without-evidence row must be a hard failure"
    assert any("F-5 RATCHET" in p for p in problems), problems
    # A1 (IN baseline, currently has evidence): strip its evidence =>
    # baseline member violating => tolerated WARN only, never a hard fail
    anchor2 = ("（**对象证据**（F-5 回填）：复核基线 HEAD `917ea11`，"
               "期初事故链 `09ae152`→`337a246`→`20d6223` 见件内）")
    assert text.count(anchor2) == 1, "A1 evidence anchor not unique"
    flipped2 = text.replace(anchor2, "（自测抹除）")
    ok2, _problems2, warns2 = check_index(verbose=False, index_text=flipped2)
    assert ok2, "baseline member must stay tolerated (WARN only), got %s" % _problems2
    assert any("F-5" in w for w in warns2), warns2


def test_ledgercheck_partial_reason_is_exactly_once():
    """ACCT2 P5① (workorder 09a74ff3b264f916): wiping the partial row's
    `不可证理由：` => HARD red; duplicating it (>=2) => HARD red."""
    text = _read(INDEX)
    anchor = "不可证理由：交付链（W1/W2/W3 顺序与作者）"
    assert text.count(anchor) == 1, "C1 partial reason anchor not unique"
    reason_start = text.index(anchor)
    # wipe: replace the whole reason sentence (up to the reviewed cell end)
    # with a placeholder -> 0 occurrences => hard fail
    cell_end = text.index(" | partial", reason_start)
    wiped = text[:reason_start] + "（自测抹除）" + text[cell_end:]
    ok, problems, _ = check_index(verbose=False, index_text=wiped)
    assert not ok, "partial row with 0 reason must be a hard failure"
    assert any("EXACTLY ONE" in p for p in problems), problems
    # duplicate: append a second `不可证理由：` in the conclusion cell
    dup = text.replace(anchor, "不可证理由：重复 " + anchor)
    ok2, problems2, _ = check_index(verbose=False, index_text=dup)
    assert not ok2, "partial row with 2 reasons must be a hard failure"
    assert any("EXACTLY ONE" in p for p in problems2), problems2


def test_ledgercheck_partial_needs_evidence_and_unknown_forbids_prefix():
    """ACCT2 P5② + A-1 (workorder 09a74ff3b264f916): stripping object
    evidence from a yes row OUTSIDE the ratchet baseline => HARD red;
    an unknown row carrying the `不可证理由：` prefix => HARD red."""
    text = _read(INDEX)
    # P5②: C2 is a yes row NOT in F5_RATCHET_BASELINE; strip its evidence
    # => yes-without-evidence outside baseline => ratchet hard failure
    anchor = ('（**对象证据**：对象行原文"P0（P5 阻塞项修复 F1–F6）"文本吻合；'
              "提交 `1384921` + `be98190`）")
    assert text.count(anchor) == 1, "C2 evidence anchor not unique"
    wiped = text.replace(anchor, "（自测抹除）")
    ok, problems, _ = check_index(verbose=False, index_text=wiped)
    assert not ok, "yes row outside baseline without evidence must be hard"
    assert any("F-5 RATCHET" in p and "C2" in p for p in problems), problems
    # A-1: unknown row (E1) must NOT carry the partial reason prefix
    anchor2 = "设计可开工（P0 放行） | unknown |"
    assert text.count(anchor2) == 1, "E1 unknown anchor not unique"
    crossed = text.replace(anchor2,
                           "设计可开工（P0 放行）不可证理由：x | unknown |")
    ok2, problems2, _ = check_index(verbose=False, index_text=crossed)
    assert not ok2, "unknown row with the partial prefix must be a hard failure"
    assert any("must NOT contain" in p for p in problems2), problems2


def _selftest():
    fns = [test_ledgercheck_criteria_zero_unassigned_zero_ghost,
           test_ledgercheck_endpoints_bidirectional_zero,
           test_ledgercheck_index_paths_and_hashes_consistent,
           test_ledgercheck_selftest_format_drift_is_loud,
           test_ledgercheck_f5_ratchet_is_hard_for_new_rows,
           test_ledgercheck_partial_reason_is_exactly_once,
           test_ledgercheck_partial_needs_evidence_and_unknown_forbids_prefix]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("SELFTEST PASS %s" % fn.__name__)
        except AssertionError as e:
            failed += 1
            print("SELFTEST FAIL %s: %s" % (fn.__name__, e))
    return failed


def main(argv):
    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    if "--emit-summary" in argv:
        emit_summary()
        return 0
    if "--selftest" in argv:
        return 1 if _selftest() else 0
    checks = {"criteria": check_criteria, "endpoints": check_endpoints,
              "index": check_index}
    names = [only] if only else list(checks)
    all_problems, all_ok = [], True
    for n in names:
        ok, problems, _ = checks[n](verbose=True)
        all_ok &= ok
        all_problems += problems
    print("LEDGERCHECK SUMMARY: %s" % ("PASS" if all_ok else "FAIL"))
    for p in all_problems:
        print("  PROBLEM: " + p)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
