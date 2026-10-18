"""CLI entry: run / doctor / evolve (explicit subcommands, appendix A).

Examples:
    python main.py run "AI 技术调研报告" --topology supervisor --llm fake
    python main.py doctor
    python main.py evolve --mode auto     # one closed loop (criterion 12b)
"""

from __future__ import annotations

import argparse
import json
import sys

# C18: explicit UTF-8 console output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# D1 (round-6): apply the CrewAI storage redirect BEFORE any project module
# (and therefore any crewai import) — single implementation in
# core/crewai_storage.py, shared with server.py and tests/conftest.py.
from core.crewai_storage import redirect_crewai_storage

redirect_crewai_storage()


def _load_evolve_cfg() -> dict:
    import yaml

    from core.paths import configs_dir

    path = configs_dir() / "evolve.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def cmd_run(args) -> int:
    from core.llm import FakeLLM
    from graphs.runner import run_topology

    llm = None
    if args.llm == "fake":
        from graphs.runner import default_llm_script

        llm = FakeLLM(script=default_llm_script)
    elif args.llm == "real":
        from core.llm import OpenAICompatClient

        llm = OpenAICompatClient()  # raises ConfigError without key (R27)

    final = run_topology(args.topology, args.task, session_id=args.session_id,
                         llm=llm, persistent=True)
    print("=" * 60)
    print(f"状态: {final.get('status')}  会话: {final.get('session_id')}")
    print("-" * 60)
    print(final.get("final_report") or "(无终稿)")
    print("-" * 60)
    from core.paths import run_dir
    from inspector.report import load_health

    health = load_health(final["run_id"])
    if health:
        print(f"自检健康分: {health['score']} — {health['summary']}")
    print(f"产物目录: {run_dir(final['run_id'])}")
    return 0


def cmd_doctor(_args) -> int:
    from inspector.runner import run_doctor

    report = run_doctor()
    for c in report.checks:
        print(f"{c.status:5} {c.name:16} {c.details}")
    print(f"score: {report.score} | {report.summary}")
    # R17-W4c: registry health row — gated by AGENT_REGISTRY=1 (design §7 P1).
    # Gate off ⇒ NOTHING extra: the output stays byte-identical to the R15
    # doctor (the 9 checks and their scoring live in inspector.runner and are
    # untouched). Gate on ⇒ one extra line (PASS summary / FAIL with every
    # lint error); a FAIL forces exit 1 (fail-closed startup signal, C4) —
    # the registry is NOT wired to dispatch in P1.
    # W4c2 (rollback property): the env is checked BEFORE the import — reverting
    # P1 removes agent_bridge.registry entirely, and `main.py doctor` must keep
    # working in that state. The literal below MUST mirror
    # agent_bridge.registry.registry_gate() (single semantic gate; the import
    # itself cannot precede the check).
    import os
    if os.environ.get("AGENT_REGISTRY", "0") == "1":
        from agent_bridge.registry import doctor_registry_row
        status, messages = doctor_registry_row()
        print(f"registry: {status}")
        for message in messages:
            print(f"  {message}")
        if status == "FAIL":
            return 1
    return 0 if report.all_passed else 1


def cmd_dsh_check(args) -> int:
    """R16-D8/F3/F8: dsh ACP 接入自检。退出码按类别区分（供平台/CI 判别）：
    0 = 全部执行且通过；1 = 存在 FAIL（平台或 dsh 侧缺陷）；
    2 = 无 FAIL 但有 NOT-RUN（如基础档未跑真模型权限/取消往返——本次未验证，
    不得当作已验证）；3 = ENV-FAIL（环境不可用，如沙箱 ACL 缺陷）。"""
    import os

    from dsh_bridge.selfcheck import aggregate_exit, run_dsh_check

    dsh_bin = os.environ.get("DSH_BIN")
    expected = os.environ.get("DSH_EXPECTED_VERSION") or None
    rows, code = run_dsh_check(full=args.full,
                               dsh_bin_js=dsh_bin,
                               expected_agent_version=expected)
    for r in rows:
        print(f"{r.status:8} {r.name:24} {r.details}")
    if any(r.status == "NOT-RUN" for r in rows):
        print("NOT-RUN: 本次未验证权限/取消真机往返(--full, 消耗 token, 需授权)")
    if any(r.status == "ENV-FAIL" for r in rows):
        print("ENV-FAIL: 环境不可用 —— 修复环境后重跑;这与任务失败是两类问题")
    _, label = aggregate_exit(rows)
    print(f"dsh-check: {label}")
    return code


def _rollback_live(store, version: int, hashes: dict, project_root,
                   reason_label: str) -> None:
    """R14-2: byte-level restore of the snapshot into the live tree, with a
    VISIBLE verification line per file (sha256). A failure of the restore
    itself must be reported INDEPENDENTLY and prominently on stderr — it is
    never allowed to be masked by the original exception the caller
    re-raises after this helper returns."""
    import sys
    try:
        restored = store.restore(version, project_root)
        same = all(restored.get(rel) == h for rel, h in hashes.items())
        print(f"[auto] {reason_label};字节级还原校验: {'PASS' if same else 'FAIL'}")
        for rel, h in hashes.items():
            print(f"        {rel}: sha256={h}")
        if not same:
            print(f"[auto] 回滚校验 FAIL:还原后哈希与快照不一致,请人工核对 "
                  f"v{version:03d} 与 prompts/.versions 快照", file=sys.stderr)
    except BaseException as restore_exc:  # noqa: BLE001 — surface, never mask
        print(f"[auto] 回滚自身失败: {type(restore_exc).__name__}: {restore_exc};"
              f"live 文件可能仍处于候选态,请人工核对 v{version:03d} 与 "
              f"prompts/.versions 快照(异常回滚未完成!)", file=sys.stderr)
        sys.stderr.flush()


