#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


MANAGER_NAME = "codex-automation-tools"


def json_dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def load_json(path: Path) -> Any:
    return json.loads(path.expanduser().read_text())


def write_json(path: Path, payload: Any) -> None:
    path.expanduser().write_text(json_dump(payload))


def remote_join(root: str, *parts: str) -> str:
    return "/".join([root.rstrip("/"), *parts])


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


def load_remote_manifest(automation_dir: Path) -> dict[str, Any]:
    manifest_path = automation_dir.expanduser() / "remote.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"remote manifest does not exist: {manifest_path}")
    manifest = load_json(manifest_path)
    if manifest.get("mode") != "remote-host":
        raise ValueError(f"remote manifest mode must be remote-host: {manifest_path}")
    return manifest


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
                "exclude": ["scripts/*/history", "scripts/*/artifacts", "scripts/*/tmp", "scripts/*/logs"],
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
    parser = argparse.ArgumentParser(description="Plan remote Codex automation lifecycle operations.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Print an install plan from automation remote.json.")
    install.add_argument("automation_dir", type=Path)

    uninstall = subparsers.add_parser("uninstall", help="Print an uninstall plan from automation remote.json.")
    uninstall.add_argument("automation_dir", type=Path)
    uninstall.add_argument("--purge", action="store_true", help="Plan irreversible workspace purge.")

    delete = subparsers.add_parser("delete", help="Mark automation remote.json as deleted with a tombstone.")
    delete.add_argument("automation_dir", type=Path)
    delete.add_argument("--deleted-at")

    pause = subparsers.add_parser("pause", help="Mark automation remote.json as paused.")
    pause.add_argument("automation_dir", type=Path)

    resume = subparsers.add_parser("resume", help="Mark automation remote.json as active.")
    resume.add_argument("automation_dir", type=Path)

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
        payload = build_install_plan(automation_dir, load_remote_manifest(automation_dir))
    elif args.command == "uninstall":
        automation_dir = args.automation_dir.expanduser().absolute()
        payload = build_uninstall_plan(load_remote_manifest(automation_dir), purge=args.purge)
    elif args.command == "delete":
        manifest_path = args.automation_dir.expanduser().absolute() / "remote.json"
        payload = mark_deleted(manifest_path, deleted_at=args.deleted_at)
    elif args.command == "pause":
        manifest_path = args.automation_dir.expanduser().absolute() / "remote.json"
        payload = mark_paused(manifest_path)
    elif args.command == "resume":
        manifest_path = args.automation_dir.expanduser().absolute() / "remote.json"
        payload = mark_active(manifest_path)
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
