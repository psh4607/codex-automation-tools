#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path
from typing import Iterable


DEFAULT_ROOT = Path.home() / ".codex" / "automations"
STANDARD_DIRS = ("scripts", "docs")
SCRIPT_WORKSPACE_DIRS = ("artifacts", "history", "tmp", "logs", "context", "memory")
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_segment(value: str, label: str) -> str:
    if not SAFE_SEGMENT.fullmatch(value) or "/" in value or "\\" in value:
        raise ValueError(f"{label} must be a safe path segment: {value!r}")
    if value in {".", ".."}:
        raise ValueError(f"{label} must not be {value!r}")
    return value


def remote_display_name(title: str | None, automation_id: str) -> str:
    base = (title or automation_id).strip()
    if not base:
        base = automation_id
    if base.lower().startswith("[remote]"):
        return base
    return f"[remote] {base}"


def remote_join(root: str, *parts: str) -> str:
    return "/".join([root.rstrip("/"), *parts])


def write_file(path: Path, content: str, *, force: bool) -> tuple[bool, str]:
    if path.exists() and not force:
        return False, str(path)
    path.write_text(content)
    return True, str(path)


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def node_script_template(automation_id: str, script_name: str) -> str:
    return f"""#!/usr/bin/env node
import {{ realpathSync }} from 'node:fs'
import {{ dirname, join }} from 'node:path'
import {{ fileURLToPath, pathToFileURL }} from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))

export function workspacePaths() {{
  return {{
    scriptDir,
    contextDir: join(scriptDir, 'context'),
    memoryDir: join(scriptDir, 'memory'),
    historyDir: join(scriptDir, 'history'),
    artifactsDir: join(scriptDir, 'artifacts'),
    tmpDir: join(scriptDir, 'tmp'),
    logsDir: join(scriptDir, 'logs'),
  }}
}}

export function main(argv = process.argv.slice(2)) {{
  return {{
    automationId: {automation_id!r},
    scriptName: {script_name!r},
    status: 'bootstrap',
    args: argv,
    paths: workspacePaths(),
  }}
}}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href
if (isMain) {{
  console.log(JSON.stringify(main(), null, 2))
}}
"""


def node_test_template(script_name: str) -> str:
    return f"""import assert from 'node:assert/strict'
import {{ test }} from 'node:test'

import {{ main, workspacePaths }} from './main.mjs'

test('returns automation metadata', () => {{
  const result = main(['--json'])

  assert.equal(result.scriptName, {script_name!r})
  assert.equal(result.status, 'bootstrap')
  assert.deepEqual(result.args, ['--json'])
  assert.equal(result.paths.contextDir, workspacePaths().contextDir)
  assert.equal(result.paths.memoryDir, workspacePaths().memoryDir)
  assert.equal(result.paths.artifactsDir, workspacePaths().artifactsDir)
  assert.equal(result.paths.historyDir, workspacePaths().historyDir)
}})
"""


def python_script_template(automation_id: str, script_name: str) -> str:
    return f"""#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def workspace_paths():
    return {{
        'scriptDir': str(SCRIPT_DIR),
        'contextDir': str(SCRIPT_DIR / 'context'),
        'memoryDir': str(SCRIPT_DIR / 'memory'),
        'historyDir': str(SCRIPT_DIR / 'history'),
        'artifactsDir': str(SCRIPT_DIR / 'artifacts'),
        'tmpDir': str(SCRIPT_DIR / 'tmp'),
        'logsDir': str(SCRIPT_DIR / 'logs'),
    }}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Automation helper entrypoint.')
    parser.add_argument('--json', action='store_true', help='Print JSON output.')
    args = parser.parse_args(argv)
    payload = {{
        'automationId': {automation_id!r},
        'scriptName': {script_name!r},
        'status': 'bootstrap',
        'paths': workspace_paths(),
    }}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


if __name__ == '__main__':
    main()
"""


def python_test_template(script_name: str) -> str:
    module_name = script_name.replace("-", "_").replace(".", "_") + "_main"
    return f"""import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name('main.py')


def load_module():
    spec = importlib.util.spec_from_file_location({module_name!r}, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_main_returns_bootstrap_metadata():
    module = load_module()
    result = module.main([])

    assert result['scriptName'] == {script_name!r}
    assert result['status'] == 'bootstrap'
    assert result['paths']['contextDir'] == module.workspace_paths()['contextDir']
    assert result['paths']['memoryDir'] == module.workspace_paths()['memoryDir']
    assert result['paths']['artifactsDir'] == module.workspace_paths()['artifactsDir']
    assert result['paths']['historyDir'] == module.workspace_paths()['historyDir']
"""


