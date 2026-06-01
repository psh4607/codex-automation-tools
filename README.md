# Codex Automation Tools

Codex Automation Tools is a Codex plugin marketplace for creating and updating personal Codex automations with durable local workspaces.

It keeps automation-specific scripts, structured inputs, templates, docs, logs, and scratch output under:

```text
/Users/seongho/.codex/automations/<automation-id>/
```

## What It Adds

- `automation-workspaces` skill for all Codex automation creation and update flows.
- `prepare_automation_workspace.py` helper to scaffold automation-owned directories.
- Node and Python helper entrypoint templates with focused tests.
- Guardrails for keeping private automation scripts out of team repositories unless they are intentionally shared.

## Install

Add this repository as a Codex plugin marketplace:

```bash
codex plugin marketplace add psh4607/codex-automation-tools --ref main
codex plugin add codex-automation-tools@codex-automation-tools
```

For local development:

```bash
codex plugin marketplace add /Users/seongho/projects/seongho/plugins/codex-automation-tools
codex plugin add codex-automation-tools@codex-automation-tools
```

Start a new Codex thread after installing so the new skill is loaded.

## Usage

When creating or updating any Codex automation, the plugin directs Codex to:

1. Inspect existing automations and avoid duplicate ids.
2. Resolve the automation id.
3. Scaffold `/Users/seongho/.codex/automations/<automation-id>/`.
4. Put repeated or guardrail behavior in `scripts/`.
5. Put non-secret structured inputs in `data/`.
6. Put reusable output bodies in `templates/`.
7. Put runbooks and design notes in `docs/`.
8. Update the automation prompt so it calls the helper script by absolute path.

Manual scaffold example:

```bash
python3 plugins/codex-automation-tools/scripts/prepare_automation_workspace.py daily-report-check --script-name run-check --language node
```

## Repository Layout

```text
.agents/plugins/marketplace.json
plugins/codex-automation-tools/
  .codex-plugin/plugin.json
  skills/automation-workspaces/SKILL.md
  scripts/prepare_automation_workspace.py
  scripts/prepare_automation_workspace_test.py
```

## Verification

```bash
python3 plugins/codex-automation-tools/scripts/prepare_automation_workspace_test.py
python3 -m py_compile \
  plugins/codex-automation-tools/scripts/prepare_automation_workspace.py \
  plugins/codex-automation-tools/scripts/prepare_automation_workspace_test.py
```
