import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts import check_weekly_run as guard

# 2026-10-05（月）08:05 JST = 2026-10-04 23:05 UTC
MONDAY_0805_JST = datetime(2026, 10, 4, 23, 5, tzinfo=timezone.utc)
# 同じ月曜の 09:40 JST（控えが遅れて起動した想定）
MONDAY_0940_JST = datetime(2026, 10, 5, 0, 40, tzinfo=timezone.utc)

DONE_JOBS = [
    {"name": "guard", "conclusion": "success"},
    {"name": "collect-and-report", "conclusion": "success"},
    {"name": "publish-results", "conclusion": "success"},
]
# 接続テスト（check_only）や省略した実行: 判定だけ成功し、残りは skipped。実行全体は success になる。
SKIPPED_JOBS = [
    {"name": "guard", "conclusion": "success"},
    {"name": "collect-and-report", "conclusion": "skipped"},
    {"name": "publish-results", "conclusion": "skipped"},
]


def run(run_id, conclusion, created_at, event="workflow_dispatch"):
    return {"id": run_id, "conclusion": conclusion, "created_at": created_at, "event": event}


class JstDayStartTest(unittest.TestCase):
    def test_day_start_is_jst_midnight_even_when_utc_date_is_sunday(self):
        # UTC ではまだ日曜でも、JST の月曜 00:00（= 日曜 15:00 UTC）を返す
        self.assertEqual(
            guard.jst_day_start_utc(MONDAY_0805_JST),
            datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc),
        )

    def test_day_start_for_fallback_time(self):
        self.assertEqual(
            guard.jst_day_start_utc(MONDAY_0940_JST),
            datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc),
        )


