import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.update_rate_history import (
    CAR_DATASET,
    HOUSING_DATASET,
    RATE_KEY_MAP,
    REPORT_RATE_CONTRACT_VERSION,
    SCHEMA_VERSION,
    expected_rate_contract_metadata,
    expected_rate_contract_metadata_with_car,
    report_has_dataset,
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

    def test_product_name_override_survives_upstream_names(self):
        report = copy.deepcopy(self.report)
        report["loan_table"] = [{
            "bank_id": "hokkaido_rokin",
            "bank_name": "北海道労働金庫",
            "product_id": "rokin_jutaku",
            "product_name": "住宅ローン",
            "loan_variable": 3.325,
        }]
        update_history(self.history, report, "2026-08-24")
        self.assertEqual("住宅ローン（定額型）", self.history["rows"][0]["product_name"])

        # 翌週も上流が旧名称のままでも、履歴側の表示名は戻らない。
        update_history(self.history, report, "2026-08-31")
        self.assertEqual("住宅ローン（定額型）", self.history["rows"][0]["product_name"])

    def test_product_name_without_override_follows_upstream(self):
        update_history(self.history, self.report, "2026-08-24")
        self.assertEqual("テスト商品", self.history["rows"][0]["product_name"])

    def test_repository_history_uses_the_overridden_product_names(self):
        history = json.loads(
            (Path(__file__).resolve().parents[1] / "docs" / "data" / "rate-history.json")
            .read_text(encoding="utf-8")
        )
        names = {
            (row["bank_id"], row["product_id"]): row["product_name"]
            for row in history["rows"]
        }
        self.assertEqual("住宅ローン（定額型）", names[("hokkaido_rokin", "rokin_jutaku")])
        self.assertEqual("すまいる上手（定率型）", names[("hokkaido_rokin", "rokin_smile")])

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


class CarLoanHistoryTest(unittest.TestCase):
    """マイカーローン金利を住宅ローンと同じ仕組みで積み上げられることを確認する。"""

    def setUp(self):
        self.report = {
            "rate_contract": expected_rate_contract_metadata_with_car(),
            "loan_table": [{
                "bank_id": "test-bank",
                "bank_name": "テスト銀行",
                "product_id": "test-jutaku",
                "product_name": "住宅ローン",
                "loan_variable": 1.25,
            }],
            "car_loan_table": [{
                "bank_id": "test-bank",
                "bank_name": "テスト銀行",
                "product_id": "test-mycar",
                "product_name": "マイカーローン",
                "url": "https://example.com/mycar",
                "car_loan_variable": 1.8,
                "car_loan_fixed": 2.4,
            }],
        }

    def car_history(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "rate_type_order": list(CAR_DATASET.rate_type_order),
            "rate_type_labels": dict(CAR_DATASET.rate_type_labels),
            "observation_dates": [],
            "rows": [],
        }

    def test_car_rates_are_stored_as_variable_and_fixed(self):
        history = self.car_history()
        update_history(history, self.report, "2026-08-24", dataset=CAR_DATASET)

        rows = {row["rate_type"]: row for row in history["rows"]}
        self.assertEqual({"variable", "fixed"}, set(rows))
        self.assertEqual(1.8, rows["variable"]["history"][0]["rate"])
        self.assertEqual(2.4, rows["fixed"]["history"][0]["rate"])
        self.assertEqual("マイカーローン", rows["variable"]["product_name"])
        self.assertEqual("https://example.com/mycar", rows["variable"]["url"])

    def test_car_and_housing_histories_do_not_mix(self):
        car = self.car_history()
        housing = {
            "schema_version": SCHEMA_VERSION,
            "rate_type_order": list(HOUSING_DATASET.rate_type_order),
            "rate_type_labels": dict(HOUSING_DATASET.rate_type_labels),
            "observation_dates": [],
            "rows": [],
        }
        update_history(car, self.report, "2026-08-24", dataset=CAR_DATASET)
        update_history(housing, self.report, "2026-08-24")

        self.assertEqual(["test-mycar"], sorted({row["product_id"] for row in car["rows"]}))
        self.assertEqual(
            ["test-jutaku"], sorted({row["product_id"] for row in housing["rows"]})
        )
        self.assertNotEqual(CAR_DATASET.history_path, HOUSING_DATASET.history_path)

    def test_car_rates_accumulate_by_survey_date(self):
        history = self.car_history()
        update_history(history, self.report, "2026-08-24", dataset=CAR_DATASET)
        changed = copy.deepcopy(self.report)
        changed["car_loan_table"][0]["car_loan_variable"] = 1.7
        update_history(history, changed, "2026-08-31", dataset=CAR_DATASET)

        entries = next(
            row for row in history["rows"] if row["rate_type"] == "variable"
        )["history"]
        self.assertEqual(
            [("2026-08-31", 1.7), ("2026-08-24", 1.8)],
            [(entry["observed_on"], entry["rate"]) for entry in entries],
        )

    def test_guarantee_note_is_carried_and_cleared_with_the_report(self):
        """保証料の注記は上流の値に追従し、消えた週は履歴からも消す。"""
        history = self.car_history()
        with_note = copy.deepcopy(self.report)
        with_note["car_loan_table"][0]["guarantee_note"] = "オリコ保証になった場合は別途 年1.60% が上乗せ"
        update_history(history, with_note, "2026-08-24", dataset=CAR_DATASET)

        self.assertTrue(
            all(row["guarantee_note"].startswith("オリコ保証") for row in history["rows"])
        )

        update_history(history, self.report, "2026-08-31", dataset=CAR_DATASET)
        self.assertTrue(all("guarantee_note" not in row for row in history["rows"]))
        validate_history(history)

    def test_guarantee_note_is_normalized_and_length_checked(self):
        """改行混じり・長すぎる注記でも表示が壊れない形に整える。"""
        history = self.car_history()
        noisy = copy.deepcopy(self.report)
        noisy["car_loan_table"][0]["guarantee_note"] = "  上乗せ\nあり\t（条件）  " + "長" * 200
        update_history(history, noisy, "2026-08-24", dataset=CAR_DATASET)

        note = history["rows"][0]["guarantee_note"]
        self.assertNotIn("\n", note)
        self.assertTrue(note.startswith("上乗せ あり （条件）"))
        self.assertLessEqual(len(note), 80)
        # 整形後の値は履歴の検証も通る（画面へ出す前にここで止める）
        validate_history(history)

    def test_invalid_guarantee_note_is_rejected_by_validation(self):
        history = self.car_history()
        update_history(history, self.report, "2026-08-24", dataset=CAR_DATASET)
        history["rows"][0]["guarantee_note"] = "長" * 81
        with self.assertRaises(ValueError):
            validate_history(history)

    def test_old_contract_is_still_accepted_for_housing(self):
        # 上流が version 1 のままでも住宅ローンの取り込みは止めない（移行期間）。
        legacy = copy.deepcopy(self.report)
        legacy["rate_contract"] = expected_rate_contract_metadata()
        legacy.pop("car_loan_table")
        housing = {
            "schema_version": SCHEMA_VERSION,
            "rate_type_order": list(HOUSING_DATASET.rate_type_order),
            "rate_type_labels": dict(HOUSING_DATASET.rate_type_labels),
            "observation_dates": [],
            "rows": [],
        }
        update_history(housing, legacy, "2026-08-24")

        self.assertEqual(1, len(housing["rows"]))
        self.assertFalse(report_has_dataset(legacy, CAR_DATASET))
        self.assertTrue(report_has_dataset(self.report, CAR_DATASET))

    def test_car_needs_the_contract_to_declare_car_fields(self):
        legacy = copy.deepcopy(self.report)
        legacy["rate_contract"] = expected_rate_contract_metadata()
        with self.assertRaisesRegex(ValueError, "car_loan_rate_fields"):
            update_history(self.car_history(), legacy, "2026-08-24", dataset=CAR_DATASET)

    def test_repository_car_history_is_valid(self):
        history = json.loads(
            (Path(__file__).resolve().parents[1] / "docs/data/car-loan-history.json")
            .read_text(encoding="utf-8")
        )

        validate_history(history)
        self.assertEqual(SCHEMA_VERSION, history["schema_version"])
        self.assertEqual(list(CAR_DATASET.rate_type_order), history["rate_type_order"])
        self.assertFalse(history.get("is_demo"))


if __name__ == "__main__":
    unittest.main()
