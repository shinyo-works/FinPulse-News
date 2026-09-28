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

    def test_only_collection_step_has_wall_clock_timeout(self):
        self.assertRegex(
            self.workflow,
            r"- name: 収集・レポート生成・送信[\s\S]*?timeout-minutes: 15[\s\S]*?run: python scripts/collect_and_send.py",
        )
        # ジョブ全体を打ち切ると、if: always() の成果物保存まで失うため設定しない。
        collect_job_header = self.workflow.split("steps:", 1)[0]
        self.assertNotIn("timeout-minutes", collect_job_header)

    def test_data_sync_check_does_not_block_news_delivery(self):
        # 上流の商品増減で条件比較との突合が落ちても、ニュース収集・送信は止めない。
        self.assertRegex(
            self.workflow,
            r"- name: 自動テスト\s*\n\s*env:[\s\S]*?FINPULSE_DEFER_DATA_SYNC_TESTS: '1'",
        )
        self.assertRegex(
            self.workflow,
            r"- name: 条件比較データと金利履歴の突合[^\n]*\n\s*if: always\(\)\s*\n"
            r"\s*continue-on-error: true\s*\n\s*run: \|\s*\n\s*python -m unittest tests.test_loan_features",
        )

    def test_publish_push_retries_with_rebase(self):
        # 別リポジトリの自動pushとの競合時に、取り込み直して再pushするループがあること
        self.assertIn("git pull --rebase --autostash origin main", self.workflow)
        self.assertRegex(self.workflow, r"for attempt in 1 2 3[\s\S]*?git push origin main")

    def test_dependencies_require_lockfile_hashes(self):
        self.assertIn("--require-hashes -r requirements.lock", self.workflow)
        lock_text = (self.repository_root / "requirements.lock").read_text(
            encoding="utf-8"
        )
        self.assertIn("--hash=sha256:", lock_text)


if __name__ == "__main__":
    unittest.main()