def readme_template(automation_id: str, script_name: str, language: str) -> str:
    entrypoint = "main.mjs" if language == "node" else "main.py"
    return f"""# {automation_id}

This directory belongs to the Codex automation `{automation_id}`.

## Layout

- `automation.toml`: scheduler metadata managed by Codex.
- `scripts/{script_name}/`: deterministic helper and context workspace for this script.
- `docs/`: runbooks and design notes for this automation.

## Primary Helper

Start with `scripts/{script_name}/{entrypoint}` and keep repeated work, guardrails, and context refresh behavior in code.
The automation prompt should call helpers by absolute path and then use their structured output.
"""


def script_readme_template(script_name: str, language: str) -> str:
    entrypoint = "main.mjs" if language == "node" else "main.py"
    test_file = "main.test.mjs" if language == "node" else "test_main.py"
    return f"""# {script_name}

This directory is the workspace for one automation helper script.

## Entrypoints

- `{entrypoint}`: script logic and guardrails.
- `{test_file}`: focused tests for this helper.

## Context

- `context/automation.json`: purpose, expected outputs, and action policy.
- `context/repo.json`: target repositories, worktree preference, and repo-local scope.
- `context/codebase.json`: live-read vs snapshot strategy and stale-cache rules.
- `context/env.json`: required env key names and secret retrieval policy, without values.
- `context/db.json`: DB need, read-only mode, allowed checks, and redacted summary policy.
- `context/integrations.json`: GitHub, Sentry, Slack, Notion, and other external systems.

## Local State

- `history/`: append-only run summaries and decision records.
- `artifacts/`: generated reports, payloads, drafts, screenshots, and other durable outputs.
- `tmp/`: scratch files safe to regenerate.
- `logs/`: local execution logs when needed for debugging.
- `memory/`: durable decisions and assumptions used by this helper.

Automation prompts should call this helper by absolute path, read context before acting, and should not scatter artifacts outside this directory unless explicitly required.
"""


def placeholder_readme(title: str, body: Iterable[str]) -> str:
    lines = [f"# {title}", ""]
    lines.extend(body)
    lines.append("")
    return "\n".join(lines)


