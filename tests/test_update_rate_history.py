import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.update_rate_history import (
    RATE_KEY_MAP,
    REPORT_RATE_CONTRACT_VERSION,
    SCHEMA_VERSION,
    update_history,
    validate_history,
    write_history_atomic,
)
from scripts.backfill_rate_history import normalize_legacy_report, rebuild_history


class UpdateRateHistoryTest(unittest.TestCase):
    def setUp(self):
        self.history = {
            "schema_version": 1,
            "rate_type_order": ["variable"],
            "rate_type_labels": {"variable": "変動"},
            "rows": [],
        }
        self.report = {
            "rate_contract": {
                "version": REPORT_RATE_CONTRACT_VERSION,
                "loan_rate_fields": list(RATE_KEY_MAP),
            },
            "loan_table": [{
                "bank_id": "test-bank",
                "bank_name": "テスト銀行",
                "product_id": "test-product",
                "product_name": "テスト商品",
                "loan_variable": 1.25,
            }]
        }

    def test_manual_products_are_not_stored(self):
        report = copy.deepcopy(self.report)
        report["loan_table"].append({
            "bank_id": "manual-bank",
            "bank_name": "手動入力銀行",
            "product_id": "manual-product",
            "product_name": "手動商品",
            "manual": True,
            "loan_variable": 9.99,
        })
        update_history(self.history, report, "2026-08-01")

        bank_ids = [row["bank_id"] for row in self.history["rows"]]
        self.assertEqual(["test-bank"], bank_ids)

    def test_manual_must_be_boolean_when_present(self):
        for invalid_value in ("false", 0, None):
            report = copy.deepcopy(self.report)
            report["loan_table"][0]["manual"] = invalid_value
            with self.subTest(value=invalid_value), self.assertRaisesRegex(
                ValueError, "manual は真偽値"
            ):
                update_history(copy.deepcopy(self.history), report, "2026-08-01")

        for valid_value in (True, False):
            report = copy.deepcopy(self.report)
            report["loan_table"][0]["manual"] = valid_value
            update_history(copy.deepcopy(self.history), report, "2026-08-01")

    def test_rejects_missing_or_mismatched_rate_contract(self):
        missing = copy.deepcopy(self.report)
        missing.pop("rate_contract")
        wrong_version = copy.deepcopy(self.report)
        wrong_version["rate_contract"]["version"] = 2
        wrong_fields = copy.deepcopy(self.report)
        wrong_fields["rate_contract"]["loan_rate_fields"] = ["loan_variable"]

        for report in (missing, wrong_version, wrong_fields):
            with self.subTest(report=report), self.assertRaisesRegex(
                ValueError, "rate_contract"
            ):
                update_history(copy.deepcopy(self.history), report, "2026-08-01")

    def test_same_day_update_is_idempotent(self):
        update_history(self.history, self.report, "2026-08-01")
        update_history(self.history, self.report, "2026-08-01")

        entries = self.history["rows"][0]["history"]
        self.assertEqual(1, len(entries))
        self.assertEqual(1.25, entries[0]["rate"])

    def test_later_rate_change_is_accumulated(self):
        update_history(self.history, self.report, "2026-08-01")
        changed_report = copy.deepcopy(self.report)
        changed_report["loan_table"][0]["loan_variable"] = 1.35
        update_history(self.history, changed_report, "2026-08-08")
        update_history(self.history, changed_report, "2026-08-15")

        entries = self.history["rows"][0]["history"]
        self.assertEqual(3, len(entries))
        self.assertEqual(
            [
                {"rate": 1.35, "observed_on": "2026-08-15"},
                {"rate": 1.35, "observed_on": "2026-08-08"},
                {"rate": 1.25, "observed_on": "2026-08-01"},
            ],
            entries,
        )
        self.assertEqual("2026-08-15", self.history["generated_at"])
        self.assertEqual(
            ["2026-08-15", "2026-08-08", "2026-08-01"],
            self.history["observation_dates"],
        )

    def test_demo_history_is_replaced_by_real_data(self):
        demo_history = copy.deepcopy(self.history)
        demo_history["is_demo"] = True
        demo_history["generated_at"] = "2026-07-21"
        demo_history["rows"] = [{
            "bank_id": "demo-bank",
            "bank_name": "デモ銀行",
            "product_id": "demo-product",
            "product_name": "デモ商品",
            "rate_type": "variable",
            "history": [{"rate": 0.5, "effective_from": "2026-07-21"}],
        }]

        summary = update_history(demo_history, self.report, "2026-07-01")

        self.assertTrue(summary["replaced_demo"])
        self.assertFalse(demo_history["is_demo"])
        self.assertEqual("2026-07-01", demo_history["generated_at"])
        self.assertEqual(1, len(demo_history["rows"]))
        self.assertEqual("test-bank", demo_history["rows"][0]["bank_id"])

    def test_rejects_older_survey_date(self):
        update_history(self.history, self.report, "2026-08-01")

        with self.assertRaisesRegex(ValueError, "より古いため"):
            update_history(self.history, self.report, "2026-07-01")

    def test_allows_explicit_backfill_and_sorts_dates(self):
        update_history(self.history, self.report, "2026-08-08")
        older_report = copy.deepcopy(self.report)
        older_report["loan_table"][0]["loan_variable"] = 1.15

        update_history(
            self.history,
            older_report,
            "2026-08-01",
            allow_backfill=True,
        )

        self.assertEqual(
            ["2026-08-08", "2026-08-01"],
            self.history["observation_dates"],
        )
        self.assertEqual(
            ["2026-08-08", "2026-08-01"],
            [entry["observed_on"] for entry in self.history["rows"][0]["history"]],
        )

    def test_missing_rate_keeps_the_date_without_copying_previous_value(self):
        update_history(self.history, self.report, "2026-08-01")
        missing_report = copy.deepcopy(self.report)
        missing_report["loan_table"][0]["loan_variable"] = None

        update_history(self.history, missing_report, "2026-08-08")

        self.assertEqual(
            ["2026-08-08", "2026-08-01"],
            self.history["observation_dates"],
        )
        self.assertEqual(
            [{"rate": 1.25, "observed_on": "2026-08-01"}],
            self.history["rows"][0]["history"],
        )

    def test_weekly_update_extends_automated_source_period(self):
        self.history["data_sources"] = [{
            "kind": "financial_report_tool",
            "period_start": "2026-05-04",
            "period_end": "2026-07-27",
        }]

        update_history(self.history, self.report, "2026-08-03")

        self.assertEqual(
            "2026-08-03",
            self.history["data_sources"][0]["period_end"],
        )

    def test_rejects_invalid_ids_and_rates(self):
        invalid_reports = []

        empty_id = copy.deepcopy(self.report)
        empty_id["loan_table"][0]["bank_id"] = ""
        invalid_reports.append((empty_id, "bank_id は空でない文字列"))

        string_rate = copy.deepcopy(self.report)
        string_rate["loan_table"][0]["loan_variable"] = "1.0"
        invalid_reports.append((string_rate, "loan_variable は数値"))

        negative_rate = copy.deepcopy(self.report)
        negative_rate["loan_table"][0]["loan_variable"] = -1.0
        invalid_reports.append((negative_rate, "0〜100"))

        duplicate = copy.deepcopy(self.report)
        duplicate["loan_table"].append(copy.deepcopy(duplicate["loan_table"][0]))
        invalid_reports.append((duplicate, "機関IDと商品IDが重複"))

        for report, expected_error in invalid_reports:
            with self.subTest(error=expected_error), self.assertRaisesRegex(
                ValueError, expected_error
            ):
                update_history(copy.deepcopy(self.history), report, "2026-08-01")

    def test_rejects_invalid_rate_type_metadata(self):
        update_history(self.history, self.report, "2026-08-01")
        invalid_histories = []

        invalid_order_type = copy.deepcopy(self.history)
        invalid_order_type["rate_type_order"] = "variable"
        invalid_histories.append((invalid_order_type, "rate_type_order"))

        duplicate_order = copy.deepcopy(self.history)
        duplicate_order["rate_type_order"] = ["variable", "variable"]
        invalid_histories.append((duplicate_order, "重複"))

        missing_label = copy.deepcopy(self.history)
        missing_label["rate_type_labels"] = {}
        invalid_histories.append((missing_label, "rate_type_labels"))

        unknown_row_type = copy.deepcopy(self.history)
        unknown_row_type["rows"][0]["rate_type"] = "unknown"
        invalid_histories.append((unknown_row_type, "rate_type が不正"))

        for history, expected_error in invalid_histories:
            with self.subTest(error=expected_error), self.assertRaisesRegex(
                ValueError, expected_error
            ):
                validate_history(history)

    def test_atomic_write_produces_valid_json_without_temp_file(self):
        update_history(self.history, self.report, "2026-08-01")

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "rate-history.json"
            write_history_atomic(self.history, target)

            self.assertEqual(
                self.history,
                json.loads(target.read_text(encoding="utf-8")),
            )
            self.assertEqual([], list(target.parent.glob("*.tmp")))

    def test_repository_history_is_real_and_valid(self):
        repository_root = Path(__file__).resolve().parents[1]
        history = json.loads(
            (repository_root / "docs/data/rate-history.json").read_text(
                encoding="utf-8"
            )
        )

        validate_history(history)
        self.assertEqual(SCHEMA_VERSION, history["schema_version"])
        self.assertFalse(history.get("is_demo"))
        self.assertGreater(len(history["rows"]), 0)
        self.assertGreater(len(history["observation_dates"]), 1)

    def test_legacy_report_is_kept_separate_from_current_products(self):
        legacy = normalize_legacy_report({
            "loan_table": [{
                "bank_id": "test-bank",
                "bank_name": "テスト銀行",
                "loan_variable": 1.0,
            }]
        })

        self.assertEqual("test-bank_legacy", legacy["loan_table"][0]["product_id"])
        self.assertEqual("旧集計（商品区分なし）", legacy["loan_table"][0]["product_name"])
        self.assertTrue(legacy["loan_table"][0]["is_legacy"])

    def test_rebuild_history_uses_report_dates(self):
        reports = [
            {
                "survey_date": "2026/08/01",
                "loan_table": [{
                    "bank_id": "test-bank",
                    "bank_name": "テスト銀行",
                    "loan_variable": 1.0,
                }],
            },
            {
                "survey_date": "2026/08/08",
                "loan_table": [{
                    "bank_id": "test-bank",
                    "bank_name": "テスト銀行",
                    "loan_variable": 1.1,
                }],
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, report in enumerate(reports):
                path = Path(directory) / f"report_{index}.json"
                path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
                paths.append(path)

            history = rebuild_history(paths)

        self.assertEqual(["2026-08-08", "2026-08-01"], history["observation_dates"])
        self.assertTrue(history["rows"][0]["is_legacy"])

    def test_rebuild_history_accepts_same_path_twice(self):
        # 日付・パスが同値の入力でも、sorted が第3要素（dict）の比較に到達して
        # TypeError にならないこと（同日再投入は冪等）
        report = {
            "survey_date": "2026/08/01",
            "loan_table": [{
                "bank_id": "test-bank",
                "bank_name": "テスト銀行",
                "loan_variable": 1.0,
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

            history = rebuild_history([path, path])

        self.assertEqual(["2026-08-01"], history["observation_dates"])


if __name__ == "__main__":
    unittest.main()
