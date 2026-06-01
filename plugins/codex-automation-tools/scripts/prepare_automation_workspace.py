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
SCRIPT_WORKSPACE_DIRS = ("artifacts", "history", "tmp", "logs", "data", "templates")
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_segment(value: str, label: str) -> str:
    if not SAFE_SEGMENT.fullmatch(value) or "/" in value or "\\" in value:
        raise ValueError(f"{label} must be a safe path segment: {value!r}")
    if value in {".", ".."}:
        raise ValueError(f"{label} must not be {value!r}")
    return value


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
    dataDir: join(scriptDir, 'data'),
    templatesDir: join(scriptDir, 'templates'),
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
        'dataDir': str(SCRIPT_DIR / 'data'),
        'templatesDir': str(SCRIPT_DIR / 'templates'),
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
    assert result['paths']['artifactsDir'] == module.workspace_paths()['artifactsDir']
    assert result['paths']['historyDir'] == module.workspace_paths()['historyDir']
"""


def readme_template(automation_id: str, script_name: str, language: str) -> str:
    entrypoint = "main.mjs" if language == "node" else "main.py"
    return f"""# {automation_id}

This directory belongs to the Codex automation `{automation_id}`.

## Layout

- `automation.toml`: scheduler metadata managed by Codex.
- `scripts/{script_name}/`: deterministic helper workspace for this script.
- `docs/`: runbooks and design notes for this automation.

## Primary Helper

Start with `scripts/{script_name}/{entrypoint}` and keep repeated or guardrail behavior in code.
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

## Local State

- `history/`: append-only run summaries and decision records.
- `artifacts/`: generated reports, payloads, drafts, screenshots, and other durable outputs.
- `tmp/`: scratch files safe to regenerate.
- `logs/`: local execution logs when needed for debugging.
- `data/`: non-secret structured inputs and stable mappings for this helper.
- `templates/`: reusable markdown, issue, report, or message templates for this helper.

Automation prompts should call this helper by absolute path and should not scatter artifacts outside this directory unless explicitly required.
"""


def placeholder_readme(title: str, body: Iterable[str]) -> str:
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

    for dirname, title, body in [
        ("data", "Data", ["Keep non-secret JSON/YAML inputs here."]),
        ("templates", "Templates", ["Keep reusable issue, report, and message templates here."]),
        ("history", "History", ["Keep append-only run summaries and decision records here."]),
        ("artifacts", "Artifacts", ["Keep generated durable outputs here."]),
    ]:
        created, path = write_file(
            script_dir / dirname / "README.md",
            placeholder_readme(title, body),
            force=force,
        )
        (created_files if created else existing_files).append(path)

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
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
