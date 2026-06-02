# Codex Automation Tools

Codex Automation Tools is a Codex plugin marketplace for creating and updating personal Codex automations with reusable script-local context.

It keeps automation-specific helper scripts and their local state under:

```text
/Users/seongho/.codex/automations/<automation-id>/
  automation.toml
  memory.md
  docs/
  scripts/
    <script-name>/
      main.mjs
      main.test.mjs
      README.md
      context/
        automation.json
        repo.json
        codebase.json
        env.json
        db.json
        integrations.json
      memory/
        decisions.md
        assumptions.md
      history/
        runs.jsonl
      artifacts/
        latest-result.json
      tmp/
      logs/
```

## What It Adds

- `automation-workspaces` skill for all Codex automation creation and update flows.
- `prepare_automation_workspace.py` helper to scaffold automation-owned directories.
- Node and Python helper entrypoint templates with focused tests and script-local path helpers.
- Script-local `history/` and `artifacts/` directories so run records and generated outputs live beside the code that created them.
- Script-local `context/` files so automations can reuse repo, codebase, env, DB, integration, action-policy, and output expectations without asking every run.
- Script-local `memory/` files for durable decisions and assumptions.
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
4. Create one helper workspace at `scripts/<script-name>/`.
5. Put repeated or guardrail behavior in `scripts/<script-name>/main.mjs`.
6. Ask and save creation-time context in `scripts/<script-name>/context/`.
7. Put durable decisions and assumptions in `scripts/<script-name>/memory/`.
8. Put run history in `scripts/<script-name>/history/runs.jsonl`.
9. Put durable generated outputs in `scripts/<script-name>/artifacts/`.
10. Update the automation prompt so it calls `scripts/<script-name>/main.mjs` by absolute path and reads context before acting.

Manual scaffold example:

```bash
python3 plugins/codex-automation-tools/scripts/prepare_automation_workspace.py daily-report-check --script-name run-check --language node
```

This creates:

```text
/Users/seongho/.codex/automations/daily-report-check/scripts/run-check/
  main.mjs
  main.test.mjs
  context/
    automation.json
    repo.json
    codebase.json
    env.json
    db.json
    integrations.json
  memory/
    decisions.md
    assumptions.md
  history/
    runs.jsonl
  artifacts/
    latest-result.json
  tmp/
  logs/
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

## Context Bootstrap

The plugin is centered on creation-time questions that future runs can reuse:

- Which repo or repos does this automation target?
- Which information is needed every run?
- Should codebase context be read live or cached as a snapshot?
- Which env key names are required, and where are they read at runtime?
- Is DB context needed, and is read-only summary enough?
- Which external systems are involved, such as GitHub, Sentry, Slack, Notion, Linear, Vercel, or Cloudflare?
- Which actions are allowed without asking again, and which require explicit approval?

The answers are stored in `context/*.json` and can be refreshed by the helper script. The automation should use these files as durable context, while treating live git checkouts, runtime env, live DBs, and external systems as the source of truth.

## Sensitive Context

The plugin stores secret context as references and policies, not values. Automation workspaces may keep required env key names, retrieval methods, minimum access rules, redacted verification results, and derived summaries. They must not store raw env files, full connection strings, API tokens, private keys, or decrypted secret values in `context/`, `memory/`, `history/`, `artifacts/`, `logs/`, or prompts.

Encrypted blobs are not treated as a complete solution: if an unattended automation can decrypt them, the decrypt key is the real security boundary. Prefer runtime env, ignored local env files, OS keychain entries, or secret-manager references, with read-only or least-privilege credentials where possible.
