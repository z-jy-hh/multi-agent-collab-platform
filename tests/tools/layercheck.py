"""Trimmed-environment layering check (task book 三·D10 / U2 / v3.1 R22).

Proves "core has zero upper-layer dependencies and is reusable standalone":

1. Copy ``core/`` + ``tests/`` (+ ``evolver/`` and ``crews/`` when present and
   ``--scope unit``) into a fresh temp dir — upper layers do NOT exist there.
2. ``PYTHONPATH=<copy>``, ``cwd=<copy>``.
3. Step 1 (``--import-all``, mandatory per R22): import ``core`` and every
   core submodule in the trimmed env. A core module that couples to an upper
   layer fails here even when no test imports it.
4. Step 2: run pytest on the copied tree; must be green.

Both steps must exit 0. Subprocess output goes to FILES (never pipes) so the
raw stdout can be attached to batch reports verbatim.

Scope notes (documented deviation, see G0/G1 reports 未决清单):
- both scopes ignore ``tests/integration`` — those exercise the full stack and
  belong to D8/D9 in the real environment, not to the layering contract;
- ``--scope core`` additionally ignores ``tests/unit/evolver`` (the evolver
  package is not copied in this scope), otherwise the two-step exit-0
  contract is unsatisfiable;
- ``--scope unit`` copies evolver/crews/configs/data and runs the whole unit
  tree; it is the "no-key state" runner used by acceptance criterion 2a.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

IMPORT_ALL_SNIPPET = """
import importlib, pathlib, pkgutil
names = ["core"]
for module in pkgutil.walk_packages([str(pathlib.Path("core"))], prefix="core."):
    names.append(module.name)
for name in names:
    importlib.import_module(name)
