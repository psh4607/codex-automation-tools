#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import re
import shlex
import subprocess
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any


MANAGER_NAME = "codex-automation-tools"
RUNNER_NAME = "codex-automation-runner.py"
CRON_PREFIX = "codex-automation-tools"
RUNTIME_EXCLUDES = ["scripts/*/history", "scripts/*/artifacts", "scripts/*/tmp", "scripts/*/logs"]


REMOTE_RUNNER_SOURCE = r'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


CRON_PREFIX = "codex-automation-tools"


def json_dump(payload):
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path):
    return json.loads(path.expanduser().read_text())


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dump(payload))


def marker_name(marker: str) -> str:
    return f"{CRON_PREFIX}:{marker}"


def replace_cron_block(existing: str, marker: str, block: str) -> str:
    full_marker = marker_name(marker)
    pattern = re.compile(
        rf"(?ms)^# {re.escape(full_marker)} BEGIN\n.*?^# {re.escape(full_marker)} END\n?"
    )
    without_old = pattern.sub("", existing).rstrip()
    next_text = block.rstrip()
    if without_old:
        return without_old + "\n" + next_text + "\n"
    return next_text + "\n"


def remove_cron_block(existing: str, marker: str) -> str:
    full_marker = marker_name(marker)
    pattern = re.compile(
        rf"(?ms)^# {re.escape(full_marker)} BEGIN\n.*?^# {re.escape(full_marker)} END\n?"
    )
    return pattern.sub("", existing).rstrip() + "\n"


def read_crontab() -> str:
    completed = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if completed.returncode != 0:
        return ""
    return completed.stdout


def write_crontab(text: str) -> None:
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)


def cron_set(marker: str, block_b64: str):
    block = base64.b64decode(block_b64.encode()).decode()
    next_text = replace_cron_block(read_crontab(), marker, block)
    write_crontab(next_text)
    return {"marker": marker, "status": "installed"}


def cron_remove(marker: str):
    next_text = remove_cron_block(read_crontab(), marker)
    write_crontab(next_text)
    return {"marker": marker, "status": "removed"}


def registry_record_path(remote_root: Path, automation_id: str) -> Path:
    return remote_root / "registry" / f"{automation_id}.json"


def load_registry_record(remote_root: Path, automation_id: str):
    return load_json(registry_record_path(remote_root, automation_id))


def append_jsonl(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def entrypoint_for(remote_root: Path, record):
    automation_id = record["automationId"]
    script_name = record.get("scriptName") or "run"
    script_dir = remote_root / "automations" / automation_id / "scripts" / script_name
    python_entry = script_dir / "main.py"
    node_entry = script_dir / "main.mjs"
    if python_entry.exists():
        return script_name, python_entry, [sys.executable, str(python_entry), "--json"]
    if node_entry.exists():
        return script_name, node_entry, ["node", str(node_entry), "--json"]
    raise FileNotFoundError(f"no supported entrypoint under {script_dir}")


def run_automation(remote_root: Path, automation_id: str):
    record = load_registry_record(remote_root, automation_id)
    status = record.get("status", "active")
    if status != "active":
        payload = {
            "automationId": automation_id,
            "runAt": now_iso(),
            "status": "skipped",
            "reason": f"registry status is {status}",
        }
        append_jsonl(remote_root / "logs" / f"{automation_id}.jsonl", payload)
        return payload

    script_name, entrypoint, command = entrypoint_for(remote_root, record)
    started_at = now_iso()
    completed = subprocess.run(command, capture_output=True, text=True)
    finished_at = now_iso()
    run_payload = {
        "automationId": automation_id,
        "scriptName": script_name,
        "entrypoint": str(entrypoint),
        "runAt": started_at,
        "finishedAt": finished_at,
        "returncode": completed.returncode,
        "status": "succeeded" if completed.returncode == 0 else "failed",
        "stdout": completed.stdout[-20000:],
        "stderr": completed.stderr[-20000:],
    }
    script_dir = remote_root / "automations" / automation_id / "scripts" / script_name
    append_jsonl(script_dir / "history" / "runs.jsonl", run_payload)
    append_jsonl(remote_root / "logs" / f"{automation_id}.jsonl", run_payload)
    write_json(script_dir / "artifacts" / "latest-result.json", run_payload)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return run_payload


def parse_datetime(value: str):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def deleted_age_days(deleted_at: str) -> float:
    return (datetime.now().astimezone() - parse_datetime(deleted_at)).total_seconds() / 86400


def archive_workspace(remote_root: Path, automation_id: str):
    workspace = remote_root / "automations" / automation_id
    if not workspace.exists():
        return None
    archive_dir = remote_root / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / automation_id
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(workspace), str(target))
    return str(target)


