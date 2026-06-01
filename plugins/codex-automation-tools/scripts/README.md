# Scripts

`prepare_automation_workspace.py` creates the standard local directory layout for a Codex automation and adds a runnable helper entrypoint plus a focused test file.

Example:

```bash
python3 scripts/prepare_automation_workspace.py daily-report-check --script-name run-check --language node
```

The script is intentionally conservative: it creates missing files, keeps existing scripts by default, and requires `--force` to overwrite generated files.