class DecideTest(unittest.TestCase):
    def decide(self, runs, *, jobs=None, check_only="", force="", current="999", now=MONDAY_0940_JST):
        """jobs は {run_id: ジョブ一覧 or None(取得失敗)}。指定のない run はやり遂げた扱い。"""
        jobs = jobs or {}
        self.fetched = []

        def fetch_jobs(run_id):
            self.fetched.append(run_id)
            return jobs.get(run_id, DONE_JOBS)

        return guard.decide(
            check_only=check_only,
            force=force,
            runs=runs,
            current_run_id=current,
            now=now,
            fetch_jobs=fetch_jobs,
        )

    def test_fallback_skips_when_main_run_did_real_work_today(self):
        should_run, reason = self.decide([run(1, "success", "2026-10-04T23:05:10Z")])
        self.assertFalse(should_run)
        self.assertIn("run 1", reason)

    def test_check_only_run_does_not_suppress_real_run_same_day(self):
        # 月曜 08:05 より前に接続テストをしても、本命と控えは止まらない（全体 success でも数えない）
        should_run, _ = self.decide(
            [run(1, "success", "2026-10-04T22:30:00Z")], jobs={1: SKIPPED_JOBS}
        )
        self.assertTrue(should_run)

    def test_skipped_run_is_ignored_but_earlier_real_run_counts(self):
        runs = [
            run(2, "success", "2026-10-05T00:40:00Z", event="schedule"),  # 省略した控え
            run(1, "success", "2026-10-04T23:05:10Z"),  # やり遂げた本命
        ]
        should_run, _ = self.decide(runs, jobs={2: SKIPPED_JOBS, 1: DONE_JOBS})
        self.assertFalse(should_run)
        self.assertEqual(self.fetched, [2, 1])

    def test_publish_failure_does_not_count(self):
        jobs = [
            {"name": "collect-and-report", "conclusion": "success"},
            {"name": "publish-results", "conclusion": "failure"},
        ]
        should_run, _ = self.decide([run(1, "success", "2026-10-04T23:05:10Z")], jobs={1: jobs})
        self.assertTrue(should_run)

    def test_unverifiable_jobs_do_not_count(self):
        # ジョブ一覧の取得に失敗した実行は数えない（止めるより動かす）
        should_run, _ = self.decide([run(1, "success", "2026-10-04T23:05:10Z")], jobs={1: None})
        self.assertTrue(should_run)

    def test_failed_runs_are_not_even_looked_up(self):
        should_run, _ = self.decide([run(1, "failure", "2026-10-04T23:05:10Z")])
        self.assertTrue(should_run)
        self.assertEqual(self.fetched, [])

    def test_fallback_runs_when_no_run_today(self):
        # cron-job.org が止まった週: 今日の実行が1件もない
        should_run, _ = self.decide([])
        self.assertTrue(should_run)

    def test_cancelled_or_in_progress_runs_do_not_count_as_success(self):
        runs = [
            run(1, "cancelled", "2026-10-04T23:05:10Z"),
            run(2, None, "2026-10-04T23:30:00Z"),
        ]
        should_run, _ = self.decide(runs)
        self.assertTrue(should_run)

    def test_success_from_previous_week_is_ignored(self):
        # 先週月曜の成功は、今日の判定に使わない（JST の日付境界の直前も含む）
        runs = [
            run(1, "success", "2026-09-27T23:05:00Z"),
            run(2, "success", "2026-10-04T14:59:59Z"),  # JST 日曜 23:59:59
        ]
        should_run, _ = self.decide(runs)
        self.assertTrue(should_run)

    def test_success_right_after_jst_midnight_counts(self):
        should_run, _ = self.decide([run(1, "success", "2026-10-04T15:00:00Z")])
        self.assertFalse(should_run)

    def test_current_run_is_excluded(self):
        # 自分自身は判定中で conclusion が無いはずだが、念のため id でも除外する
        should_run, _ = self.decide([run(999, "success", "2026-10-04T23:05:10Z")], current="999")
        self.assertTrue(should_run)

    def test_force_runs_even_after_success(self):
        should_run, _ = self.decide([run(1, "success", "2026-10-04T23:05:10Z")], force="true")
        self.assertTrue(should_run)

    def test_check_only_never_runs_even_with_force(self):
        should_run, reason = self.decide([], check_only="true", force="true")
        self.assertFalse(should_run)
        self.assertIn("接続確認のみ", reason)

    def test_failed_listing_falls_back_to_running(self):
        should_run, _ = self.decide(None)
        self.assertTrue(should_run)

    def test_inputs_are_true_only_when_explicit(self):
        for value in ("", "false", "False", None, "yes", "1"):
            with self.subTest(value=value):
                self.assertFalse(guard.is_true(value))
        for value in ("true", "True", " TRUE "):
            with self.subTest(value=value):
                self.assertTrue(guard.is_true(value))

    def test_broken_run_entries_are_ignored(self):
        runs = [
            {"id": 1, "conclusion": "success"},
            run(2, "success", 12345),
            run(3, "success", "not-a-date"),
        ]
        should_run, _ = self.decide(runs)
        self.assertTrue(should_run)


