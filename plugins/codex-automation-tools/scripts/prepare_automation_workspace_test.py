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


if __name__ == "__main__":
    unittest.main()
