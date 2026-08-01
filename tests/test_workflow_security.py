import re
import unittest
from pathlib import Path


class WorkflowSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository_root = Path(__file__).resolve().parents[1]
        cls.workflow = (
            cls.repository_root / ".github/workflows/weekly-news-report.yml"
        ).read_text(encoding="utf-8")

    def test_all_actions_are_pinned_to_full_commit(self):
        action_refs = re.findall(r"^\s*uses:\s*([^\s#]+)", self.workflow, re.MULTILINE)
        self.assertGreater(len(action_refs), 0)
        for action_ref in action_refs:
            with self.subTest(action=action_ref):
                self.assertRegex(action_ref, r"^[^@]+@[0-9a-f]{40}$")

    def test_collection_has_read_only_permission_and_publish_has_write(self):
        self.assertRegex(
            self.workflow,
            r"collect-and-report:[\s\S]*?permissions:\s*\n\s*contents: read",
        )
        self.assertRegex(
            self.workflow,
            r"publish-results:[\s\S]*?permissions:\s*\n\s*contents: write",
        )

    def test_unused_claude_secret_is_not_exposed(self):
        self.assertNotIn("ANTHROPIC_API_KEY", self.workflow)

    def test_dependencies_require_lockfile_hashes(self):
        self.assertIn("--require-hashes -r requirements.lock", self.workflow)
        lock_text = (self.repository_root / "requirements.lock").read_text(
            encoding="utf-8"
        )
        self.assertIn("--hash=sha256:", lock_text)


if __name__ == "__main__":
    unittest.main()
