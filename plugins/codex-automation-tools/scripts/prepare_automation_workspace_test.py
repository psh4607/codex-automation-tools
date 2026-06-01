import importlib.util
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
            for dirname in ["scripts", "data", "templates", "docs", "tmp", "logs"]:
                self.assertTrue((automation_dir / dirname).is_dir(), dirname)

            script = automation_dir / "scripts" / "run-check.mjs"
            test_file = automation_dir / "scripts" / "run-check.test.mjs"
            self.assertTrue(script.exists())
            self.assertTrue(test_file.exists())
            self.assertIn("export function main", script.read_text())
            self.assertIn("daily-report-check", script.read_text())

    def test_does_not_overwrite_existing_script_by_default(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            script_dir = Path(tmp) / "existing-automation" / "scripts"
            script_dir.mkdir(parents=True)
            script = script_dir / "worker.mjs"
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


if __name__ == "__main__":
    unittest.main()
