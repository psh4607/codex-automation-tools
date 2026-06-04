import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "prepare_automation_workspace.py"


def load_module():
    spec = importlib.util.spec_from_file_location("prepare_automation_workspace", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PrepareAutomationWorkspaceTest(unittest.TestCase):
    def test_creates_standard_workspace_and_node_entrypoints(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            result = module.prepare_workspace(
                root=Path(tmp),
                automation_id="daily-report-check",
                script_name="run-check",
                language="node",
            )

            automation_dir = Path(tmp) / "daily-report-check"
            self.assertEqual(result["automation_dir"], str(automation_dir))
            for dirname in ["scripts", "docs"]:
                self.assertTrue((automation_dir / dirname).is_dir(), dirname)

            script_dir = automation_dir / "scripts" / "run-check"
            for dirname in ["artifacts", "history", "tmp", "logs", "context", "memory"]:
                self.assertTrue((script_dir / dirname).is_dir(), dirname)

            script = script_dir / "main.mjs"
            test_file = script_dir / "main.test.mjs"
            context_files = [
                "automation.json",
                "repo.json",
                "codebase.json",
                "env.json",
                "db.json",
                "integrations.json",
            ]
            self.assertTrue(script.exists())
            self.assertTrue(test_file.exists())
            for filename in context_files:
                self.assertTrue((script_dir / "context" / filename).exists(), filename)
            self.assertTrue((script_dir / "memory" / "decisions.md").exists())
            self.assertTrue((script_dir / "memory" / "assumptions.md").exists())
            self.assertTrue((script_dir / "history" / "runs.jsonl").exists())
            self.assertTrue((script_dir / "artifacts" / "latest-result.json").exists())
            self.assertIn("export function main", script.read_text())
            self.assertIn("daily-report-check", script.read_text())
            self.assertIn("artifactsDir", script.read_text())
            self.assertIn("historyDir", script.read_text())
            self.assertIn("contextDir", script.read_text())
            self.assertIn("memoryDir", script.read_text())
            env_payload = json.loads((script_dir / "context" / "env.json").read_text())
            self.assertEqual(env_payload["policy"]["storeSecretValues"], False)
            self.assertEqual(env_payload["policy"]["storeRawEnvFiles"], False)
            db_payload = json.loads((script_dir / "context" / "db.json").read_text())
            self.assertEqual(db_payload["defaultAccessMode"], "read-only")
            self.assertEqual(result["script_dir"], str(script_dir))

    def test_does_not_overwrite_existing_script_by_default(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            script_dir = Path(tmp) / "existing-automation" / "scripts" / "worker"
            script_dir.mkdir(parents=True)
            script = script_dir / "main.mjs"
            script.write_text("custom script\n")

            result = module.prepare_workspace(
                root=Path(tmp),
                automation_id="existing-automation",
                script_name="worker",
                language="node",
            )

            self.assertEqual(script.read_text(), "custom script\n")
            self.assertIn(str(script), result["existing_files"])

    def test_rejects_path_traversal_ids(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                module.prepare_workspace(
                    root=Path(tmp),
                    automation_id="../escape",
                    script_name="worker",
                    language="node",
                )

    def test_generated_node_script_prints_json_when_executed(self):
        if shutil.which("node") is None:
            self.skipTest("node is not available")

        module = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            result = module.prepare_workspace(
                root=Path(tmp),
                automation_id="daily-report-check",
                script_name="run-check",
                language="node",
            )

            completed = subprocess.run(
                ["node", result["script"], "--json"],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)

            self.assertEqual(payload["scriptName"], "run-check")
            self.assertTrue(payload["paths"]["artifactsDir"].endswith("/artifacts"))
            self.assertTrue(payload["paths"]["contextDir"].endswith("/context"))
            self.assertTrue(payload["paths"]["memoryDir"].endswith("/memory"))

    def test_remote_host_creates_remote_manifest_and_title_prefix(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            result = module.prepare_workspace(
                root=Path(tmp),
                automation_id="daily-report-check",
                script_name="run-check",
                language="node",
                title="Daily Report Check",
                remote_host="dalpha-mac",
            )

            automation_dir = Path(tmp) / "daily-report-check"
            remote_manifest = automation_dir / "remote.json"
            payload = json.loads(remote_manifest.read_text())

            self.assertEqual(result["suggested_name"], "[remote] Daily Report Check")
            self.assertEqual(result["remote_manifest"], str(remote_manifest))
            self.assertEqual(payload["mode"], "remote-host")
            self.assertEqual(payload["displayName"], "[remote] Daily Report Check")
            self.assertEqual(payload["host"], "dalpha-mac")
            self.assertEqual(payload["remoteRoot"], "~/.codex/remote-automations")
            self.assertEqual(payload["scheduler"]["reconcileIntervalHours"], 6)
            self.assertEqual(payload["lifecycle"]["deleteStrategy"], "tombstone")
            self.assertEqual(payload["sync"]["history"], "remote-owned")
            self.assertEqual(payload["sync"]["artifacts"], "remote-owned")

    def test_remote_host_registry_overrides_runtime_defaults(self):
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
                                "id": "runner-1",
                                "sshAlias": "dalpha-mac",
                                "remoteRoot": "/srv/codex-remote",
                                "scheduler": "cron",
                                "reconcileIntervalHours": 24,
                            }
                        ],
                    }
                )
            )

            result = module.prepare_workspace(
                root=Path(tmp) / "automations",
                automation_id="daily-report-check",
                script_name="run-check",
                language="node",
                title="Daily Report Check",
                remote_host="runner-1",
                remote_host_registry=registry,
            )

            payload = json.loads(Path(result["remote_manifest"]).read_text())
            self.assertEqual(payload["host"], "dalpha-mac")
            self.assertEqual(payload["hostId"], "runner-1")
            self.assertEqual(payload["remoteRoot"], "/srv/codex-remote")
            self.assertEqual(payload["scheduler"]["type"], "cron")
            self.assertEqual(payload["scheduler"]["reconcileIntervalHours"], 24)
            self.assertEqual(payload["hostRegistry"], str(registry))


if __name__ == "__main__":
    unittest.main()
