---
name: automation-workspaces
description: Use when creating or updating any Codex automation, recurring job, monitor, reminder, heartbeat, cron task, scheduled follow-up, or Korean "자동화" flow.
---

# Automation Workspaces

## Core Rule

For personal Codex automations, keep durable implementation assets with the automation, not in a team repo:

```text
/Users/seongho/.codex/automations/<automation-id>/
  automation.toml
  remote.json
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

Use repo files only when the script is a shared team-maintained tool. If it is private Codex automation behavior, keep it under the automation id. Script context, memory, history, and artifacts belong under the script workspace that uses them.

`remote.json` exists only for automations that should execute on a remote host. It is optional and should not be created for ordinary local Codex automations.

## Required Flow

1. Inspect existing automations first and avoid duplicate automation ids.
2. Resolve the current automation id.
   - Existing automation: use the id from `automation.toml` or the automation tool.
   - New automation: create or suggest it with the automation tool, then use the returned id. If the prompt must reference a script path, update the automation after the id exists.
3. Resolve this installed plugin root from the current skill path.
   - This skill lives at `<plugin-root>/skills/automation-workspaces/SKILL.md`.
   - The scaffold helper is `<plugin-root>/scripts/prepare_automation_workspace.py`.
4. Scaffold the local workspace for the automation id:

```bash
python3 <plugin-root>/scripts/prepare_automation_workspace.py <automation-id> --script-name <script-slug> --language node
```

For a remote-host automation, always prefix the automation title with `[remote]` and scaffold with a remote manifest:

```bash
python3 <plugin-root>/scripts/manage_remote_hosts.py discover --include <ssh-host>

python3 <plugin-root>/scripts/prepare_automation_workspace.py <automation-id> \
  --script-name <script-slug> \
  --language node \
  --title "<human title>" \
  --remote-host <ssh-host>
