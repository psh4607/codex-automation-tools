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
  memory.md
  docs/
  scripts/
    <script-name>/
      main.mjs
      main.test.mjs
      README.md
      data/
      templates/
      history/
      artifacts/
      tmp/
      logs/
```

Use repo files only when the script is a shared team-maintained tool. If it is private Codex automation behavior, keep it under the automation id. Script history and artifacts belong under the script workspace that produced them.

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

5. Put deterministic work in `scripts/<script-name>/main.mjs` or `main.py` and keep tests beside it. Every new cron/workspace automation should get at least one helper entrypoint, even if the first version only validates inputs and emits structured metadata.
6. Put structured non-secret inputs in `scripts/<script-name>/data/`, reusable output bodies in `scripts/<script-name>/templates/`, run history in `scripts/<script-name>/history/`, and generated durable outputs in `scripts/<script-name>/artifacts/`.
7. Put automation-level runbooks and design notes in `docs/`.
8. Update the automation prompt to call helper scripts by absolute path, then reason over their structured output.

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
- history entries and artifact writes

Keep Codex prompt reasoning for ambiguous classification, final summary writing, and explicit tradeoff decisions.

## Prompt Contract

When an automation uses a helper, the prompt should say:

- Run `/Users/seongho/.codex/automations/<automation-id>/scripts/<script-name>/main.mjs` first for Node helpers, or `main.py` for Python helpers.
- Treat script failure as a blocked run unless the prompt explicitly allows fallback.
- Read the helper's JSON/Markdown output instead of reimplementing collection logic ad hoc.
- Write run history to `/Users/seongho/.codex/automations/<automation-id>/scripts/<script-name>/history/`.
- Write generated reports, drafts, payloads, screenshots, and other durable outputs to `/Users/seongho/.codex/automations/<automation-id>/scripts/<script-name>/artifacts/`.
- Do not print secrets or auth tokens.
- Do not bypass create caps, dedupe, or guardrail failures.

## Verification

Before handing back an automation change:

- Run the helper's focused tests, for example `node --test /Users/seongho/.codex/automations/<id>/scripts/<script-name>/main.test.mjs`.
- Run the helper in a dry or read-only mode if it supports one.
- Re-read `automation.toml` and confirm the prompt references the final absolute helper path.

If authentication or external API access is unavailable, report that as the blocker and leave drafts or structured output instead of creating tracker objects.
