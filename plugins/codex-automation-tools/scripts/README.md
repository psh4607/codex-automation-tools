# Scripts

`prepare_automation_workspace.py` creates the standard local directory layout for a Codex automation and adds a runnable helper workspace with script-local state directories.

Example:

```bash
python3 scripts/prepare_automation_workspace.py daily-report-check --script-name run-check --language node
```

The script is intentionally conservative: it creates missing files, keeps existing scripts by default, and requires `--force` to overwrite generated files.

Each helper is created as `~/.codex/automations/<automation-id>/scripts/<script-name>/` with `main.mjs` or `main.py`, a focused test, `context/`, `memory/`, `history/`, `artifacts/`, `tmp/`, and `logs/`.

The `context/` directory starts with `automation.json`, `repo.json`, `codebase.json`, `env.json`, `db.json`, and `integrations.json`. These files store reusable creation-time answers and source-of-truth rules, not raw secret values.

## Remote Automations

Use `--remote-host <ssh-host>` when the automation should be visible from local Codex but execute on a remote host. Remote automation titles should start with `[remote]`.

First register the host from SSH config when practical:

```bash
python3 scripts/manage_remote_hosts.py discover --include dalpha-mac
python3 scripts/manage_remote_hosts.py list
python3 scripts/manage_remote_hosts.py show dalpha-mac
```

```bash
python3 scripts/prepare_automation_workspace.py daily-report-check \
  --script-name run-check \
  --title "Daily Report Check" \
  --remote-host dalpha-mac
```

This creates `remote.json` beside `automation.toml`. Manage remote lifecycle plans with:

```bash
python3 scripts/manage_remote_automation.py install ~/.codex/automations/daily-report-check
python3 scripts/manage_remote_automation.py pause ~/.codex/automations/daily-report-check
python3 scripts/manage_remote_automation.py resume ~/.codex/automations/daily-report-check
python3 scripts/manage_remote_automation.py delete ~/.codex/automations/daily-report-check
python3 scripts/manage_remote_automation.py diff --desired desired.json --actual actual.json
```

Deletes are tombstones first. Pruning jobs that are missing from the desired registry requires the explicit `--prune-missing` flag.