def reconcile(remote_root: Path):
    registry_dir = remote_root / "registry"
    summary = {"remoteRoot": str(remote_root), "checked": [], "cronRemoved": [], "archived": [], "purged": []}
    if not registry_dir.exists():
        return summary
    for record_path in sorted(registry_dir.glob("*.json")):
        record = load_json(record_path)
        automation_id = record.get("automationId") or record.get("id")
        if not automation_id:
            continue
        status = record.get("status", "active")
        summary["checked"].append(automation_id)
        if status in {"paused", "deleted"}:
            cron_remove(f"{automation_id}:run")
            summary["cronRemoved"].append(automation_id)
        if status == "deleted":
            deleted_at = record.get("deletedAt")
            purge_after_days = record.get("lifecycle", {}).get("purgeAfterDays", 14)
            workspace = remote_root / "automations" / automation_id
            if deleted_at and deleted_age_days(deleted_at) >= purge_after_days:
                if workspace.exists():
                    shutil.rmtree(workspace)
                archived = remote_root / "archive" / automation_id
                if archived.exists():
                    shutil.rmtree(archived)
                summary["purged"].append(automation_id)
            else:
                archived_to = archive_workspace(remote_root, automation_id)
                if archived_to:
                    summary["archived"].append(automation_id)
    return summary