def json_template(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def automation_context_template(automation_id: str, script_name: str) -> str:
    return json_template(
        {
            "automationId": automation_id,
            "scriptName": script_name,
            "purpose": "",
            "expectedOutputs": [],
            "actionPolicy": {
                "defaultMode": "report-or-draft",
                "allowedActions": [],
                "requiresExplicitApprovalFor": ["code changes", "database writes", "destructive operations"],
            },
            "questionsToResolveAtCreation": [
                "What should this automation accomplish?",
                "What output should it leave after each run?",
                "Which actions may it perform without asking again?",
            ],
        }
    )


def repo_context_template(automation_id: str, script_name: str) -> str:
    return json_template(
        {
            "automationId": automation_id,
            "scriptName": script_name,
            "primaryRepo": None,
            "targetRepos": [],
            "worktree": {
                "default": "follow-user-or-repo-policy",
                "notes": [],
            },
            "repoLocalFiles": [],
            "questionsToResolveAtCreation": [
                "Which repo or repos does this automation target?",
                "Should it use the current checkout, a worktree, or read-only inspection?",
                "Which app, package, module, or path is in scope?",
            ],
        }
    )


def codebase_context_template(automation_id: str, script_name: str) -> str:
    return json_template(
        {
            "automationId": automation_id,
            "scriptName": script_name,
            "sourceOfTruth": "live-git-checkout",
            "strategy": "live-read",
            "snapshot": {
                "enabled": False,
                "artifactPath": "artifacts/codebase-map.json",
                "staleWhen": ["git-sha-changed", "tracked-files-hash-changed"],
            },
            "questionsToResolveAtCreation": [
                "Should the automation read the live codebase every run?",
                "Should it maintain a snapshot or codebase map artifact?",
                "What makes cached codebase context stale?",
            ],
        }
    )


def env_context_template(automation_id: str, script_name: str) -> str:
    return json.dumps(
        {
            "automationId": automation_id,
            "scriptName": script_name,
            "sourceOfTruth": ["runtime-env", "ignored-local-env-file", "os-keychain-reference", "secret-manager-reference"],
            "requiredKeys": [],
            "optionalKeys": [],
            "policy": {
                "storeSecretValues": False,
                "storeRawEnvFiles": False,
                "printSecretValues": False,
                "recordPresenceOnly": True,
            },
            "questionsToResolveAtCreation": [
                "Which env key names does this automation need?",
                "Where should those keys be read from at runtime?",
                "Which env keys are required vs optional?",
            ],
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def db_context_template(automation_id: str, script_name: str) -> str:
    return json_template(
        {
            "automationId": automation_id,
            "scriptName": script_name,
            "needed": False,
            "sourceOfTruth": "live-db-and-migration-source",
            "defaultAccessMode": "read-only",
            "connection": {
                "source": "runtime-env-or-secret-reference",
                "storeConnectionStrings": False,
            },
            "allowedChecks": [],
            "summaryArtifacts": {
                "schemaSummary": "artifacts/db-schema-summary.json",
                "latestCheck": "artifacts/db-latest-check.json",
            },
            "questionsToResolveAtCreation": [
                "Does this automation need DB context?",
                "Is read-only summary enough?",
                "Which checks are allowed?",
            ],
        }
    )


def integrations_context_template(automation_id: str, script_name: str) -> str:
    return json_template(
        {
            "automationId": automation_id,
            "scriptName": script_name,
            "systems": [],
            "knownSystems": ["GitHub", "Sentry", "Slack", "Notion", "Linear", "Vercel", "Cloudflare"],
            "policy": {
                "leastPrivilege": True,
                "storeTokens": False,
                "preferDraftsBeforeWrites": True,
            },
            "questionsToResolveAtCreation": [
                "Which external systems does this automation use?",
                "May it create or update objects there?",
                "What identifiers should it remember without storing credentials?",
            ],
        }
    )


def remote_manifest_template(
    *,
    automation_id: str,
    script_name: str,
    title: str | None,
    remote_host: str,
    remote_root: str,
    remote_scheduler: str,
    remote_reconcile_interval_hours: int,
    remote_purge_after_days: int,
) -> str:
    return json_template(
        {
            "schemaVersion": 1,
            "managedBy": "codex-automation-tools",
            "mode": "remote-host",
            "automationId": automation_id,
            "scriptName": script_name,
            "displayName": remote_display_name(title, automation_id),
            "status": "active",
            "host": remote_host,
            "remoteRoot": remote_root,
            "remoteAutomationDir": remote_join(remote_root, "automations", automation_id),
            "registry": {
                "path": remote_join(remote_root, "registry.json"),
                "recordPath": remote_join(remote_root, "registry", f"{automation_id}.json"),
            },
            "scheduler": {
                "type": remote_scheduler,
                "reconcileIntervalHours": remote_reconcile_interval_hours,
            },
            "lifecycle": {
                "deleteStrategy": "tombstone",
                "pauseStrategy": "disable-scheduler",
                "archiveBeforePurge": True,
                "purgeAfterDays": remote_purge_after_days,
                "pruneMissing": False,
            },
            "sync": {
                "docs": True,
                "scripts": True,
                "context": True,
                "memory": True,
                "history": "remote-owned",
                "artifacts": "remote-owned",
                "tmp": "remote-owned",
                "logs": "remote-owned",
            },
        }
    )


def markdown_template(title: str, body: Iterable[str]) -> str:
    lines = [f"# {title}", ""]
    lines.extend(body)
    lines.append("")
    return "\n".join(lines)


def prepare_workspace(
    *,
    root: Path = DEFAULT_ROOT,
    automation_id: str,
    script_name: str,
    language: str = "node",
    force: bool = False,
    title: str | None = None,
    remote_host: str | None = None,
    remote_root: str = "~/.codex/remote-automations",
    remote_scheduler: str = "systemd-timer",
    remote_reconcile_interval_hours: int = 6,
    remote_purge_after_days: int = 14,
) -> dict:
    automation_id = validate_segment(automation_id, "automation_id")
    script_name = validate_segment(script_name, "script_name")
    root = Path(root).expanduser().absolute()
    automation_dir = root / automation_id
    script_dir = automation_dir / "scripts" / script_name

    created_files: list[str] = []
    existing_files: list[str] = []
    for dirname in STANDARD_DIRS:
        (automation_dir / dirname).mkdir(parents=True, exist_ok=True)
    for dirname in SCRIPT_WORKSPACE_DIRS:
        (script_dir / dirname).mkdir(parents=True, exist_ok=True)

    docs_readme = automation_dir / "docs" / "README.md"
    created, path = write_file(
        docs_readme,
        readme_template(automation_id, script_name, language),
        force=force,
    )
    (created_files if created else existing_files).append(path)

    script_readme = script_dir / "README.md"
    created, path = write_file(
        script_readme,
        script_readme_template(script_name, language),
        force=force,
    )
    (created_files if created else existing_files).append(path)

    for dirname, readme_title, body in [
        ("context", "Context", ["Keep automation bootstrap answers and reusable context contracts here."]),
        ("memory", "Memory", ["Keep durable decisions and assumptions used by this helper here."]),
        ("history", "History", ["Keep append-only run summaries and decision records here."]),
        ("artifacts", "Artifacts", ["Keep generated durable outputs here."]),
    ]:
        created, path = write_file(
            script_dir / dirname / "README.md",
            placeholder_readme(readme_title, body),
            force=force,
        )
        (created_files if created else existing_files).append(path)

    for filename, content in [
        ("automation.json", automation_context_template(automation_id, script_name)),
        ("repo.json", repo_context_template(automation_id, script_name)),
        ("codebase.json", codebase_context_template(automation_id, script_name)),
        ("env.json", env_context_template(automation_id, script_name)),
        ("db.json", db_context_template(automation_id, script_name)),
        ("integrations.json", integrations_context_template(automation_id, script_name)),
    ]:
        created, path = write_file(
            script_dir / "context" / filename,
            content,
            force=force,
        )
        (created_files if created else existing_files).append(path)

    for filename, content in [
        ("decisions.md", markdown_template("Decisions", ["Record durable choices this automation should reuse."])),
        ("assumptions.md", markdown_template("Assumptions", ["Record assumptions to verify or revisit during future runs."])),
    ]:
        created, path = write_file(
            script_dir / "memory" / filename,
            content,
            force=force,
        )
        (created_files if created else existing_files).append(path)

    created, path = write_file(script_dir / "history" / "runs.jsonl", "", force=force)
    (created_files if created else existing_files).append(path)

    created, path = write_file(
        script_dir / "artifacts" / "latest-result.json",
        json_template(
            {
                "automationId": automation_id,
                "scriptName": script_name,
                "status": "bootstrap",
                "generatedAt": None,
                "summary": None,
            }
        ),
        force=force,
    )
    (created_files if created else existing_files).append(path)

    remote_manifest_path: str | None = None
    suggested_name = title or automation_id
    if remote_host:
        remote_manifest = automation_dir / "remote.json"
        created, path = write_file(
            remote_manifest,
            remote_manifest_template(
                automation_id=automation_id,
                script_name=script_name,
                title=title,
                remote_host=remote_host,
                remote_root=remote_root,
                remote_scheduler=remote_scheduler,
                remote_reconcile_interval_hours=remote_reconcile_interval_hours,
                remote_purge_after_days=remote_purge_after_days,
            ),
            force=force,
        )
        (created_files if created else existing_files).append(path)
        remote_manifest_path = path
        suggested_name = remote_display_name(title, automation_id)

    if language == "node":
        script = script_dir / "main.mjs"
        test_file = script_dir / "main.test.mjs"
        script_body = node_script_template(automation_id, script_name)
        test_body = node_test_template(script_name)
    elif language == "python":
        script = script_dir / "main.py"
        test_file = script_dir / "test_main.py"
        script_body = python_script_template(automation_id, script_name)
        test_body = python_test_template(script_name)
    else:
        raise ValueError("language must be 'node' or 'python'")

    created, path = write_file(script, script_body, force=force)
    (created_files if created else existing_files).append(path)
    if created:
        make_executable(script)

    created, path = write_file(test_file, test_body, force=force)
    (created_files if created else existing_files).append(path)

    return {
        "automation_dir": str(automation_dir),
        "script_dir": str(script_dir),
        "created_files": created_files,
        "existing_files": existing_files,
        "script": str(script),
        "test": str(test_file),
        "remote_manifest": remote_manifest_path,
        "suggested_name": suggested_name,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a standard local workspace for a Codex automation."
    )
    parser.add_argument("automation_id", help="Automation id under ~/.codex/automations.")
    parser.add_argument(
        "--script-name",
        default="run",
        help="Base helper script name without extension.",
    )
    parser.add_argument(
        "--language",
        choices=("node", "python"),
        default="node",
        help="Entry point template language.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Automation root directory.",
    )
    parser.add_argument("--title", help="Human-readable automation title.")
    parser.add_argument("--remote-host", help="Remote host that should execute this automation.")
    parser.add_argument(
        "--remote-root",
        default="~/.codex/remote-automations",
        help="Remote automation root on the execution host.",
    )
    parser.add_argument(
        "--remote-scheduler",
        choices=("systemd-timer", "cron"),
        default="systemd-timer",
        help="Remote scheduler type.",
    )
    parser.add_argument(
        "--remote-reconcile-interval-hours",
        type=int,
        default=6,
        help="How often the remote host should reconcile registry state.",
    )
    parser.add_argument(
        "--remote-purge-after-days",
        type=int,
        default=14,
        help="Retention window before deleted remote workspaces are purged.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing generated files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = prepare_workspace(
        root=args.root,
        automation_id=args.automation_id,
        script_name=args.script_name,
        language=args.language,
        force=args.force,
        title=args.title,
        remote_host=args.remote_host,
        remote_root=args.remote_root,
        remote_scheduler=args.remote_scheduler,
        remote_reconcile_interval_hours=args.remote_reconcile_interval_hours,
        remote_purge_after_days=args.remote_purge_after_days,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