class DidRealWorkTest(unittest.TestCase):
    def test_requires_both_jobs_success(self):
        self.assertTrue(guard.did_real_work(DONE_JOBS))
        self.assertFalse(guard.did_real_work(SKIPPED_JOBS))
        self.assertFalse(
            guard.did_real_work([{"name": "collect-and-report", "conclusion": "success"}])
        )
        self.assertFalse(guard.did_real_work([]))
        self.assertFalse(guard.did_real_work(["broken", {"conclusion": "success"}]))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FetchRunsTest(unittest.TestCase):
    def test_request_filters_this_workflow_since_jst_midnight(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeResponse(json.dumps({"workflow_runs": [run(1, "success", "x")]}).encode())

        runs = guard.fetch_runs_since(
            "shinyo-works/FinPulse-News",
            "token-value",
            guard.jst_day_start_utc(MONDAY_0940_JST),
            opener=opener,
        )
        self.assertEqual(len(runs), 1)
        self.assertIn(
            "/repos/shinyo-works/FinPulse-News/actions/workflows/weekly-news-report.yml/runs?",
            captured["url"],
        )
        self.assertIn("created=%3E%3D2026-10-04T15%3A00%3A00Z", captured["url"])
        self.assertEqual(captured["auth"], "Bearer token-value")
        self.assertEqual(captured["timeout"], guard.REQUEST_TIMEOUT_SECONDS)

    def test_http_error_returns_none(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 500, "error", {}, None)

        with mock.patch("builtins.print"):
            self.assertIsNone(guard.fetch_runs_since("o/r", "t", MONDAY_0940_JST, opener=opener))

    def test_unexpected_payload_returns_none(self):
        def opener(request, timeout):
            return FakeResponse(b'{"message": "Not Found"}')

        with mock.patch("builtins.print"):
            self.assertIsNone(guard.fetch_runs_since("o/r", "t", MONDAY_0940_JST, opener=opener))

    def test_invalid_json_returns_none(self):
        def opener(request, timeout):
            return FakeResponse(b"<html>")

        with mock.patch("builtins.print"):
            self.assertIsNone(guard.fetch_runs_since("o/r", "t", MONDAY_0940_JST, opener=opener))


class FetchJobsTest(unittest.TestCase):
    def test_request_url_and_parsing(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            return FakeResponse(json.dumps({"jobs": DONE_JOBS}).encode())

        jobs = guard.fetch_jobs("shinyo-works/FinPulse-News", "t", 42, opener=opener)
        self.assertEqual(jobs, DONE_JOBS)
        self.assertTrue(
            captured["url"].endswith(
                "/repos/shinyo-works/FinPulse-News/actions/runs/42/jobs?per_page=100"
            )
        )

    def test_bad_id_or_error_returns_none(self):
        def opener(request, timeout):
            raise urllib.error.URLError("down")

        with mock.patch("builtins.print"):
            self.assertIsNone(guard.fetch_jobs("o/r", "t", None, opener=opener))
            self.assertIsNone(guard.fetch_jobs("o/r", "t", "../x", opener=opener))
            self.assertIsNone(guard.fetch_jobs("o/r", "t", 1, opener=opener))


class MainOutputTest(unittest.TestCase):
    def run_main(self, env):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.txt"
            summary = Path(directory) / "summary.md"
            full_env = {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary), **env}
            with mock.patch.dict(os.environ, full_env, clear=True), mock.patch("builtins.print"):
                code = guard.main()
            return code, output.read_text(encoding="utf-8"), summary.read_text(encoding="utf-8")

    def test_check_only_writes_false_without_calling_api(self):
        with mock.patch.object(guard, "fetch_runs_since") as fetch:
            code, output, summary = self.run_main({"INPUT_CHECK_ONLY": "true"})
        fetch.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(output, "should_run=false\n")
        self.assertIn("実行しない", summary)

    def test_missing_token_runs(self):
        code, output, _ = self.run_main({"GITHUB_REPOSITORY": "o/r"})
        self.assertEqual(code, 0)
        self.assertEqual(output, "should_run=true\n")

    def test_success_today_writes_false(self):
        now_runs = [run(1, "success", "2026-10-04T23:05:10Z")]
        with mock.patch.object(guard, "fetch_runs_since", return_value=now_runs), mock.patch.object(
            guard, "fetch_jobs", return_value=DONE_JOBS
        ), mock.patch.object(guard, "datetime", wraps=datetime) as fake_datetime:
            fake_datetime.now.return_value = MONDAY_0940_JST
            _, output, _ = self.run_main(
                {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", "GITHUB_RUN_ID": "2"}
            )
        self.assertEqual(output, "should_run=false\n")

    def test_check_only_success_today_does_not_block(self):
        now_runs = [run(1, "success", "2026-10-04T22:30:00Z")]
        with mock.patch.object(guard, "fetch_runs_since", return_value=now_runs), mock.patch.object(
            guard, "fetch_jobs", return_value=SKIPPED_JOBS
        ), mock.patch.object(guard, "datetime", wraps=datetime) as fake_datetime:
            fake_datetime.now.return_value = MONDAY_0805_JST
            _, output, _ = self.run_main(
                {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", "GITHUB_RUN_ID": "2"}
            )
        self.assertEqual(output, "should_run=true\n")


if __name__ == "__main__":
    unittest.main()