def status(remote_root: Path, automation_id: str):
    record_path = registry_record_path(remote_root, automation_id)
    crontab = read_crontab()
    return {
        "automationId": automation_id,
        "registryRecordExists": record_path.exists(),
        "workspaceExists": (remote_root / "automations" / automation_id).exists(),
        "runCronInstalled": marker_name(f"{automation_id}:run") in crontab,
        "reconcileCronInstalled": marker_name("reconcile") in crontab,
        "record": load_json(record_path) if record_path.exists() else None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run and reconcile remote Codex automations.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_cmd = subparsers.add_parser("run")
    run_cmd.add_argument("--remote-root", required=True)
    run_cmd.add_argument("--automation-id", required=True)

    reconcile_cmd = subparsers.add_parser("reconcile")
    reconcile_cmd.add_argument("--remote-root", required=True)

    status_cmd = subparsers.add_parser("status")
    status_cmd.add_argument("--remote-root", required=True)
    status_cmd.add_argument("--automation-id", required=True)

    cron_set_cmd = subparsers.add_parser("cron-set")
    cron_set_cmd.add_argument("--marker", required=True)
    cron_set_cmd.add_argument("--block-b64", required=True)

    cron_remove_cmd = subparsers.add_parser("cron-remove")
    cron_remove_cmd.add_argument("--marker", required=True)

    args = parser.parse_args(argv)
    if args.command == "run":
        payload = run_automation(Path(args.remote_root).expanduser(), args.automation_id)
    elif args.command == "reconcile":
        payload = reconcile(Path(args.remote_root).expanduser())
    elif args.command == "status":
        payload = status(Path(args.remote_root).expanduser(), args.automation_id)
    elif args.command == "cron-set":
        payload = cron_set(args.marker, args.block_b64)
    elif args.command == "cron-remove":
        payload = cron_remove(args.marker)
    else:
        raise ValueError(args.command)
    print(json_dump(payload), end="")


if __name__ == "__main__":
    main()
'''


def json_dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def load_json(path: Path) -> Any:
    return json.loads(path.expanduser().read_text())


def write_json(path: Path, payload: Any) -> None:
    path.expanduser().write_text(json_dump(payload))


def remote_join(root: str, *parts: str) -> str:
    return "/".join([root.rstrip("/"), *parts])


def remote_shell_path(path: str) -> str:
    if path.startswith("~/"):
        return "$HOME/" + shlex.quote(path[2:])
    return shlex.quote(path)


def runner_path(remote_root: str) -> str:
    return remote_join(remote_root, "bin", RUNNER_NAME)


def record_id(record: dict[str, Any]) -> str:
    value = record.get("automationId") or record.get("id")
    if not isinstance(value, str) or not value:
        raise ValueError(f"registry record is missing automationId: {record!r}")
    return value


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def deleted_age_days(deleted_at: str, now: str) -> float:
    delta = parse_datetime(now) - parse_datetime(deleted_at)
    return delta.total_seconds() / 86400


def default_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_rrule(rrule: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in rrule.split(";"):
        if not part:
            continue
        key, sep, value = part.partition("=")
        if not sep:
            continue
        values[key.upper()] = value
    return values


def rrule_to_cron(rrule: str) -> str:
    values = parse_rrule(rrule)
    freq = values.get("FREQ", "").upper()
    minute = values.get("BYMINUTE", "0")
    hour = values.get("BYHOUR", "0")
    if freq == "HOURLY":
        interval = int(values.get("INTERVAL", "1"))
        if interval <= 0 or interval > 24:
            raise ValueError(f"unsupported hourly interval: {interval}")
        return f"0 */{interval} * * *"
    if freq == "DAILY":
        return f"{minute} {hour} * * *"
    if freq == "WEEKLY":
        day_map = {"SU": "0", "MO": "1", "TU": "2", "WE": "3", "TH": "4", "FR": "5", "SA": "6"}
        days = values.get("BYDAY")
        if not days:
            raise ValueError("weekly RRULE requires BYDAY")
        cron_days = ",".join(day_map[day] for day in days.split(","))
        return f"{minute} {hour} * * {cron_days}"
    raise ValueError(f"unsupported RRULE frequency for remote cron: {rrule}")


def reconcile_cron_expression(interval_hours: int) -> str:
    if interval_hours <= 0 or interval_hours > 24:
        raise ValueError(f"reconcile interval must be 1-24 hours: {interval_hours}")
    return f"0 */{interval_hours} * * *"


def load_automation_cron(automation_dir: Path) -> str:
    toml_path = automation_dir.expanduser() / "automation.toml"
    if not toml_path.exists():
        raise FileNotFoundError(f"automation.toml does not exist: {toml_path}")
    with toml_path.open("rb") as handle:
        payload = tomllib.load(handle)
    rrule = payload.get("rrule")
    if not rrule:
        raise ValueError(f"automation.toml is missing rrule: {toml_path}")
    return rrule_to_cron(rrule)


def load_remote_manifest(automation_dir: Path) -> dict[str, Any]:
    manifest_path = automation_dir.expanduser() / "remote.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"remote manifest does not exist: {manifest_path}")
    manifest = load_json(manifest_path)
    if manifest.get("mode") != "remote-host":
        raise ValueError(f"remote manifest mode must be remote-host: {manifest_path}")
    return manifest


def cron_marker(marker: str) -> str:
    return f"{CRON_PREFIX}:{marker}"


def cron_block(marker: str, cron_expression: str, command: str) -> str:
    full_marker = cron_marker(marker)
    return "\n".join(
        [
            f"# {full_marker} BEGIN",
            f"{cron_expression} {command}",
            f"# {full_marker} END",
        ]
    )


def replace_cron_block(existing: str, marker: str, block: str) -> str:
    full_marker = cron_marker(marker)
    pattern = re.compile(
        rf"(?ms)^# {re.escape(full_marker)} BEGIN\n.*?^# {re.escape(full_marker)} END\n?"
    )
    without_old = pattern.sub("", existing).rstrip()
    next_text = block.rstrip()
    if without_old:
        return without_old + "\n" + next_text + "\n"
    return next_text + "\n"


def remove_cron_block(existing: str, marker: str) -> str:
    full_marker = cron_marker(marker)
    pattern = re.compile(
        rf"(?ms)^# {re.escape(full_marker)} BEGIN\n.*?^# {re.escape(full_marker)} END\n?"
    )
    return pattern.sub("", existing).rstrip() + "\n"


def runner_command(remote_root: str, *args: str) -> str:
    runner = remote_shell_path(runner_path(remote_root))
    quoted_args = " ".join(shlex.quote(arg) for arg in args)
    return f"/usr/bin/python3 {runner} {quoted_args}".strip()


def build_run_cron_block(manifest: dict[str, Any], cron_expression: str) -> str:
    automation_id = record_id(manifest)
    remote_root = manifest["remoteRoot"]
    log_path = remote_shell_path(remote_join(remote_root, "logs", f"{automation_id}.cron.log"))
    command = (
        runner_command(remote_root, "run", "--remote-root", remote_root, "--automation-id", automation_id)
        + f" >> {log_path} 2>&1"
    )
    return cron_block(f"{automation_id}:run", cron_expression, command)


def build_reconcile_cron_block(manifest: dict[str, Any]) -> str:
    remote_root = manifest["remoteRoot"]
    interval = manifest.get("scheduler", {}).get("reconcileIntervalHours", 6)
    log_path = remote_shell_path(remote_join(remote_root, "logs", "reconcile.cron.log"))
    command = (
        runner_command(remote_root, "reconcile", "--remote-root", remote_root)
        + f" >> {log_path} 2>&1"
    )
    return cron_block("reconcile", reconcile_cron_expression(int(interval)), command)


def build_install_plan(local_automation_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    automation_id = record_id(manifest)
    remote_root = manifest["remoteRoot"]
    remote_automation_dir = manifest.get("remoteAutomationDir") or remote_join(
        remote_root, "automations", automation_id
    )
    registry_record = manifest.get("registry", {}).get("recordPath") or remote_join(
        remote_root, "registry", f"{automation_id}.json"
    )
    scheduler = manifest.get("scheduler", {})
    return {
        "automationId": automation_id,
        "displayName": manifest.get("displayName", automation_id),
        "host": manifest["host"],
        "remoteRoot": remote_root,
        "actions": [
            {
                "kind": "ensure-remote-root",
                "target": remote_root,
            },
            {
                "kind": "sync-automation",
                "source": str(local_automation_dir),
                "target": remote_automation_dir,
                "exclude": RUNTIME_EXCLUDES,
            },
            {
                "kind": "install-scheduler",
                "scheduler": scheduler.get("type", "systemd-timer"),
                "reconcileIntervalHours": scheduler.get("reconcileIntervalHours", 6),
            },
            {
                "kind": "write-registry-record",
                "target": registry_record,
                "status": manifest.get("status", "active"),
            },
        ],
    }


def build_execute_install_plan(
    local_automation_dir: Path,
    manifest: dict[str, Any],
    *,
    cron_expression: str,
) -> dict[str, Any]:
    plan = build_install_plan(local_automation_dir, manifest)
    remote_root = manifest["remoteRoot"]
    automation_id = record_id(manifest)
    registry_record = manifest.get("registry", {}).get("recordPath") or remote_join(
        remote_root, "registry", f"{automation_id}.json"
    )
    plan["actions"] = [
        {"kind": "ensure-remote-root", "target": remote_root},
        {"kind": "install-runner", "target": runner_path(remote_root)},
        {
            "kind": "sync-automation",
            "source": str(local_automation_dir),
            "target": manifest.get("remoteAutomationDir") or remote_join(remote_root, "automations", automation_id),
            "exclude": RUNTIME_EXCLUDES,
        },
        {"kind": "write-registry-record", "target": registry_record, "status": manifest.get("status", "active")},
        {"kind": "install-run-cron", "cron": cron_expression, "marker": f"{automation_id}:run"},
        {
            "kind": "install-reconcile-cron",
            "cron": reconcile_cron_expression(int(manifest.get("scheduler", {}).get("reconcileIntervalHours", 6))),
            "marker": "reconcile",
        },
    ]
    return plan


def build_uninstall_plan(manifest: dict[str, Any], *, purge: bool = False) -> dict[str, Any]:
    automation_id = record_id(manifest)
    remote_root = manifest["remoteRoot"]
    remote_automation_dir = manifest.get("remoteAutomationDir") or remote_join(
        remote_root, "automations", automation_id
    )
    registry_record = manifest.get("registry", {}).get("recordPath") or remote_join(
        remote_root, "registry", f"{automation_id}.json"
    )
    workspace_action = "purge-workspace" if purge else "archive-workspace"
    return {
        "automationId": automation_id,
        "displayName": manifest.get("displayName", automation_id),
        "host": manifest["host"],
        "actions": [
            {"kind": "stop-scheduler"},
            {"kind": "remove-registry-record", "target": registry_record},
            {"kind": workspace_action, "target": remote_automation_dir},
        ],
    }


def run_checked(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, input=input_text, text=True, capture_output=True, check=True)


def ssh(host: str, command: str) -> subprocess.CompletedProcess[str]:
    return run_checked(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", host, command])


def rsync_to_remote(source: str, host: str, target: str, *, excludes: list[str] | None = None, delete: bool = False) -> None:
    command = ["rsync", "-az"]
    if delete:
        command.append("--delete")
    for exclude in excludes or []:
        command.extend(["--exclude", exclude])
    command.extend([source, f"{host}:{target}"])
    run_checked(command)


def ensure_remote_root(host: str, remote_root: str) -> None:
    root = remote_shell_path(remote_root)
    ssh(host, f"mkdir -p {root}/bin {root}/automations {root}/registry {root}/logs {root}/archive")


def install_remote_runner(host: str, remote_root: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner = Path(tmp) / RUNNER_NAME
        runner.write_text(REMOTE_RUNNER_SOURCE)
        runner.chmod(0o755)
        rsync_to_remote(str(runner), host, remote_join(remote_root, "bin", RUNNER_NAME))
    ssh(host, f"chmod +x {remote_shell_path(runner_path(remote_root))}")


def sync_automation(local_automation_dir: Path, manifest: dict[str, Any]) -> None:
    host = manifest["host"]
    automation_id = record_id(manifest)
    remote_root = manifest["remoteRoot"]
    target = manifest.get("remoteAutomationDir") or remote_join(remote_root, "automations", automation_id)
    rsync_to_remote(
        str(local_automation_dir).rstrip("/") + "/",
        host,
        target.rstrip("/") + "/",
        excludes=RUNTIME_EXCLUDES,
        delete=True,
    )


def sync_registry_record(local_automation_dir: Path, manifest: dict[str, Any]) -> None:
    host = manifest["host"]
    automation_id = record_id(manifest)
    remote_root = manifest["remoteRoot"]
    target = manifest.get("registry", {}).get("recordPath") or remote_join(remote_root, "registry", f"{automation_id}.json")
    with tempfile.TemporaryDirectory() as tmp:
        record = Path(tmp) / f"{automation_id}.json"
        record.write_text(json_dump(manifest))
        rsync_to_remote(str(record), host, target)


def runner_remote_command(manifest: dict[str, Any], *args: str) -> str:
    return runner_command(manifest["remoteRoot"], *args)


def remote_cron_set(manifest: dict[str, Any], marker: str, block: str) -> dict[str, Any]:
    encoded = base64.b64encode(block.encode()).decode()
    command = runner_remote_command(manifest, "cron-set", "--marker", marker, "--block-b64", encoded)
    completed = ssh(manifest["host"], command)
    return json.loads(completed.stdout)


def remote_cron_remove(manifest: dict[str, Any], marker: str) -> dict[str, Any]:
    command = runner_remote_command(manifest, "cron-remove", "--marker", marker)
    completed = ssh(manifest["host"], command)
    return json.loads(completed.stdout)


def execute_install(local_automation_dir: Path, manifest: dict[str, Any], *, cron_expression: str) -> dict[str, Any]:
    scheduler = manifest.get("scheduler", {}).get("type", "cron")
    if scheduler != "cron":
        raise ValueError(f"execute currently supports cron scheduler only, got: {scheduler}")
    ensure_remote_root(manifest["host"], manifest["remoteRoot"])
    install_remote_runner(manifest["host"], manifest["remoteRoot"])
    sync_automation(local_automation_dir, manifest)
    sync_registry_record(local_automation_dir, manifest)
    run_result = remote_cron_set(manifest, f"{record_id(manifest)}:run", build_run_cron_block(manifest, cron_expression))
    reconcile_result = remote_cron_set(manifest, "reconcile", build_reconcile_cron_block(manifest))
    return {
        "automationId": record_id(manifest),
        "host": manifest["host"],
        "remoteRoot": manifest["remoteRoot"],
        "status": "installed",
        "runCron": run_result,
        "reconcileCron": reconcile_result,
    }


def execute_run_once(manifest: dict[str, Any]) -> dict[str, Any]:
    completed = ssh(
        manifest["host"],
        runner_remote_command(manifest, "run", "--remote-root", manifest["remoteRoot"], "--automation-id", record_id(manifest)),
    )
    return json.loads(completed.stdout)


def execute_status(manifest: dict[str, Any]) -> dict[str, Any]:
    completed = ssh(
        manifest["host"],
        runner_remote_command(manifest, "status", "--remote-root", manifest["remoteRoot"], "--automation-id", record_id(manifest)),
    )
    return json.loads(completed.stdout)


def execute_reconcile(manifest: dict[str, Any]) -> dict[str, Any]:
    completed = ssh(manifest["host"], runner_remote_command(manifest, "reconcile", "--remote-root", manifest["remoteRoot"]))
    return json.loads(completed.stdout)


def execute_pause(local_automation_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    sync_registry_record(local_automation_dir, manifest)
    cron_result = remote_cron_remove(manifest, f"{record_id(manifest)}:run")
    status_result = execute_status(manifest)
    return {"automationId": record_id(manifest), "status": "paused", "cron": cron_result, "remoteStatus": status_result}


def execute_resume(local_automation_dir: Path, manifest: dict[str, Any], *, cron_expression: str) -> dict[str, Any]:
    sync_registry_record(local_automation_dir, manifest)
    cron_result = remote_cron_set(manifest, f"{record_id(manifest)}:run", build_run_cron_block(manifest, cron_expression))
    status_result = execute_status(manifest)
    return {"automationId": record_id(manifest), "status": "active", "cron": cron_result, "remoteStatus": status_result}


def execute_delete(local_automation_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    sync_registry_record(local_automation_dir, manifest)
    cron_result = remote_cron_remove(manifest, f"{record_id(manifest)}:run")
    reconcile_result = execute_reconcile(manifest)
    return {"automationId": record_id(manifest), "status": "deleted", "cron": cron_result, "reconcile": reconcile_result}


def execute_uninstall(manifest: dict[str, Any], *, purge: bool = False) -> dict[str, Any]:
    automation_id = record_id(manifest)
    remote_root = manifest["remoteRoot"]
    remote_automation_dir = manifest.get("remoteAutomationDir") or remote_join(remote_root, "automations", automation_id)
    registry_record = manifest.get("registry", {}).get("recordPath") or remote_join(remote_root, "registry", f"{automation_id}.json")
    remote_cron_remove(manifest, f"{automation_id}:run")
    command = (
        f"rm -f {remote_shell_path(registry_record)}; "
        + (
            f"rm -rf {remote_shell_path(remote_automation_dir)}"
            if purge
            else f"mkdir -p {remote_shell_path(remote_join(remote_root, 'archive'))}; "
            f"if [ -d {remote_shell_path(remote_automation_dir)} ]; then "
            f"rm -rf {remote_shell_path(remote_join(remote_root, 'archive', automation_id))}; "
            f"mv {remote_shell_path(remote_automation_dir)} {remote_shell_path(remote_join(remote_root, 'archive', automation_id))}; "
            "fi"
        )
    )
    ssh(manifest["host"], command)
    return {"automationId": automation_id, "status": "uninstalled", "purged": purge}


def mark_deleted(manifest_path: Path, *, deleted_at: str | None = None) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    manifest["status"] = "deleted"
    manifest["deletedAt"] = deleted_at or default_now()
    lifecycle = manifest.setdefault("lifecycle", {})
    lifecycle.setdefault("deleteStrategy", "tombstone")
    lifecycle.setdefault("purgeAfterDays", 14)
    write_json(manifest_path, manifest)
    return manifest


def mark_paused(manifest_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    manifest["status"] = "paused"
    write_json(manifest_path, manifest)
    return manifest


def mark_active(manifest_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    manifest["status"] = "active"
    manifest.pop("deletedAt", None)
    write_json(manifest_path, manifest)
    return manifest


def load_registry_records(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("automations"), list):
        return payload["automations"]
    if isinstance(payload, dict) and ("automationId" in payload or "id" in payload):
        return [payload]
    raise ValueError(f"registry file must contain a record list: {path}")


def compute_registry_diff(
    desired_records: list[dict[str, Any]],
    actual_records: list[dict[str, Any]],
    *,
    now: str | None = None,
    prune_missing: bool = False,
) -> dict[str, list[str]]:
    now = now or default_now()
    desired = {record_id(record): record for record in desired_records}
    actual = {record_id(record): record for record in actual_records}
    diff = {
        "install": [],
        "update": [],
        "delete": [],
        "purge": [],
        "pruneMissing": [],
    }

    for automation_id, desired_record in desired.items():
        actual_record = actual.get(automation_id)
        status = desired_record.get("status", "active")
        if status == "deleted":
            purge_after_days = desired_record.get("lifecycle", {}).get("purgeAfterDays", 14)
            deleted_at = desired_record.get("deletedAt")
            if deleted_at and deleted_age_days(deleted_at, now) >= purge_after_days:
                diff["purge"].append(automation_id)
            elif actual_record is not None and actual_record.get("status") != "deleted":
                diff["delete"].append(automation_id)
            continue

        if actual_record is None:
            diff["install"].append(automation_id)
        elif actual_record.get("status") != status or actual_record.get("displayName") != desired_record.get("displayName"):
            diff["update"].append(automation_id)

    if prune_missing:
        for automation_id, actual_record in actual.items():
            if automation_id not in desired and actual_record.get("managedBy") == MANAGER_NAME:
                diff["pruneMissing"].append(automation_id)

    for values in diff.values():
        values.sort()
    return diff


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan or execute remote Codex automation lifecycle operations.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Print an install plan from automation remote.json.")
    install.add_argument("automation_dir", type=Path)
    install.add_argument("--execute", action="store_true", help="Sync files and install remote cron entries.")
    install.add_argument("--cron", help="Override cron expression instead of reading automation.toml RRULE.")

    uninstall = subparsers.add_parser("uninstall", help="Print an uninstall plan from automation remote.json.")
    uninstall.add_argument("automation_dir", type=Path)
    uninstall.add_argument("--purge", action="store_true", help="Plan irreversible workspace purge.")
    uninstall.add_argument("--execute", action="store_true", help="Remove remote cron entry, registry record, and archive/purge workspace.")

    delete = subparsers.add_parser("delete", help="Mark automation remote.json as deleted with a tombstone.")
    delete.add_argument("automation_dir", type=Path)
    delete.add_argument("--deleted-at")
    delete.add_argument("--execute", action="store_true", help="Sync tombstone to remote and reconcile immediately.")

    pause = subparsers.add_parser("pause", help="Mark automation remote.json as paused.")
    pause.add_argument("automation_dir", type=Path)
    pause.add_argument("--execute", action="store_true", help="Sync paused state to remote and remove run cron entry.")

    resume = subparsers.add_parser("resume", help="Mark automation remote.json as active.")
    resume.add_argument("automation_dir", type=Path)
    resume.add_argument("--execute", action="store_true", help="Sync active state to remote and install run cron entry.")
    resume.add_argument("--cron", help="Override cron expression instead of reading automation.toml RRULE.")

    run_once = subparsers.add_parser("run-once", help="Run the remote automation once through the installed runner.")
    run_once.add_argument("automation_dir", type=Path)
    run_once.add_argument("--execute", action="store_true", help="Actually run on the remote host.")

    status = subparsers.add_parser("status", help="Show remote automation runner status.")
    status.add_argument("automation_dir", type=Path)
    status.add_argument("--execute", action="store_true", help="Query the remote host.")

    reconcile = subparsers.add_parser("reconcile", help="Run the remote reconcile loop once.")
    reconcile.add_argument("automation_dir", type=Path)
    reconcile.add_argument("--execute", action="store_true", help="Actually run reconcile on the remote host.")

    diff = subparsers.add_parser("diff", help="Compare desired and actual remote registry records.")
    diff.add_argument("--desired", required=True, type=Path)
    diff.add_argument("--actual", required=True, type=Path)
    diff.add_argument("--now")
    diff.add_argument("--prune-missing", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "install":
        automation_dir = args.automation_dir.expanduser().absolute()
        manifest = load_remote_manifest(automation_dir)
        if args.execute:
            cron_expression = args.cron or load_automation_cron(automation_dir)
            payload = execute_install(automation_dir, manifest, cron_expression=cron_expression)
        else:
            payload = build_execute_install_plan(
                automation_dir,
                manifest,
                cron_expression=args.cron or load_automation_cron(automation_dir),
            )
    elif args.command == "uninstall":
        automation_dir = args.automation_dir.expanduser().absolute()
        manifest = load_remote_manifest(automation_dir)
        if args.execute:
            payload = execute_uninstall(manifest, purge=args.purge)
        else:
            payload = build_uninstall_plan(manifest, purge=args.purge)
    elif args.command == "delete":
        automation_dir = args.automation_dir.expanduser().absolute()
        manifest_path = automation_dir / "remote.json"
        manifest = mark_deleted(manifest_path, deleted_at=args.deleted_at)
        payload = execute_delete(automation_dir, manifest) if args.execute else manifest
    elif args.command == "pause":
        automation_dir = args.automation_dir.expanduser().absolute()
        manifest_path = automation_dir / "remote.json"
        manifest = mark_paused(manifest_path)
        payload = execute_pause(automation_dir, manifest) if args.execute else manifest
    elif args.command == "resume":
        automation_dir = args.automation_dir.expanduser().absolute()
        manifest_path = automation_dir / "remote.json"
        manifest = mark_active(manifest_path)
        if args.execute:
            payload = execute_resume(automation_dir, manifest, cron_expression=args.cron or load_automation_cron(automation_dir))
        else:
            payload = manifest
    elif args.command == "run-once":
        automation_dir = args.automation_dir.expanduser().absolute()
        manifest = load_remote_manifest(automation_dir)
        payload = (
            execute_run_once(manifest)
            if args.execute
            else {
                "automationId": record_id(manifest),
                "host": manifest["host"],
                "command": runner_remote_command(
                    manifest,
                    "run",
                    "--remote-root",
                    manifest["remoteRoot"],
                    "--automation-id",
                    record_id(manifest),
                ),
            }
        )
    elif args.command == "status":
        automation_dir = args.automation_dir.expanduser().absolute()
        manifest = load_remote_manifest(automation_dir)
        payload = (
            execute_status(manifest)
            if args.execute
            else {
                "automationId": record_id(manifest),
                "host": manifest["host"],
                "command": runner_remote_command(
                    manifest,
                    "status",
                    "--remote-root",
                    manifest["remoteRoot"],
                    "--automation-id",
                    record_id(manifest),
                ),
            }
        )
    elif args.command == "reconcile":
        automation_dir = args.automation_dir.expanduser().absolute()
        manifest = load_remote_manifest(automation_dir)
        payload = (
            execute_reconcile(manifest)
            if args.execute
            else {
                "host": manifest["host"],
                "command": runner_remote_command(manifest, "reconcile", "--remote-root", manifest["remoteRoot"]),
            }
        )
    elif args.command == "diff":
        payload = compute_registry_diff(
            load_registry_records(args.desired),
            load_registry_records(args.actual),
            now=args.now,
            prune_missing=args.prune_missing,
        )
    else:
        raise ValueError(f"unknown command: {args.command}")

    print(json_dump(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
