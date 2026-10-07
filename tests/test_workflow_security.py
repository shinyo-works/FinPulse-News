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
        for name in ("weekly-news-report.yml", "import-rate-history.yml"):
            workflow = (self.repository_root / ".github/workflows" / name).read_text(encoding="utf-8")
            action_refs = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
            self.assertGreater(len(action_refs), 0)
            for action_ref in action_refs:
                with self.subTest(workflow=name, action=action_ref):
                    if action_ref.startswith("./.github/workflows/"):
                        continue  # 同じリポジトリ内の再利用ワークフロー（版はこのコミットに固定）
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
        collect_job = self.workflow.split("\n  collect-and-report:\n", 1)[1]
        collect_job_header = collect_job.split("steps:", 1)[0]
        self.assertIn("runs-on:", collect_job_header)
        self.assertNotIn("timeout-minutes", collect_job_header)

    def job_block(self, name):
        """jobs 直下の1ジョブ分（次のジョブ見出しの手前まで）を返す。"""
        match = re.search(
            rf"^  {re.escape(name)}:\n([\s\S]*?)(?=^  [a-z][a-z-]*:\n|\Z)",
            self.workflow,
            re.MULTILINE,
        )
        self.assertIsNotNone(match, name)
        return match.group(1)

    def test_main_trigger_is_external_dispatch_and_schedule_is_fallback(self):
        # 本命は cron-job.org の workflow_dispatch（08:05 JST）、schedule は 09:00 JST の控え。
        self.assertRegex(self.workflow, r"schedule:\s*\n\s*- cron: '0 0 \* \* 1'")
        self.assertEqual(len(re.findall(r"- cron:", self.workflow)), 1)
        for name in ("force", "check_only"):
            with self.subTest(input=name):
                self.assertRegex(
                    self.workflow,
                    rf"{name}:\s*\n\s*description: [^\n]+\n\s*type: boolean\s*\n\s*default: false",
                )

    def test_guard_can_only_read_and_gets_no_secrets(self):
        guard = self.job_block("guard")
        self.assertRegex(guard, r"permissions:\s*\n\s*actions: read\s*\n\s*contents: read")
        self.assertNotIn("write", guard)
        self.assertNotIn("secrets.", guard)
        self.assertIn("run: python3 scripts/check_weekly_run.py", guard)
        # 判定の失敗で控えまで止めないよう、判定ステップは失敗しても先へ進む。
        self.assertRegex(guard, r"id: decide\s*\n\s*continue-on-error: true")

    def test_collection_waits_for_guard_and_fails_open(self):
        collect = self.job_block("collect-and-report")
        self.assertIn("needs: guard", collect)
        self.assertIn("!cancelled()", collect)
        self.assertIn("github.ref == 'refs/heads/main'", collect)
        # 「実行しない」と明示された時だけ止める（出力なし＝判定失敗は実行する）。
        self.assertIn("needs.guard.outputs.should_run != 'false'", collect)
        # 接続テストは判定の成否に関係なく収集しない（本物のメールを送らない）。
        # API から文字列 "true" で届いても真偽値で届いても止まるよう、文字列にそろえて比べる。
        self.assertIn("format('{0}', inputs.check_only) != 'true'", collect)

    def test_publish_runs_only_after_real_collection(self):
        publish = self.job_block("publish-results")
        self.assertIn("needs: collect-and-report", publish)
        self.assertIn("needs.collect-and-report.result == 'success'", publish)
        self.assertIn("needs.collect-and-report.result == 'failure'", publish)
        # 収集が skipped（判定で省略・接続テスト）の時は公開しない＝if に skipped を許す条件を入れない。
        publish_if = publish.split("runs-on:", 1)[0]
        self.assertNotIn("result == 'skipped'", publish_if)

    def test_guard_required_jobs_match_workflow_job_keys(self):
        # 判定は Actions API のジョブ名で「収集と公開をやり遂げたか」を見る。ジョブ名はキー名と
        # 一致する前提なので、ジョブにジョブ単位の name: を付けたり改名したりしたらここで落とす
        # （落とさないと控えが毎週取り直しを続ける）。
        from scripts.check_weekly_run import REQUIRED_JOBS

        for job_name in REQUIRED_JOBS:
            with self.subTest(job=job_name):
                block = self.job_block(job_name)
                self.assertIsNone(re.search(r"^    name:", block, re.MULTILINE))

    def test_runs_are_serialized_so_fallback_sees_finished_main_run(self):
        self.assertRegex(
            self.workflow,
            re.compile(
                r"^concurrency:\s*\n\s*group: weekly-news-report-main\s*\n\s*cancel-in-progress: false",
                re.MULTILINE,
            ),
        )

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



class RateImportWorkflowTest(unittest.TestCase):
    """2026-10-07 監査 W2: 金利履歴は、こちらから読み取り専用の鍵で取りに行って自分で公開する。"""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.importer = (root / ".github/workflows/import-rate-history.yml").read_text(encoding="utf-8")
        cls.weekly = (root / ".github/workflows/weekly-news-report.yml").read_text(encoding="utf-8")

    def test_feed_is_read_with_read_only_key_and_not_persisted(self):
        self.assertIn("ssh-key: ${{ secrets.RATE_FEED_READ_KEY }}", self.importer)
        self.assertIn("repository: ${{ secrets.RATE_FEED_REPOSITORY }}", self.importer)
        self.assertIn("ref: rate-feed", self.importer)
        feed_step = self.importer.split("- name: 公開用の金利データを読み取り専用の鍵で取得", 1)[1].split("- name:", 1)[0]
        self.assertIn("persist-credentials: false", feed_step)

    def test_private_repository_name_is_not_written_in_public_workflow(self):
        # 公開ログと公開リポジトリに、非公開の取得元の名前を出さない（Secret から渡す）
        for text in (self.importer, self.weekly):
            for line in re.findall(r"^\s*repository:.*$", text, re.MULTILINE):
                with self.subTest(line=line):
                    self.assertIn("${{ secrets.RATE_FEED_REPOSITORY }}", line)

    def test_only_newer_by_default_and_manual_reimport_is_explicit(self):
        self.assertIn('update_rate_history.py "$feed" --only-newer', self.importer)
        self.assertIn("REIMPORT_SAME_DAY: ${{ inputs.reimport_same_day }}", self.importer)
        self.assertNotIn("${{ inputs.reimport_same_day }}\n", self.importer.split("run: |", 1)[1])

    def test_permissions_are_minimal(self):
        self.assertRegex(self.importer, r"(?m)^permissions: \{\}$")
        self.assertRegex(self.importer, r"import:[\s\S]*?permissions:\s*\n\s*contents: write")
        self.assertIn("github.ref == 'refs/heads/main'", self.importer)

    def test_weekly_calls_import_except_connection_test(self):
        block = self.weekly.split("\n  import-rate-history:\n", 1)[1].split("\n  collect-and-report:\n", 1)[0]
        self.assertIn("uses: ./.github/workflows/import-rate-history.yml", block)
        self.assertIn("format('{0}', inputs.check_only) != 'true'", block)
        self.assertNotIn("secrets: inherit", block)
        self.assertNotIn("needs:", block)


if __name__ == "__main__":
    unittest.main()
