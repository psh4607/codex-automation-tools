import re
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PLUGIN_ROOT / "skills" / "automation-workspaces" / "SKILL.md"


def frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.MULTILINE)
    if not match:
        raise AssertionError(f"{key} is missing from skill frontmatter")
    return match.group(1)


class SkillsMetadataTest(unittest.TestCase):
    def test_automation_workspace_description_triggers_existing_automation_edits(self):
        description = frontmatter_value(SKILL_PATH.read_text(), "description")

        for trigger in ["automation.toml", "memory.md", "자동화 수정"]:
            self.assertIn(trigger, description)

        self.assertLessEqual(len(description), 500)


if __name__ == "__main__":
    unittest.main()
