# Contributing

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # optional
python main.py doctor
```

## Gates (run all of them before a PR)

```bash
python tests/tools/gate_all.py
```

It refuses to run on a dirty tree and then runs, in order:

1. `pytest` (the full suite)
2. `ledgercheck` (every test must be assigned to a criterion; endpoint whitelist)
3. `audit_invariants --check clean`
4. `layercheck` (trimmed-environment layering check)
5. `ui_mutation` (mutation probe for the console guard tests)

## Conventions

- **One logical change per commit**; commit messages say what behaviour changed.
- **New endpoint?** Register it in `docs/endpoint_authority.md` first; `ledgercheck` compares the
  registry against `@app` decorators.
- **New test module?** Add a row to `docs/criteria_ledger.md` and keep the totals consistent.
- **New backend?** `agent_bridge/factory.py` is registry-driven: call `register_backend(...)`.
- **New event?** Add it to the contract docs and to the console's `EVENT_TYPES`.
- **No secrets** in the repository. `.env` is ignored; use `.env.example` for shape only.
- Tests must be deterministic; the suite is expected to leave the tree clean.

## Native backend (optional)

`dsh` runs over ACP/stdio. Set `DSH_BIN` to its `bin.js` and `DSH_EXPECTED_VERSION` to the version
it reports; without those, the platform stays on the CrewAI/scripted path.