```

The host discovery helper reads `~/.ssh/config`, validates the alias with `ssh -G <ssh-host>`, and writes `/Users/seongho/.codex/remote-hosts.json`. It does not open a remote shell. If a host registry exists, `prepare_automation_workspace.py --remote-host <id-or-alias>` must resolve against it before writing `remote.json`.

5. Put deterministic work in `scripts/<script-name>/main.mjs` or `main.py` and keep tests beside it. Every new cron/workspace automation should get at least one helper entrypoint, even if the first version only validates inputs and emits structured metadata.
6. Ask the creation-time context questions and store answers in `scripts/<script-name>/context/*.json`.
7. Put durable decisions and assumptions in `scripts/<script-name>/memory/`, run history in `scripts/<script-name>/history/runs.jsonl`, and generated durable outputs in `scripts/<script-name>/artifacts/`.
8. Put automation-level runbooks and design notes in `docs/`.
9. Update the automation prompt to call helper scripts by absolute path, read context first, then reason over their structured output.

## Creation-Time Context Questions

Ask only what is needed to make future automation runs self-sufficient:

- What is this automation's purpose and expected output?
- Which repo or repos does it target?
- Which information is needed every run?
- Should codebase context be read live, cached as a snapshot, or both?
- Which env key names are required, and where should they be read from at runtime?
- Is DB context needed, and is read-only summary enough?
- Which external systems are involved: GitHub, Sentry, Slack, Notion, Linear, Vercel, Cloudflare, or others?
- Which actions are allowed without asking again, and which require explicit approval?
- Does this need to keep running if the local Codex machine is off? If yes, use `[remote]` title and `remote.json`.
- Which registered remote host should run it? If not registered yet, discover it from SSH config first.

Store those answers in:

- `context/automation.json` for purpose, outputs, and action policy.
- `context/repo.json` for target repositories and repo-local scope.
- `context/codebase.json` for live-read vs snapshot behavior.
- `context/env.json` for env key names and runtime retrieval policy.
- `context/db.json` for DB need, read-only mode, and allowed checks.
- `context/integrations.json` for external systems and write permissions.

## What Belongs In Scripts

Move behavior into scripts when it is repeated, high-risk, or should not depend on model judgment:

- API pagination and collection
- sorting and priority rules
- dedupe keys and existing tracker/search queries
- create/update caps and rate limits
- PII, UUID, token, and DSN redaction
- deterministic classification rules
- markdown/JSON report generation
- guardrail checks that should block unsafe writes
- context refreshes
- history entries and artifact writes

Keep Codex prompt reasoning for ambiguous classification, final summary writing, and explicit tradeoff decisions.

## Remote Runtime

Codex native automation scheduling is local. If the user wants execution to continue while this Mac is off, treat local Codex as the control plane and a remote host as the runtime:

- Use an automation title starting with `[remote]`.
- Keep available runner hosts in `/Users/seongho/.codex/remote-hosts.json`.
- Keep the local desired runtime contract in `/Users/seongho/.codex/automations/<automation-id>/remote.json`.
- Use `<plugin-root>/scripts/manage_remote_hosts.py discover --include <ssh-host>` to register a host from SSH config.
- Use `<plugin-root>/scripts/manage_remote_hosts.py list` or `show <host>` before selecting a remote host for a new automation.
- Use `<plugin-root>/scripts/manage_remote_automation.py install <automation-dir>` to produce the remote install plan.
- Use `<plugin-root>/scripts/manage_remote_automation.py pause <automation-dir>` and `resume <automation-dir>` to change desired scheduler state.
- Use `<plugin-root>/scripts/manage_remote_automation.py delete <automation-dir>` to write a tombstone instead of immediately deleting state.
- Use `<plugin-root>/scripts/manage_remote_automation.py diff --desired <desired-registry.json> --actual <actual-registry.json>` for remote reconcile decisions.
- The remote host owns runtime `history/`, `artifacts/`, `tmp/`, and `logs/`; local sync should not overwrite them by default.

Lifecycle policy:

- Pause means stop or disable the remote scheduler while keeping workspace, context, memory, history, and artifacts.
- Delete means write a tombstone, stop scheduler, archive workspace, and purge only after `lifecycle.purgeAfterDays`.
- Missing desired-registry entries must not delete remote jobs by default. Only use `--prune-missing` for records with `managedBy: codex-automation-tools` when the desired registry is known complete.
- Default remote reconcile cadence is 6 hours. Daily is acceptable for low-urgency automations.

## Source Of Truth And Sensitive Context

Store reusable context, not secret values:

- Codebase source of truth: the live git checkout and git SHA. Cached codebase maps are derived artifacts and must include the source git SHA.
- Env source of truth: runtime env, ignored local env files, OS keychain entries, or secret-manager references. Store required key names and retrieval methods only.
- DB source of truth: the live DB plus migration/schema source. Store read-only checks and redacted schema summaries only.
- Automation workspace: durable context, decisions, assumptions, run history, and generated artifacts. It is not the canonical source for secrets or code.
- Remote host registry: configured host aliases and non-secret SSH metadata. It is not an auth store and does not contain private key contents.
- Remote runtime manifest: desired scheduling and lifecycle state only. It is not a scheduler by itself and does not contain credentials.

Encrypted secret blobs are only useful when the decrypt key stays outside the automation workspace. If an unattended automation can decrypt a blob, the decrypt key is the real secret boundary. Prefer secret references and runtime injection over encrypted values stored beside the automation.

## Prompt Contract

When an automation uses a helper, the prompt should say:

- Run `/Users/seongho/.codex/automations/<automation-id>/scripts/<script-name>/main.mjs` first for Node helpers, or `main.py` for Python helpers.
- Treat script failure as a blocked run unless the prompt explicitly allows fallback.
- Read `context/*.json` before asking the user for information already captured there.
- Read the helper's JSON/Markdown output instead of reimplementing collection logic ad hoc.
- Write run history to `/Users/seongho/.codex/automations/<automation-id>/scripts/<script-name>/history/`.
- Write generated reports, drafts, payloads, screenshots, and other durable outputs to `/Users/seongho/.codex/automations/<automation-id>/scripts/<script-name>/artifacts/`.
- Keep repo, codebase, env, DB, and external-system context in `context/*.json`.
- Do not print secrets or auth tokens.
- Do not store raw env files, full connection strings, private keys, tokens, cookies, or decrypted secret values in context, memory, history, artifacts, logs, or prompts.
- Do not bypass create caps, dedupe, or guardrail failures.
- For `[remote]` automations, do not claim the local Codex cron itself is the durable runtime. The durable runtime is the remote scheduler described by `remote.json`.

## Verification

Before handing back an automation change:

- Run the helper's focused tests, for example `node --test /Users/seongho/.codex/automations/<id>/scripts/<script-name>/main.test.mjs`.
- Run the helper in a dry or read-only mode if it supports one.
- Re-read `automation.toml` and confirm the prompt references the final absolute helper path.
- For `[remote]` automations, inspect `remote.json`, confirm the title prefix, and run the remote install/delete/diff plan command in dry JSON form before reporting the lifecycle change.
- If `/Users/seongho/.codex/remote-hosts.json` exists, confirm the selected host is present and `remote.json.hostRegistry` points at it.

If authentication or external API access is unavailable, report that as the blocker and leave drafts or structured output instead of creating tracker objects.
