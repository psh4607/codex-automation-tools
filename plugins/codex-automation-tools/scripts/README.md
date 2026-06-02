# Scripts

`prepare_automation_workspace.py` creates the standard local directory layout for a Codex automation and adds a runnable helper workspace with script-local state directories.

Example:

```bash
python3 scripts/prepare_automation_workspace.py daily-report-check --script-name run-check --language node
```

The script is intentionally conservative: it creates missing files, keeps existing scripts by default, and requires `--force` to overwrite generated files.

Each helper is created as `~/.codex/automations/<automation-id>/scripts/<script-name>/` with `main.mjs` or `main.py`, a focused test, and `data/`, `templates/`, `history/`, `artifacts/`, `tmp/`, and `logs/`.

The `data/` directory starts with `ssot-contract.json` and `secret-contract.json`. These files store source-of-truth and secret retrieval rules, not raw secret values.
