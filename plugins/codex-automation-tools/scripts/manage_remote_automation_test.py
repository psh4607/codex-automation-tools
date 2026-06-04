import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "manage_remote_automation.py"


def load_module():
    spec = importlib.util.spec_from_file_location("manage_remote_automation", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ManageRemoteAutomationTest(unittest.TestCase):
    def test_build_install_plan_uses_remote_manifest(self):
        module = load_module()
        manifest = {
            "automationId": "daily-report-check",
            "displayName": "[remote] Daily Report Check",
            "host": "dalpha-mac",
            "remoteRoot": "~/.codex/remote-automations",
            "scheduler": {"type": "systemd-timer", "reconcileIntervalHours": 6},
            "status": "active",
        }

        plan = module.build_install_plan(Path("/tmp/local/daily-report-check"), manifest)

        self.assertEqual(plan["automationId"], "daily-report-check")
        self.assertEqual(plan["host"], "dalpha-mac")
        self.assertEqual(plan["displayName"], "[remote] Daily Report Check")
        self.assertEqual(
            [action["kind"] for action in plan["actions"]],
            ["ensure-remote-root", "sync-automation", "install-scheduler", "write-registry-record"],
        )
        self.assertEqual(plan["actions"][1]["target"], "~/.codex/remote-automations/automations/daily-report-check")

    def test_mark_deleted_writes_tombstone_without_purging_workspace(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "remote.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "automationId": "daily-report-check",
                        "displayName": "[remote] Daily Report Check",
                        "status": "active",
                        "lifecycle": {"purgeAfterDays": 14},
                    }
                )
            )

            result = module.mark_deleted(
                manifest_path,
                deleted_at="2026-06-04T10:00:00+09:00",
            )
            payload = json.loads(manifest_path.read_text())

            self.assertEqual(result["status"], "deleted")
            self.assertEqual(payload["status"], "deleted")
            self.assertEqual(payload["deletedAt"], "2026-06-04T10:00:00+09:00")
            self.assertEqual(payload["displayName"], "[remote] Daily Report Check")
            self.assertEqual(payload["lifecycle"]["purgeAfterDays"], 14)

    def test_pause_and_resume_update_manifest_status(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "remote.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "automationId": "daily-report-check",
                        "displayName": "[remote] Daily Report Check",
                        "status": "active",
                    }
                )
            )

            paused = module.mark_paused(manifest_path)
            resumed = module.mark_active(manifest_path)

            self.assertEqual(paused["status"], "paused")
            self.assertEqual(resumed["status"], "active")
            self.assertEqual(json.loads(manifest_path.read_text())["status"], "active")

    def test_registry_diff_uses_tombstones_and_prune_missing_is_explicit(self):
        module = load_module()
        desired = [
            {
                "automationId": "daily-report-check",
                "status": "deleted",
                "deletedAt": "2026-06-01T00:00:00+09:00",
                "lifecycle": {"purgeAfterDays": 14},
            }
        ]
        actual = [
            {"automationId": "daily-report-check", "status": "active", "managedBy": "codex-automation-tools"},
            {"automationId": "orphaned-check", "status": "active", "managedBy": "codex-automation-tools"},
        ]

        diff = module.compute_registry_diff(
            desired,
            actual,
            now="2026-06-04T00:00:00+09:00",
            prune_missing=False,
        )

        self.assertEqual(diff["delete"], ["daily-report-check"])
        self.assertEqual(diff["purge"], [])
        self.assertEqual(diff["pruneMissing"], [])

        prune_diff = module.compute_registry_diff(
            desired,
            actual,
            now="2026-06-04T00:00:00+09:00",
            prune_missing=True,
        )

        self.assertEqual(prune_diff["pruneMissing"], ["orphaned-check"])

    def test_registry_diff_purges_after_retention_window(self):
        module = load_module()
        desired = [
            {
                "automationId": "daily-report-check",
                "status": "deleted",
                "deletedAt": "2026-05-01T00:00:00+09:00",
                "lifecycle": {"purgeAfterDays": 14},
            }
        ]
        actual = [
            {"automationId": "daily-report-check", "status": "deleted", "managedBy": "codex-automation-tools"}
        ]

        diff = module.compute_registry_diff(
            desired,
            actual,
            now="2026-06-04T00:00:00+09:00",
            prune_missing=False,
        )

        self.assertEqual(diff["delete"], [])
        self.assertEqual(diff["purge"], ["daily-report-check"])


if __name__ == "__main__":
    unittest.main()
