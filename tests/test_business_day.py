"""月初の回を「月の最初の銀行営業日」だけ動かす判定のテスト（本人依頼 2026-10-08）。"""

import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from scripts import check_weekly_run as guard
from scripts.check_weekly_run import JST

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/weekly-news-report.yml"


def jst(year, month, day, hour=9, minute=20):
    """JST の日時を UTC の aware datetime で返す。"""
    return datetime(year, month, day, hour, minute, tzinfo=JST).astimezone(timezone.utc)


class FirstBusinessDayTest(unittest.TestCase):
    def test_weekday_first_is_itself(self):
        self.assertEqual(guard.first_business_day(2026, 10), date(2026, 10, 1))  # 木曜
        self.assertEqual(guard.first_business_day(2026, 12), date(2026, 12, 1))  # 火曜

    def test_weekend_moves_to_monday(self):
        self.assertEqual(guard.first_business_day(2026, 11), date(2026, 11, 2))  # 1日が日曜
        self.assertEqual(guard.first_business_day(2026, 8), date(2026, 8, 3))  # 1日が土曜

    def test_bunka_no_hi_after_weekend(self):
        # 2025-11-01 土、2 日、3 月（文化の日）→ 4 火
        self.assertEqual(guard.first_business_day(2025, 11), date(2025, 11, 4))

    def test_golden_week(self):
        # 2027-05-01 土、2 日、3〜5 祝日 → 6 木
        self.assertEqual(guard.first_business_day(2027, 5), date(2027, 5, 6))
        self.assertEqual(guard.first_business_day(2026, 5), date(2026, 5, 1))  # 金曜

    def test_new_year_bank_holidays(self):
        self.assertEqual(guard.first_business_day(2027, 1), date(2027, 1, 4))  # 4 が月曜
        self.assertEqual(guard.first_business_day(2026, 1), date(2026, 1, 5))  # 4 が日曜
        self.assertEqual(guard.first_business_day(2025, 1), date(2025, 1, 6))  # 4 が土曜

    def test_substitute_holiday(self):
        # 2020-05-03（日）の振替は 5/6（4・5 が祝日のため）
        self.assertTrue(guard.is_bank_holiday(date(2020, 5, 6)))
        self.assertTrue(guard.is_bank_holiday(date(2019, 11, 4)))  # 11/3 が日曜
        self.assertFalse(guard.is_bank_holiday(date(2026, 11, 4)))

    def test_always_within_first_week(self):
        for year in range(2026, 2051):
            for month in range(1, 13):
                with self.subTest(year=year, month=month):
                    self.assertLessEqual(guard.first_business_day(year, month).day, 7)


class MonthlyDecideTest(unittest.TestCase):
    def decide(self, now, *, monthly=True, force="", check_only=""):
        return guard.decide(
            check_only=check_only,
            force=force,
            runs=[],
            current_run_id="999",
            now=now,
            fetch_jobs=lambda run_id: None,
            monthly=monthly,
        )

    def test_runs_on_first_business_day(self):
        self.assertTrue(self.decide(jst(2026, 12, 1))[0])
        self.assertTrue(self.decide(jst(2026, 11, 2))[0])

    def test_skips_other_days_in_first_week(self):
        should_run, reason = self.decide(jst(2026, 11, 1))
        self.assertFalse(should_run)
        self.assertIn("11/2", reason)
        self.assertFalse(self.decide(jst(2026, 12, 2))[0])

    def test_uses_jst_date_not_utc(self):
        # 2026-12-01 00:30 JST は UTC ではまだ 11/30
        self.assertTrue(self.decide(jst(2026, 12, 1, 0, 30))[0])

    def test_non_monthly_trigger_ignores_calendar(self):
        self.assertTrue(self.decide(jst(2026, 11, 1), monthly=False)[0])

    def test_force_and_check_only_take_precedence(self):
        self.assertTrue(self.decide(jst(2026, 11, 1), force="true")[0])
        self.assertFalse(self.decide(jst(2026, 12, 1), check_only="true")[0])

    def test_trigger_detection(self):
        self.assertTrue(guard.is_monthly_trigger("true", ""))
        self.assertTrue(guard.is_monthly_trigger("", guard.MONTHLY_CRON))
        self.assertFalse(guard.is_monthly_trigger("", ""))
        self.assertFalse(guard.is_monthly_trigger("false", "0 0 * * 1"))

    def test_monthly_cron_matches_workflow(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(f"- cron: \x27{guard.MONTHLY_CRON}\x27", text)
        self.assertIn("INPUT_MONTHLY: ${{ inputs.monthly }}", text)
        self.assertIn("EVENT_SCHEDULE: ${{ github.event.schedule }}", text)


if __name__ == "__main__":
    unittest.main()
