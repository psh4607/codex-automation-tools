import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "manage_remote_hosts.py"


def load_module():
    spec = importlib.util.spec_from_file_location("manage_remote_hosts", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ManageRemoteHostsTest(unittest.TestCase):
    def test_parse_ssh_config_lists_concrete_aliases_only(self):
        module = load_module()

        aliases = module.parse_ssh_config_aliases(
            """
Host *
  ServerAliveInterval 60

Host dalpha-mac hyunmoo
  HostName ssh-d.seongho.dev

Host !blocked *.internal
  User nobody
"""
        )

        self.assertEqual(aliases, ["dalpha-mac", "hyunmoo"])

    def test_parse_ssh_g_output_extracts_non_secret_connection_metadata(self):
        module = load_module()

        payload = module.parse_ssh_g_output(
            """
host dalpha-mac
user soengho
hostname ssh-d.seongho.dev
port 22
identityfile ~/.ssh/keys/dalpha-mac
proxycommand cloudflared access ssh --hostname %h
"""
        )

        self.assertEqual(payload["hostname"], "ssh-d.seongho.dev")
        self.assertEqual(payload["user"], "soengho")
        self.assertEqual(payload["port"], "22")
        self.assertEqual(payload["identityFiles"], ["~/.ssh/keys/dalpha-mac"])
        self.assertEqual(payload["proxyCommand"], "cloudflared access ssh --hostname %h")

    def test_discover_hosts_uses_include_and_resolver(self):
        module = load_module()

        def fake_resolver(alias):
            self.assertEqual(alias, "dalpha-mac")
            return """
host dalpha-mac
user soengho
hostname ssh-d.seongho.dev
port 22
"""

        hosts = module.discover_hosts(
            ssh_config_text="Host dalpha-mac\n  HostName ssh-d.seongho.dev\nHost hyunmoo\n",
            include=["dalpha-mac"],
            resolver=fake_resolver,
        )

        self.assertEqual(len(hosts), 1)
        self.assertEqual(hosts[0]["id"], "dalpha-mac")
        self.assertEqual(hosts[0]["role"], "automation-runner")
        self.assertEqual(hosts[0]["remoteRoot"], "~/.codex/remote-automations")
        self.assertEqual(hosts[0]["scheduler"], "systemd-timer")
        self.assertEqual(hosts[0]["reconcileIntervalHours"], 6)
        self.assertEqual(hosts[0]["ssh"]["hostname"], "ssh-d.seongho.dev")

    def test_discover_hosts_rejects_missing_include(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.discover_hosts(
                ssh_config_text="Host hyunmoo\n  HostName hyunmoo.local\n",
                include=["dalpha-mac"],
                resolver=lambda alias: "",
            )

    def test_write_registry_merges_existing_hosts(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "remote-hosts.json"
            registry.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "managedBy": "codex-automation-tools",
                        "hosts": [
                            {
                                "id": "existing",
                                "sshAlias": "existing",
                                "role": "automation-runner",
                            }
                        ],
                    }
                )
            )

            payload = module.write_registry(
                registry,
                [
                    {
                        "id": "dalpha-mac",
                        "sshAlias": "dalpha-mac",
                        "role": "automation-runner",
                        "remoteRoot": "~/.codex/remote-automations",
                    }
                ],
            )

            self.assertEqual([host["id"] for host in payload["hosts"]], ["dalpha-mac", "existing"])
            self.assertEqual(json.loads(registry.read_text()), payload)

    def test_write_registry_treats_empty_file_as_new_registry(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "remote-hosts.json"
            registry.write_text("")

            payload = module.write_registry(
                registry,
                [
                    {
                        "id": "dalpha-mac",
                        "sshAlias": "dalpha-mac",
                        "role": "automation-runner",
                    }
                ],
            )

            self.assertEqual(payload["managedBy"], "codex-automation-tools")
            self.assertEqual(payload["hosts"][0]["id"], "dalpha-mac")

    def test_find_host_matches_id_or_ssh_alias(self):
        module = load_module()
        registry = {
            "hosts": [
                {"id": "runner-1", "sshAlias": "dalpha-mac", "remoteRoot": "/srv/codex"},
            ]
        }

        self.assertEqual(module.find_host(registry, "runner-1")["sshAlias"], "dalpha-mac")
        self.assertEqual(module.find_host(registry, "dalpha-mac")["id"], "runner-1")


if __name__ == "__main__":
    unittest.main()
