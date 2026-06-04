#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Callable


DEFAULT_REGISTRY = Path.home() / ".codex" / "remote-hosts.json"
DEFAULT_SSH_CONFIG = Path.home() / ".ssh" / "config"
MANAGER_NAME = "codex-automation-tools"


def json_dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def parse_ssh_config_aliases(text: str) -> list[str]:
    aliases: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if not parts or parts[0].lower() != "host":
            continue
        for alias in parts[1:]:
            if alias.startswith("!") or "*" in alias or "?" in alias:
                continue
            if alias not in aliases:
                aliases.append(alias)
    return aliases


def parse_ssh_g_output(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    identity_files: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        key = key.lower()
        value = value.strip()
        if key == "hostname":
            payload["hostname"] = value
        elif key == "user":
            payload["user"] = value
        elif key == "port":
            payload["port"] = value
        elif key == "identityfile":
            identity_files.append(value)
        elif key == "proxycommand":
            payload["proxyCommand"] = value
        elif key == "proxyjump":
            payload["proxyJump"] = value
    if identity_files:
        payload["identityFiles"] = identity_files
    return payload


def resolve_ssh_config(alias: str) -> str:
    completed = subprocess.run(
        ["ssh", "-G", alias],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def host_record(
    alias: str,
    *,
    ssh_metadata: dict[str, Any] | None = None,
    role: str = "automation-runner",
    remote_root: str = "~/.codex/remote-automations",
    scheduler: str = "systemd-timer",
    reconcile_interval_hours: int = 6,
    status: str = "configured",
) -> dict[str, Any]:
    return {
        "id": alias,
        "sshAlias": alias,
        "role": role,
        "remoteRoot": remote_root,
        "scheduler": scheduler,
        "reconcileIntervalHours": reconcile_interval_hours,
        "source": "ssh-config",
        "status": status,
        "ssh": ssh_metadata or {},
    }


def discover_hosts(
    *,
    ssh_config_text: str,
    include: list[str] | None = None,
    resolver: Callable[[str], str] | None = None,
    role: str = "automation-runner",
    remote_root: str = "~/.codex/remote-automations",
    scheduler: str = "systemd-timer",
    reconcile_interval_hours: int = 6,
) -> list[dict[str, Any]]:
    aliases = parse_ssh_config_aliases(ssh_config_text)
    if include:
        include_set = set(include)
        missing = sorted(include_set.difference(aliases))
        if missing:
            raise ValueError(f"SSH host alias is not present in config: {', '.join(missing)}")
        aliases = [alias for alias in aliases if alias in include_set]
    resolver = resolver or resolve_ssh_config
    records: list[dict[str, Any]] = []
    for alias in aliases:
        status = "configured"
        try:
            ssh_metadata = parse_ssh_g_output(resolver(alias))
        except (OSError, subprocess.CalledProcessError):
            ssh_metadata = {}
            status = "unresolved"
        records.append(
            host_record(
                alias,
                ssh_metadata=ssh_metadata,
                role=role,
                remote_root=remote_root,
                scheduler=scheduler,
                reconcile_interval_hours=reconcile_interval_hours,
                status=status,
            )
        )
    return records


def load_registry(path: Path) -> dict[str, Any]:
    path = path.expanduser()
    if not path.exists():
        return {"schemaVersion": 1, "managedBy": MANAGER_NAME, "hosts": []}
    text = path.read_text().strip()
    if not text:
        return {"schemaVersion": 1, "managedBy": MANAGER_NAME, "hosts": []}
    payload = json.loads(text)
    payload.setdefault("schemaVersion", 1)
    payload.setdefault("managedBy", MANAGER_NAME)
    payload.setdefault("hosts", [])
    return payload


def write_registry(path: Path, hosts: list[dict[str, Any]]) -> dict[str, Any]:
    path = path.expanduser()
    payload = load_registry(path)
    by_id = {host["id"]: host for host in payload.get("hosts", [])}
    for host in hosts:
        by_id[host["id"]] = host
    payload["hosts"] = [by_id[key] for key in sorted(by_id)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dump(payload))
    return payload


def find_host(registry: dict[str, Any], host_id_or_alias: str) -> dict[str, Any]:
    for host in registry.get("hosts", []):
        if host.get("id") == host_id_or_alias or host.get("sshAlias") == host_id_or_alias:
            return host
    raise KeyError(f"remote host is not registered: {host_id_or_alias}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage Codex remote automation host registry.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="Discover SSH aliases and write remote-hosts.json.")
    discover.add_argument("--ssh-config", type=Path, default=DEFAULT_SSH_CONFIG)
    discover.add_argument("--output", type=Path, default=DEFAULT_REGISTRY)
    discover.add_argument("--include", action="append", default=[])
    discover.add_argument("--role", default="automation-runner")
    discover.add_argument("--remote-root", default="~/.codex/remote-automations")
    discover.add_argument("--scheduler", choices=("systemd-timer", "cron"), default="systemd-timer")
    discover.add_argument("--reconcile-interval-hours", type=int, default=6)

    list_cmd = subparsers.add_parser("list", help="Print registered remote hosts.")
    list_cmd.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)

    show = subparsers.add_parser("show", help="Show one registered remote host.")
    show.add_argument("host")
    show.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "discover":
        ssh_config_text = args.ssh_config.expanduser().read_text()
        hosts = discover_hosts(
            ssh_config_text=ssh_config_text,
            include=args.include or None,
            role=args.role,
            remote_root=args.remote_root,
            scheduler=args.scheduler,
            reconcile_interval_hours=args.reconcile_interval_hours,
        )
        payload = write_registry(args.output, hosts)
    elif args.command == "list":
        payload = load_registry(args.registry)
    elif args.command == "show":
        payload = find_host(load_registry(args.registry), args.host)
    else:
        raise ValueError(f"unknown command: {args.command}")
    print(json_dump(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