def cmd_evolve(args) -> int:
    from evolver.analyzer import analyze
    from evolver.golden import run_golden
    from evolver.guard import apply_candidate, validate_candidate
    from evolver.optimizer import propose_changes
    from evolver.versions import VersionStore, decide
    from inspector.report import load_health
    from core.paths import PROJECT_ROOT

    cfg = _load_evolve_cfg()
    mode = args.mode or cfg.get("mode", "propose")
    if mode == "off":
        print("evolve=off:自进化已整体关闭(§五)")
        return 0
    K = int(cfg.get("K", 3))

    print(f"[evolve] baseline: golden 集跑 {K} 轮…")
    baseline = run_golden(K=K, run_prefix="evolve-base")
    print(f"[evolve] baseline metrics: {baseline}")

    # gather weaknesses from the golden runs' health + events
    reports = []
    events_by_run: dict[str, list] = {}
    from core.paths import run_dir
    from core.schemas import Event
    from pathlib import Path

    for d in Path(run_dir("x")).parent.glob("evolve-base-*"):
        rid = d.name
        h = load_health(rid)
        if h:
            from core.schemas import HealthReport

            reports.append(HealthReport(**h))
        lines = (d / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        events_by_run[rid] = [Event(**json.loads(l)) for l in lines if l]
    weaknesses = analyze(reports, events_by_run)
    print(f"[evolve] weaknesses: {[ (w.kind, w.target_file) for w in weaknesses ]}")
    candidates = propose_changes(weaknesses, PROJECT_ROOT)
    if not candidates:
        print("[evolve] 本轮未发现可改进点,无候选改动")
        return 0

    store = VersionStore()
    for cand in candidates:
        version = store.next_version()
        if mode == "propose":
            # C8: propose only — record candidate + diff, apply nothing
            pend = store.base / "pending" / f"v{version:03d}"
            pend.mkdir(parents=True, exist_ok=True)
            (pend / "proposal.diff").write_text(cand.diff, encoding="utf-8")
            (pend / "proposal.json").write_text(json.dumps(
                {"file": cand.file, "reason": cand.reason,
                 "weakness_kinds": cand.weakness_kinds}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            print(f"[propose] 候选改动待审: v{version:03d} → {cand.file}(diff 见 {pend})")
            print(cand.diff)
            continue

        # auto: whitelist guard → snapshot → PENDING record → apply → compare →
        # final record → keep/rollback. R14-1: the provisional record is
        # decision="PENDING", upgraded exactly once by the final write below.
        # R14-2 (P0, incident 20260926): apply → golden → final-record →
        # verdict runs inside try/except — ANY exception byte-level-restores
        # the live files from the snapshot, verifies each sha256 visibly, and
        # re-raises the ORIGINAL exception loud (the R13 incident left live in
        # candidate state because the exception landed after apply_candidate
        # and before the decision==ROLLBACK-only restore).
        validate_candidate(cand.file, cand.new_content)
        hashes = store.snapshot(version, {cand.file: PROJECT_ROOT / cand.file})
        store.save_record(version, cand.reason, "PENDING", 0.0, hashes,
                          diff=cand.diff)  # provisional (R14-1)
        try:
            apply_candidate(cand.file, cand.new_content)
            print(f"[auto] 已应用候选 v{version:03d}({cand.file}),golden 复跑对比…")
            candidate = run_golden(K=K, run_prefix=f"evolve-cand{version}")
            delta = None
            from evolver.versions import composite_score

            delta = composite_score(candidate) - composite_score(baseline)
            decision = decide(baseline, candidate, float(cfg.get("threshold", 0.01)))
            print(f"[auto] candidate metrics: {candidate} | delta={delta:.6f} → {decision}")
            store.save_record(version, cand.reason, decision, delta, hashes, diff=cand.diff)
        except BaseException:
            _rollback_live(store, version, hashes, PROJECT_ROOT, "异常回滚")
            raise
        if decision == "ROLLBACK":
            _rollback_live(store, version, hashes, PROJECT_ROOT, "已回滚")
        else:
            print(f"[auto] 候选保留为新版本 v{version:03d}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="main.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="跑一次协同任务")
    p_run.add_argument("task")
    p_run.add_argument("--topology", default="supervisor",
                       choices=["single", "pipeline", "supervisor", "debate"])
    p_run.add_argument("--llm", default="auto", choices=["auto", "fake", "real"])
    p_run.add_argument("--session-id", default=None)
    p_run.set_defaults(fn=cmd_run)

    p_doc = sub.add_parser("doctor", help="系统体检(环境/依赖/配置/存储/冒烟)")
    p_doc.set_defaults(fn=cmd_doctor)

    p_dsh = sub.add_parser("dsh-check",
                           help="dsh ACP 接入自检(环境/握手/会话;--full 含真模型往返)")
    p_dsh.add_argument("--full", action="store_true",
                       help="包含真模型权限/取消往返(消耗 token,需委托方授权)")
    p_dsh.set_defaults(fn=cmd_dsh_check)

    p_evo = sub.add_parser("evolve", help="自进化闭环(propose 只出候选;auto 应用并对比)")
    p_evo.add_argument("--mode", default=None, choices=["propose", "auto", "off"])
    p_evo.set_defaults(fn=cmd_evolve)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