print("imported %d core modules" % len(names))
"""


def copy_tree(tmp: Path, scope: str, source_root: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(source_root / "core", tmp / "core", ignore=ignore)
    # R17-W4b: agent_bridge is a layer BELOW the unit tests (agent_bridge ->
    # core, never reverse) — the unit tree may import it, so it ships with the
    # trimmed env in BOTH scopes, exactly like core. agents/registry.yaml is
    # the data artifact agent_bridge.registry reads by default; without it the
    # trimmed env cannot run the registry tests (missing-file exit 2).
    # R17-W5b: dsh_bridge is ALSO below the unit tests (the layer that
    # agent_bridge composes: core <- dsh_bridge <- agent_bridge); the SPI
    # conformance tests import it, so it ships too.
    shutil.copytree(source_root / "dsh_bridge", tmp / "dsh_bridge", ignore=ignore)
    shutil.copytree(source_root / "agent_bridge", tmp / "agent_bridge", ignore=ignore)
    agents_src = source_root / "agents"
    if agents_src.is_dir():
        shutil.copytree(agents_src, tmp / "agents", ignore=ignore)
    if scope == "unit":
        for extra in ("evolver", "crews", "configs", "data", "prompts"):
            src = source_root / extra
            if src.is_dir():
                shutil.copytree(src, tmp / extra, ignore=ignore)
    shutil.copytree(source_root / "tests", tmp / "tests", ignore=ignore)
    # F10: tests must not SILENTLY skip when a required data file is absent in
    # the real repo. The marker makes the trimmed-copy case explicit; a test
    # that depends on data intentional to one scope can skip on this marker and
    # must FAIL otherwise.
    (tmp / ".layercheck_scope").write_text(scope, encoding="utf-8")


def pytest_step_cmd(scope: str) -> list[str]:
    cmd = [
        sys.executable, "-m", "pytest", "tests", "-q",
        "--ignore=tests/unit/core/_negatives",
        "--ignore=tests/unit/_negatives",
        # integration tests exercise the FULL stack (graphs/api/inspector) —
        # they are D8/D9 evidence in the real env, not layering evidence; the
        # trimmed env deliberately lacks upper layers (D10 contract)
        "--ignore=tests/integration",
        # same rationale: tests/unit/inspector imports the inspector package
        # (doctor storage fixtures, WAL task 20260926) — upper layer
        "--ignore=tests/unit/inspector",
        # R17-BCFIX2: the old ignore for tests/unit/test_recovery.py is GONE —
        # the tests are deterministic again (controlled `now` injection, no
        # sleep), so they run in the trimmed env as layering evidence
        "-p", "no:cacheprovider",
    ]
    if scope == "core":
        cmd += ["--ignore=tests/unit/evolver"]
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=["core", "unit"], default="core")
    parser.add_argument("--import-all", action="store_true",
                        help="mandatory smoke: import all core modules in the trimmed env")
    parser.add_argument("--log-dir", default=None,
                        help="where to write step outputs (default: reports/layercheck)")
    parser.add_argument("--state-label", default=None,
                        help="rectification B2: label recorded in a STATE= header line "
                             "inside the logs, e.g. no_key | dummy | real")
    parser.add_argument("--root", default=None,
                        help="补遗 B2: source tree root (fixture mode — operate on a copy); "
                             "default: this repository")
    args = parser.parse_args()
    # 补遗 B2: fixture mode — the source tree can be a COPY with a broken core
    src_root = Path(args.root).resolve() if args.root else PROJECT_ROOT
    # R17-SEAL-TOOLING: pre-flight on required inputs. A missing directory
    # is an ENUMERABLE precondition (the four unconditional copytree items
    # below), so it gets a loud standard summary (B1/K5) instead of a raw
    # copytree traceback. Deliberately NO try/except net around copy_tree:
    # unexpected OS errors must stay loud and specific, not be swallowed
    # into a generic summary.
    _missing = [d for d in ("core", "tests", "dsh_bridge", "agent_bridge")
                if not (src_root / d).is_dir()]
    if _missing:
        _line = (f"LAYERCHECK FAIL (scope={args.scope}) -- missing required "
                 f"directories: {', '.join(_missing)} (root={src_root})")
        print(_line, file=sys.stderr)
        print(_line)
        return 1


    log_dir = Path(args.log_dir) if args.log_dir else (
        # F6: fixture mode (--root) defaults its logs INSIDE the copy — the
        # real repository's reports/ dir is not touched by fixture runs
        src_root / "reports" / "layercheck" if args.root
        else PROJECT_ROOT / "reports" / "layercheck")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # R17-SEAL-TOOLING: a bad --log-dir is also a "missing input" --
        # same loud standard summary, never a bare traceback (B1/K5).
        _line = (f"LAYERCHECK FAIL (scope={args.scope}) -- cannot create "
                 f"log dir {log_dir}: {exc.__class__.__name__}")
        print(_line, file=sys.stderr)
        print(_line)
        return 1
    # B1: timestamped log names — evidence files are append-only, never overwritten
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    header = f"STATE={args.state_label or 'unlabeled'} scope={args.scope} utc={ts}\n"

    exit_code = 0
    with tempfile.TemporaryDirectory(prefix="layercheck_") as td:
        tmp = Path(td)
        copy_tree(tmp, args.scope, src_root)

        env = dict(os.environ)
        env["PYTHONPATH"] = str(tmp)
        env.pop("LAYERCHECK_NEGATIVE", None)
        env.pop("NETWORK_NEGATIVE", None)
        # N3 convergence: if anything in the trimmed env imports crewai, its
        # import-time storage dir is a FIXED name — no new random leftovers
        env["CREWAI_STORAGE_DIR"] = "layercheck_probe"

        # -- step 1: import-all smoke ------------------------------------
        if args.import_all:
            step1_log = log_dir / f"import_all_{args.scope}.{ts}.log"
            with open(step1_log, "w", encoding="utf-8") as fh:
                fh.write(header)
                proc1 = subprocess.run(
                    [sys.executable, "-c", IMPORT_ALL_SNIPPET],
                    cwd=str(tmp), env=env,
                    stdout=fh, stderr=subprocess.STDOUT,
                )
            print(f"step1 import-all exit={proc1.returncode} log={step1_log}")
            print(header.strip())
            print(step1_log.read_text(encoding="utf-8").strip().splitlines()[-1])
            if proc1.returncode != 0:
                exit_code = 1

        # -- step 2: pytest in the trimmed env ---------------------------
        step2_log = log_dir / f"pytest_{args.scope}.{ts}.log"
        with open(step2_log, "w", encoding="utf-8") as fh:
            fh.write(header)
            proc2 = subprocess.run(
                pytest_step_cmd(args.scope),
                cwd=str(tmp), env=env,
                stdout=fh, stderr=subprocess.STDOUT,
            )
        print(f"step2 pytest exit={proc2.returncode} log={step2_log}")
        text = step2_log.read_text(encoding="utf-8").strip()
        print(text.splitlines()[-1] if text else "(no output)")
        if proc2.returncode != 0:
            exit_code = 1

    print(f"LAYERCHECK {'PASS' if exit_code == 0 else 'FAIL'} (scope={args.scope})")
    if exit_code != 0:
        # 补遗 B1: failure summary must be LOUD on stderr too (K5); the stdout
        # line keeps its historical format for downstream consumers
        print(f"LAYERCHECK {'FAIL' if exit_code != 0 else 'PASS'} (scope={args.scope})",
              file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
