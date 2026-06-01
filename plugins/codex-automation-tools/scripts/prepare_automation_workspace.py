#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path
from typing import Iterable


DEFAULT_ROOT = Path.home() / ".codex" / "automations"
STANDARD_DIRS = ("scripts", "data", "templates", "docs", "tmp", "logs")
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
import {{ pathToFileURL }} from 'node:url'

export function main(argv = process.argv.slice(2)) {{
  return {{
    automationId: {automation_id!r},
    scriptName: {script_name!r},
    status: 'bootstrap',
    args: argv,
  }}
}}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href
if (isMain) {{
  console.log(JSON.stringify(main(), null, 2))
}}
"""


def node_test_template(script_name: str) -> str:
    return f"""import assert from 'node:assert/strict'
import {{ test }} from 'node:test'

import {{ main }} from './{script_name}.mjs'

test('returns automation metadata', () => {{
  const result = main(['--json'])

  assert.equal(result.scriptName, {script_name!r})
  assert.equal(result.status, 'bootstrap')
  assert.deepEqual(result.args, ['--json'])
}})
"""


def python_script_template(automation_id: str, script_name: str) -> str:
    return f"""#!/usr/bin/env python3
import argparse
import json


def main(argv=None):
    parser = argparse.ArgumentParser(description='Automation helper entrypoint.')
    parser.add_argument('--json', action='store_true', help='Print JSON output.')
    args = parser.parse_args(argv)
    payload = {{
        'automationId': {automation_id!r},
        'scriptName': {script_name!r},
        'status': 'bootstrap',
    }}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


if __name__ == '__main__':
    main()
"""


def python_test_template(script_name: str) -> str:
    module_name = script_name.replace("-", "_").replace(".", "_")
    return f"""import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name({script_name + ".py"!r})


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
"""


def readme_template(automation_id: str, script_name: str, language: str) -> str:
    return f"""# {automation_id}

This directory belongs to the Codex automation `{automation_id}`.

## Layout

- `automation.toml`: scheduler metadata managed by Codex.
- `scripts/`: deterministic helpers, guardrails, collectors, classifiers, tests.
- `data/`: non-secret structured inputs and stable mappings.
- `templates/`: reusable markdown, issue, or report templates.
- `docs/`: runbooks and design notes for this automation.
- `tmp/`: scratch output that can be regenerated.
- `logs/`: local execution logs when a helper needs durable evidence.

## Primary Helper

Start with `scripts/{script_name}.{'mjs' if language == 'node' else 'py'}` and keep repeated or guardrail behavior in code.
The automation prompt should call helpers by absolute path and then use their structured output.
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

    created_files: list[str] = []
    existing_files: list[str] = []
    for dirname in STANDARD_DIRS:
        (automation_dir / dirname).mkdir(parents=True, exist_ok=True)

    docs_readme = automation_dir / "docs" / "README.md"
    created, path = write_file(
        docs_readme,
        readme_template(automation_id, script_name, language),
        force=force,
    )
    (created_files if created else existing_files).append(path)

    for dirname, title, body in [
        ("data", "Data", ["Keep non-secret JSON/YAML inputs here."]),
        ("templates", "Templates", ["Keep reusable issue, report, and message templates here."]),
    ]:
        created, path = write_file(
            automation_dir / dirname / "README.md",
            placeholder_readme(title, body),
            force=force,
        )
        (created_files if created else existing_files).append(path)

    if language == "node":
        script = automation_dir / "scripts" / f"{script_name}.mjs"
        test_file = automation_dir / "scripts" / f"{script_name}.test.mjs"
        script_body = node_script_template(automation_id, script_name)
        test_body = node_test_template(script_name)
    elif language == "python":
        script = automation_dir / "scripts" / f"{script_name}.py"
        test_file = automation_dir / "scripts" / f"test_{script_name.replace('-', '_')}.py"
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
