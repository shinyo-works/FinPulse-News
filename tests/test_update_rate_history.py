import copy
import dataclasses
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

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
from scripts import update_rate_history
from scripts.backfill_rate_history import normalize_legacy_report, rebuild_history


def next_survey_day():
    """リポジトリの履歴の最新日の 1 週後。本物のデータが進んでも「まだ取り込んでいない日」になる。"""
    latest = max(
        date
        for path in (update_rate_history.HISTORY_PATH, update_rate_history.CAR_HISTORY_PATH)
        for date in json.loads(path.read_text(encoding="utf-8"))["observation_dates"]
    )
    return (
        update_rate_history.datetime.fromisoformat(latest) + update_rate_history.timedelta(days=7)
    ).date()


def freeze_clock(test, day, hour, minute):
    """update_rate_history の「今」を固定する（未来の版・未来の日の拒否が本物の時計に左右されないように）。
    返り値の stop() で外せる。外し忘れてもテストの終わりに外れる。"""
    fixed = update_rate_history.datetime(day.year, day.month, day.day, hour, minute, tzinfo=update_rate_history.JST)
    patcher = mock.patch.object(update_rate_history, "datetime", wraps=update_rate_history.datetime)
    patcher.start().now.return_value = fixed
    test.addCleanup(patcher.stop)
    return patcher


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

    def test_new_product_without_names_stops_the_import(self):
        report = copy.deepcopy(self.report)
        report["loan_table"][0]["bank_name"] = None
        with self.assertRaisesRegex(ValueError, "機関名・商品名"):
            update_history(copy.deepcopy(self.history), report, "2026-08-01")

        report = copy.deepcopy(self.report)
        report["loan_table"][0]["product_name"] = "  "
        with self.assertRaisesRegex(ValueError, "機関名・商品名"):
            update_history(copy.deepcopy(self.history), report, "2026-08-01")

    def test_existing_row_keeps_previous_names_when_upstream_drops_them(self):
        update_history(self.history, self.report, "2026-08-01")
        report = copy.deepcopy(self.report)
        report["loan_table"][0]["bank_name"] = None
        del report["loan_table"][0]["product_name"]
        report["loan_table"][0]["loan_variable"] = 1.3

        update_history(self.history, report, "2026-08-08")

        row = self.history["rows"][0]
        self.assertEqual("テスト銀行", row["bank_name"])
        self.assertEqual("テスト商品", row["product_name"])
        self.assertEqual(1.3, row["history"][0]["rate"])

    def test_only_http_urls_are_stored(self):
        report = copy.deepcopy(self.report)
        report["loan_table"][0]["url"] = "javascript:alert(1)"
        update_history(self.history, report, "2026-08-01")
        self.assertIsNone(self.history["rows"][0]["url"])

        report["loan_table"][0]["url"] = "https://example.com/loan"
        update_history(self.history, report, "2026-08-08")
        self.assertEqual("https://example.com/loan", self.history["rows"][0]["url"])

        report["loan_table"][0]["url"] = "ftp://example.com/loan"
        update_history(self.history, report, "2026-08-15")
        self.assertEqual("https://example.com/loan", self.history["rows"][0]["url"])

    def test_validation_rejects_bad_names_and_urls_in_history(self):
        update_history(self.history, self.report, "2026-08-01")
        for key, value, message in (
            ("bank_name", "", "bank_name"),
            ("product_name", None, "product_name"),
            ("url", "javascript:alert(1)", "url"),
        ):
            broken = copy.deepcopy(self.history)
            broken["rows"][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, message):
                validate_history(broken)

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

    def test_outage_note_marks_all_rate_rows_even_when_no_rate_was_taken(self):
        """機関のサイト障害で金利が1つも取れない週も、注記は商品の全種別の行に付き、戻った週に消える。"""
        history = self.car_history()
        update_history(history, self.report, "2026-08-24", dataset=CAR_DATASET)
        down = copy.deepcopy(self.report)
        loan = down["car_loan_table"][0]
        loan["car_loan_variable"] = None
        loan["car_loan_fixed"] = None
        loan["outage_note"] = "  不正アクセスのため\n取得不可  "
        update_history(history, down, "2026-08-31", dataset=CAR_DATASET)

        self.assertEqual(
            ["不正アクセスのため 取得不可"] * len(history["rows"]),
            [row["outage_note"] for row in history["rows"]],
        )
        validate_history(history)

        update_history(history, self.report, "2026-09-07", dataset=CAR_DATASET)
        self.assertTrue(all("outage_note" not in row for row in history["rows"]))

    def test_outage_note_is_not_rewound_by_an_older_survey(self):
        """過去の調査日を後から入れても、今の注記を付け外ししない。"""
        history = self.car_history()
        down = copy.deepcopy(self.report)
        down["car_loan_table"][0]["outage_note"] = "不正アクセスのため取得不可"
        update_history(history, down, "2026-08-31", dataset=CAR_DATASET)
        update_history(history, self.report, "2026-08-24", dataset=CAR_DATASET, allow_backfill=True)
        self.assertTrue(all(row.get("outage_note") for row in history["rows"]))

    def test_invalid_outage_note_is_rejected_by_validation(self):
        history = self.car_history()
        update_history(history, self.report, "2026-08-24", dataset=CAR_DATASET)
        for bad in ("長" * 81, "", 1):
            broken = copy.deepcopy(history)
            broken["rows"][0]["outage_note"] = bad
            with self.assertRaises(ValueError):
                validate_history(broken)

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

    def test_invalid_car_table_leaves_the_housing_file_untouched(self):
        report = copy.deepcopy(self.report)
        report["car_loan_table"][0]["car_loan_variable"] = -1
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            housing_path = Path(directory) / "rate-history.json"
            car_path = Path(directory) / "car-loan-history.json"
            datasets = {
                "housing": dataclasses.replace(HOUSING_DATASET, history_path=housing_path),
                "car": dataclasses.replace(CAR_DATASET, history_path=car_path),
            }
            with mock.patch.dict(update_rate_history.DATASETS, datasets), mock.patch(
                "sys.argv",
                ["update_rate_history.py", str(report_path), "--date", "2026-08-24"],
            ), redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                update_rate_history.main()

            self.assertFalse(housing_path.exists())
            self.assertFalse(car_path.exists())

            # 正しい入力なら両方とも書かれる（分割後も通常経路が動くこと）
            report_path.write_text(json.dumps(self.report, ensure_ascii=False), encoding="utf-8")
            with mock.patch.dict(update_rate_history.DATASETS, datasets), mock.patch(
                "sys.argv",
                ["update_rate_history.py", str(report_path), "--date", "2026-08-24"],
            ), redirect_stdout(io.StringIO()):
                update_rate_history.main()
            self.assertTrue(housing_path.exists())
            self.assertTrue(car_path.exists())

    def test_repository_car_history_is_valid(self):
        history = json.loads(
            (Path(__file__).resolve().parents[1] / "docs/data/car-loan-history.json")
            .read_text(encoding="utf-8")
        )

        validate_history(history)
        self.assertEqual(SCHEMA_VERSION, history["schema_version"])
        self.assertEqual(list(CAR_DATASET.rate_type_order), history["rate_type_order"])
        self.assertFalse(history.get("is_demo"))



class SecurityAuditFixTest(unittest.TestCase):
    """2026-10-07 監査 W5: 未来の調査日を拒否し、自動取り込みは新しい週だけにする。"""

    def test_future_date_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未来の日付"):
            update_rate_history.reject_future_date("2026-10-13", today="2026-10-12")
        update_rate_history.reject_future_date("2026-10-12", today="2026-10-12")

    def test_update_history_rejects_future_survey_date(self):
        tomorrow = (
            update_rate_history.datetime.now(update_rate_history.JST)
            + update_rate_history.timedelta(days=1)
        ).date().isoformat()
        history = json.loads(update_rate_history.HISTORY_PATH.read_text(encoding="utf-8"))
        report = {"rate_contract": update_rate_history.expected_rate_contract_metadata(), "loan_table": []}
        with self.assertRaisesRegex(ValueError, "未来の日付"):
            update_rate_history.update_history(history, report, tomorrow)

    def _run_main(self, argv, tmp, stale=None, keep=False):
        """keep=True なら、tmp に前回の呼び出しで書いた履歴をそのまま使う（初回はリポジトリの写し）。"""
        housing = Path(tmp) / "rate-history.json"
        car = Path(tmp) / "car-loan-history.json"
        if not (keep and housing.exists()):
            housing.write_text(update_rate_history.HISTORY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        if not (keep and car.exists()):
            car.write_text(update_rate_history.CAR_HISTORY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        datasets = {
            key: dataclasses.replace(ds, history_path=housing if key == "housing" else car)
            for key, ds in update_rate_history.DATASETS.items()
        }
        out = io.StringIO()
        with mock.patch.object(update_rate_history, "DATASETS", datasets), \
                mock.patch.object(update_rate_history, "stale_feed_message", return_value=stale), \
                mock.patch("sys.argv", ["update_rate_history.py", *argv]), redirect_stdout(out):
            update_rate_history.main()
        return housing, car, out.getvalue()

    def test_only_newer_skips_already_imported_week_without_writing(self):
        latest = json.loads(update_rate_history.HISTORY_PATH.read_text(encoding="utf-8"))["observation_dates"][0]
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "feed.json"
            report.write_text(json.dumps({
                "rate_contract": update_rate_history.expected_rate_contract_metadata_with_car(),
                "survey_date": latest.replace("-", "/"),
                "loan_table": [{"bank_id": "x", "product_id": "y", "loan_variable": 9.9}],
                "car_loan_table": [{"bank_id": "x", "product_id": "z", "car_loan_variable": 9.9}],
            }), encoding="utf-8")
            housing, car, log = self._run_main([str(report), "--only-newer"], tmp)
            self.assertEqual(
                housing.read_text(encoding="utf-8"),
                update_rate_history.HISTORY_PATH.read_text(encoding="utf-8"),
            )
            self.assertIn("取り込み済み", log)

    def test_main_rejects_future_date_even_with_only_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "feed.json"
            report.write_text(json.dumps({
                "rate_contract": update_rate_history.expected_rate_contract_metadata_with_car(),
                "survey_date": "2099/01/05",
                "loan_table": [{"bank_id": "x", "product_id": "y", "loan_variable": 1.0}],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "未来の日付"):
                self._run_main([str(report), "--only-newer"], tmp)

    def test_empty_car_table_imports_housing_only_and_exits_partial(self):
        # 約束にマイカーがあるのに表が空の回: 住宅だけ取り込み、マイカーは前回のまま、終了コード 2（赤）
        day = next_survey_day()
        today = day.isoformat()
        freeze_clock(self, day, 7, 15)
        housing_before = json.loads(update_rate_history.HISTORY_PATH.read_text(encoding="utf-8"))
        bank = housing_before["rows"][0]
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "feed.json"
            report.write_text(json.dumps({
                "rate_contract": update_rate_history.expected_rate_contract_metadata_with_car(),
                "survey_date": today,
                "loan_table": [{
                    "bank_id": bank["bank_id"],
                    "bank_name": bank["bank_name"],
                    "product_id": bank["product_id"],
                    "product_name": bank["product_name"],
                    "loan_variable": 1.0,
                }],
                "car_loan_table": [],
            }), encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                self._run_main([str(report)], tmp)
            self.assertEqual(update_rate_history.EXIT_PARTIAL, raised.exception.code)
            housing = json.loads((Path(tmp) / "rate-history.json").read_text(encoding="utf-8"))
            self.assertEqual(today, housing["observation_dates"][0])
            self.assertEqual(
                (Path(tmp) / "car-loan-history.json").read_text(encoding="utf-8"),
                update_rate_history.CAR_HISTORY_PATH.read_text(encoding="utf-8"),
            )

    def test_report_has_dataset_follows_the_contract_not_the_table(self):
        report = {
            "rate_contract": update_rate_history.expected_rate_contract_metadata_with_car(),
            "loan_table": [{"bank_id": "x"}],
            "car_loan_table": [],
        }
        self.assertTrue(update_rate_history.report_has_dataset(report, update_rate_history.CAR_DATASET))
        self.assertTrue(update_rate_history.report_table_is_empty(report, update_rate_history.CAR_DATASET))


def _history(latest="2026-11-02", **extra):
    history = {
        "schema_version": SCHEMA_VERSION,
        "rate_type_order": list(HOUSING_DATASET.rate_type_order),
        "rate_type_labels": dict(HOUSING_DATASET.rate_type_labels),
        "observation_dates": [latest] if latest else [],
        "rows": [],
    }
    if latest:
        history["generated_at"] = latest
    history.update(extra)
    return history


class FeedVersionImportTest(unittest.TestCase):
    """再レビュー G-2（2026-10-13）: 同じ調査日でも新しい版なら取り込み直す。手直しの日は manual_lock で守る。"""

    def decide(self, history, survey_date, version):
        return update_rate_history.import_decision(history, survey_date, version)[0]

    def test_newer_survey_date_imports(self):
        self.assertTrue(self.decide(_history(), "2026-11-09", None))

    def test_empty_history_imports(self):
        self.assertTrue(self.decide(_history(latest=None), "2026-11-02", None))

    def test_older_survey_date_does_nothing(self):
        self.assertFalse(self.decide(_history(), "2026-10-26", "2026-10-26T07:00:00+09:00"))

    def test_same_day_newer_version_imports(self):
        history = _history(last_feed_generated_at="2026-11-02T07:10:00+09:00")
        self.assertTrue(self.decide(history, "2026-11-02", "2026-11-02T09:10:00+09:00"))

    def test_same_day_same_or_older_version_does_nothing(self):
        history = _history(last_feed_generated_at="2026-11-02T09:10:00+09:00")
        self.assertFalse(self.decide(history, "2026-11-02", "2026-11-02T09:10:00+09:00"))
        self.assertFalse(self.decide(history, "2026-11-02", "2026-11-02T07:10:00+09:00"))

    def test_manual_lock_protects_the_day(self):
        history = _history(
            last_feed_generated_at="2026-11-02T07:10:00+09:00",
            manual_lock={"2026-11-02": "北洋の固定10年を公式サイトの値に手直し"},
        )
        should_import, reason = update_rate_history.import_decision(
            history, "2026-11-02", "2026-11-02T09:10:00+09:00"
        )
        self.assertFalse(should_import)
        self.assertIn("manual_lock", reason)

    def test_history_without_version_is_not_overwritten_on_same_day(self):
        # 版の記録を始める前に手で直した値を守る
        self.assertFalse(self.decide(_history(), "2026-11-02", "2026-11-02T09:10:00+09:00"))

    def test_versionless_feed_does_not_overwrite_same_day(self):
        history = _history(last_feed_generated_at="2026-11-02T07:10:00+09:00")
        self.assertFalse(self.decide(history, "2026-11-02", None))

    def test_versions_are_compared_as_instants(self):
        history = _history(last_feed_generated_at="2026-11-02T09:00:00+09:00")
        self.assertTrue(self.decide(history, "2026-11-02", "2026-11-02T00:30:00+00:00"))  # 09:30 JST

    def test_validate_history_checks_new_keys(self):
        validate_history(_history(
            last_feed_generated_at="2026-11-02T07:10:00+09:00",
            manual_lock={"2026-11-02": "理由"},
        ))
        for bad in (
            {"last_feed_generated_at": "2026-11-02T07:10:00"},  # 時差なし
            {"last_feed_generated_at": "きのう"},
            {"manual_lock": ["2026-11-02"]},
            {"manual_lock": {"2026/11/02": "理由"}},
            {"manual_lock": {"2026-11-02": ""}},
            {"manual_lock": {"2026-11-02": "あ" * 201}},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_history(_history(**bad))

    def test_report_with_broken_version_is_rejected(self):
        report = {
            "rate_contract": update_rate_history.expected_rate_contract_metadata(),
            "feed_generated_at": "2026-11-02 07:10",
            "loan_table": [{"bank_id": "x", "product_id": "y"}],
        }
        with self.assertRaisesRegex(ValueError, "feed_generated_at"):
            update_rate_history.validate_report_data(report)
        report["feed_generated_at"] = "2099-01-01T00:00:00+09:00"
        with self.assertRaisesRegex(ValueError, "未来"):
            update_rate_history.validate_report_data(report)

    def test_import_records_the_version_and_same_day_rerun_updates_value(self):
        day = next_survey_day()
        today = day.isoformat()
        source = json.loads(update_rate_history.HISTORY_PATH.read_text(encoding="utf-8"))["rows"][0]

        def feed(rate, version):
            return {
                "rate_contract": update_rate_history.expected_rate_contract_metadata_with_car(),
                "survey_date": today,
                "feed_generated_at": version,
                "loan_table": [{
                    "bank_id": source["bank_id"], "bank_name": source["bank_name"],
                    "product_id": source["product_id"], "product_name": source["product_name"],
                    "loan_variable": rate,
                }],
                "car_loan_table": [],
            }

        helper = SecurityAuditFixTest()
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "feed.json"
            first = f"{today}T07:10:00+09:00"
            report.write_text(json.dumps(feed(1.0, first)), encoding="utf-8")
            clock = freeze_clock(self, day, 7, 15)  # 07:10 版の直後
            with self.assertRaises(SystemExit):  # マイカーが空なので最後は赤（#6）
                helper._run_main([str(report)], tmp)
            clock.stop()
            housing = json.loads((Path(tmp) / "rate-history.json").read_text(encoding="utf-8"))
            self.assertEqual(first, housing["last_feed_generated_at"])

            # 同じ日の新しい版（月初の 09:00 取り直し）は --only-newer でも取り込み直す
            second = f"{today}T09:10:00+09:00"
            report.write_text(json.dumps(feed(1.5, second)), encoding="utf-8")
            clock = freeze_clock(self, day, 9, 15)  # 09:10 版の直後
            with self.assertRaises(SystemExit):
                helper._run_main([str(report), "--only-newer"], tmp, keep=True)
            clock.stop()
            housing = json.loads((Path(tmp) / "rate-history.json").read_text(encoding="utf-8"))
            self.assertEqual(second, housing["last_feed_generated_at"])
            row = next(
                r for r in housing["rows"]
                if r["bank_id"] == source["bank_id"] and r["product_id"] == source["product_id"]
                and r["rate_type"] == "variable"
            )
            self.assertEqual({"rate": 1.5, "observed_on": today}, row["history"][0])

            # 手直しの印を付けた日は、さらに新しい版が来ても上書きしない
            housing["manual_lock"] = {today: "手直し"}
            (Path(tmp) / "rate-history.json").write_text(json.dumps(housing, ensure_ascii=False), encoding="utf-8")
            before = (Path(tmp) / "rate-history.json").read_text(encoding="utf-8")
            report.write_text(json.dumps(feed(2.0, f"{today}T10:10:00+09:00")), encoding="utf-8")
            clock = freeze_clock(self, day, 10, 15)  # 10:10 版の直後
            with self.assertRaises(SystemExit):
                helper._run_main([str(report), "--only-newer"], tmp, keep=True)
            clock.stop()
            self.assertEqual(before, (Path(tmp) / "rate-history.json").read_text(encoding="utf-8"))


JST_TEST = update_rate_history.JST


class StaleFeedTest(unittest.TestCase):
    """再レビュー G-3（2026-10-13）: 公開用データが古いまま止まったら、控えの時刻以降は赤にする。"""

    def at(self, *args):
        return update_rate_history.datetime(*args, tzinfo=JST_TEST)

    def test_expected_date_is_latest_monday_or_first_business_day(self):
        expect = update_rate_history.expected_latest_survey_date
        self.assertEqual("2026-10-12", expect(self.at(2026, 10, 13, 9)).isoformat())  # 火曜 → 前日の月曜
        self.assertEqual("2026-10-12", expect(self.at(2026, 10, 12, 8)).isoformat())  # 月曜当日
        self.assertEqual("2026-12-01", expect(self.at(2026, 12, 3, 12)).isoformat())  # 月初の火曜が後
        self.assertEqual("2026-11-30", expect(self.at(2026, 11, 30, 12)).isoformat())
        # 2027-01-04（月）は年始休みの明けで最初の営業日でもある
        self.assertEqual("2027-01-04", expect(self.at(2027, 1, 4, 9)).isoformat())
        # 2027-01-01〜03 は休みなので、まだ前の週の月曜（12/28）
        self.assertEqual("2026-12-28", expect(self.at(2027, 1, 2, 12)).isoformat())

    def test_fresh_feed_is_quiet(self):
        self.assertIsNone(update_rate_history.stale_feed_message("2026-10-12", self.at(2026, 10, 12, 12)))

    def test_stale_before_eleven_is_a_warning(self):
        level, text = update_rate_history.stale_feed_message("2026-10-05", self.at(2026, 10, 12, 8, 5))
        self.assertEqual("warning", level)
        self.assertIn("2026-10-12", text)

    def test_stale_after_eleven_is_an_error(self):
        level, _ = update_rate_history.stale_feed_message("2026-10-05", self.at(2026, 10, 12, 11, 0))
        self.assertEqual("error", level)
        level, _ = update_rate_history.stale_feed_message("2026-10-05", self.at(2026, 10, 13, 8, 0))
        self.assertEqual("error", level)

    def test_stale_error_turns_main_red_but_warning_does_not(self):
        latest = json.loads(update_rate_history.HISTORY_PATH.read_text(encoding="utf-8"))["observation_dates"][0]
        helper = SecurityAuditFixTest()
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "feed.json"
            report.write_text(json.dumps({
                "rate_contract": update_rate_history.expected_rate_contract_metadata(),
                "survey_date": latest,
                "loan_table": [{"bank_id": "x", "product_id": "y", "loan_variable": 1.0}],
            }), encoding="utf-8")
            _, _, log = helper._run_main([str(report), "--only-newer"], tmp, stale=("warning", "古い"))
            self.assertIn("::warning title=公開用の金利データが古いままです::古い", log)
            with self.assertRaises(SystemExit) as raised:
                helper._run_main([str(report), "--only-newer"], tmp, stale=("error", "古い"))
            self.assertEqual(update_rate_history.EXIT_PARTIAL, raised.exception.code)


class ImportGuardTest(unittest.TestCase):
    """再レビュー #5（2026-10-13）: 受け手の検証を締める。範囲外・急な変化はその項目だけ保留し、
    名前とリンク先のホストの変更は前回のままにして警告する。"""

    def history(self, rate=2.0, url="https://www.example-bank.jp/loan/"):
        return {
            "schema_version": SCHEMA_VERSION,
            "rate_type_order": list(HOUSING_DATASET.rate_type_order),
            "rate_type_labels": dict(HOUSING_DATASET.rate_type_labels),
            "observation_dates": ["2026-10-05"],
            "generated_at": "2026-10-05",
            "rows": [{
                "bank_id": "bank",
                "bank_name": "確認銀行",
                "product_id": "loan",
                "product_name": "住宅ローン",
                "url": url,
                "rate_type": "variable",
                "history": [{"rate": rate, "observed_on": "2026-10-05"}],
            }],
        }

    def report(self, **loan):
        row = {
            "bank_id": "bank",
            "bank_name": "確認銀行",
            "product_id": "loan",
            "product_name": "住宅ローン",
            "url": "https://www.example-bank.jp/loan/",
            "loan_variable": 2.25,
        }
        row.update(loan)
        return {"rate_contract": expected_rate_contract_metadata(), "loan_table": [row]}

    def latest(self, history):
        return history["rows"][0]["history"][0]

    def test_normal_revision_passes(self):
        # 北洋の +0.25% のような正当な改定は通る
        history = self.history()
        summary = update_history(history, self.report(), "2026-10-12")
        self.assertEqual({"rate": 2.25, "observed_on": "2026-10-12"}, self.latest(history))
        self.assertEqual([], summary["held"])
        self.assertEqual([], summary["warnings"])

    def test_out_of_range_values_are_held(self):
        for value in (0.05, 15.5, 0.0):
            with self.subTest(value=value):
                history = self.history()
                summary = update_history(history, self.report(loan_variable=value), "2026-10-12")
                self.assertEqual("2026-10-05", self.latest(history)["observed_on"])
                self.assertEqual(1, len(summary["held"]))
                self.assertIn("範囲外", summary["held"][0])

    def test_range_bounds_are_inclusive(self):
        for value in (0.1, 15.0):
            with self.subTest(value=value):
                history = self.history(rate=value)
                summary = update_history(history, self.report(loan_variable=value), "2026-10-12")
                self.assertEqual([], summary["held"])

    def test_big_jump_or_ratio_is_held(self):
        for previous, value in ((2.0, 5.1), (1.0, 10.0), (3.0, 0.3)):
            with self.subTest(previous=previous, value=value):
                history = self.history(rate=previous)
                summary = update_history(history, self.report(loan_variable=value), "2026-10-12")
                self.assertEqual(previous, self.latest(history)["rate"])
                self.assertEqual(1, len(summary["held"]))

    def test_jump_of_exactly_three_points_passes(self):
        history = self.history(rate=2.0)
        summary = update_history(history, self.report(loan_variable=5.0), "2026-10-12")
        self.assertEqual([], summary["held"])

    def test_same_day_reimport_compares_with_the_previous_day(self):
        # 同じ日の取り直しでも、比べる相手は前の調査日の値
        history = self.history(rate=2.0)
        update_history(history, self.report(loan_variable=2.25), "2026-10-12")
        summary = update_history(history, self.report(loan_variable=5.3), "2026-10-12")
        self.assertEqual(2.25, self.latest(history)["rate"])
        self.assertEqual(1, len(summary["held"]))

    def test_new_row_out_of_range_is_held(self):
        history = self.history()
        report = self.report(product_id="new", product_name="新商品", loan_variable=20.0)
        summary = update_history(history, report, "2026-10-12")
        self.assertEqual(1, len(history["rows"]))
        self.assertEqual(1, len(summary["held"]))

    def test_host_change_keeps_old_url(self):
        history = self.history()
        summary = update_history(
            history, self.report(url="https://evil.example.com/loan/"), "2026-10-12"
        )
        self.assertEqual("https://www.example-bank.jp/loan/", history["rows"][0]["url"])
        self.assertEqual(1, len(summary["warnings"]))
        self.assertIn("evil.example.com", summary["warnings"][0])

    def test_same_host_url_is_updated(self):
        history = self.history()
        summary = update_history(
            history, self.report(url="https://WWW.example-bank.jp/loan/new/"), "2026-10-12"
        )
        self.assertEqual("https://WWW.example-bank.jp/loan/new/", history["rows"][0]["url"])
        self.assertEqual([], summary["warnings"])

    def test_missing_url_row_takes_the_new_url(self):
        history = self.history(url=None)
        update_history(history, self.report(), "2026-10-12")
        self.assertEqual("https://www.example-bank.jp/loan/", history["rows"][0]["url"])

    def test_name_change_keeps_old_name(self):
        history = self.history()
        summary = update_history(
            history, self.report(bank_name="別の銀行", product_name="別の商品"), "2026-10-12"
        )
        row = history["rows"][0]
        self.assertEqual(("確認銀行", "住宅ローン"), (row["bank_name"], row["product_name"]))
        self.assertEqual(2, len(summary["warnings"]))
        # 金利そのものは取り込む
        self.assertEqual(2.25, self.latest(history)["rate"])

    def test_exact_three_point_jump_with_float_error_passes(self):
        history = self.history(rate=2.2)
        summary = update_history(history, self.report(loan_variable=5.2), "2026-10-12")
        self.assertEqual([], summary["held"])

    def test_names_in_warnings_cannot_inject_workflow_commands(self):
        history = self.history()
        summary = update_history(
            history, self.report(bank_name="別名\n::error::偽物"), "2026-10-12"
        )
        self.assertEqual(1, len(summary["warnings"]))
        self.assertNotIn("\n", summary["warnings"][0])
        self.assertIn("::error::偽物", summary["warnings"][0])  # 1 行の途中なのでコマンドにならない

    def test_all_held_does_not_advance_the_survey_date(self):
        history = self.history()
        report = self.report(loan_variable=42.0)
        report["feed_generated_at"] = "2026-10-12T07:00:00+09:00"
        summary = update_history(history, report, "2026-10-12")
        self.assertEqual(["2026-10-05"], history["observation_dates"])
        self.assertEqual("2026-10-05", history["generated_at"])
        self.assertNotIn("last_feed_generated_at", history)
        self.assertEqual(1, len(summary["held"]))
        validate_history(history)

    def test_partial_hold_still_records_the_version(self):
        # 再レビュー G23（本人決定 2026-10-19）: 保留があっても版を記録する。記録しないと同じ日の
        # 新しい版が「版の記録が無い日は守る」で断られ、報告側の検算も「公開中の版 なし」になる
        history = self.history()
        history["rows"].append({
            **history["rows"][0], "rate_type": "fixed_10y",
            "history": [{"rate": 3.0, "observed_on": "2026-10-05"}],
        })
        report = self.report(loan_fixed_10y=9.5)  # 3.0 → 9.5 は急な変化で保留
        report["feed_generated_at"] = "2026-10-12T07:00:00+09:00"
        summary = update_history(history, report, "2026-10-12")
        self.assertEqual("2026-10-12", history["generated_at"])  # 変動は取り込めた
        self.assertEqual("2026-10-12T07:00:00+09:00", history["last_feed_generated_at"])
        self.assertEqual(1, len(summary["held"]))
        # 直った新しい版は、版の比較で同じ日に取り込み直せる
        should, _ = update_rate_history.import_decision(history, "2026-10-12", "2026-10-12T09:00:00+09:00")
        self.assertTrue(should)

    def test_max_rate_constant_for_whole_history_is_unchanged(self):
        # 履歴全体の検証（validate_history）と共用なので、取り込み用の範囲とは別に保つ
        self.assertEqual(100.0, update_rate_history.MAX_RATE_PERCENT)

    def test_main_prints_warnings_for_holds(self):
        day = next_survey_day()
        today = day.isoformat()
        freeze_clock(self, day, 7, 15)
        source = json.loads(update_rate_history.HISTORY_PATH.read_text(encoding="utf-8"))["rows"][0]
        helper = SecurityAuditFixTest()
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "feed.json"
            report.write_text(json.dumps({
                "rate_contract": update_rate_history.expected_rate_contract_metadata(),
                "survey_date": today,
                "loan_table": [{
                    "bank_id": source["bank_id"], "bank_name": source["bank_name"],
                    "product_id": source["product_id"], "product_name": source["product_name"],
                    "loan_variable": 42.0,
                }],
            }), encoding="utf-8")
            _, _, log = helper._run_main([str(report)], tmp)
        self.assertIn("::warning title=住宅ローン金利で保留した金利があります::", log)


class SameDayReplaceTest(unittest.TestCase):
    """再レビュー G23（本人決定 2026-10-19）: 同じ日の新しい版の取り込み直しは、その日の列の置き換え。
    確かめられなかった項目の当日の値は消す。取れた割合が 7 割未満の種別は前の版のまま残す。"""

    DAY = "2026-11-02"
    V7 = "2026-11-02T07:10:00+09:00"
    V9 = "2026-11-02T09:10:00+09:00"

    def history(self, products=1):
        rows = [{
            "bank_id": "bank", "bank_name": "確認銀行",
            "product_id": f"loan{n}", "product_name": f"住宅ローン{n}",
            "url": "https://www.example-bank.jp/loan/", "rate_type": "variable",
            "history": [{"rate": 2.0, "observed_on": "2026-10-26"}],
        } for n in range(products)]
        return {
            "schema_version": SCHEMA_VERSION,
            "rate_type_order": list(HOUSING_DATASET.rate_type_order),
            "rate_type_labels": dict(HOUSING_DATASET.rate_type_labels),
            "observation_dates": ["2026-10-26"],
            "generated_at": "2026-10-26",
            "rows": rows,
        }

    def report(self, version, loans):
        return {
            "rate_contract": expected_rate_contract_metadata(),
            "feed_generated_at": version,
            "loan_table": [
                {"bank_id": "bank", "bank_name": "確認銀行", "product_id": pid,
                 "product_name": f"住宅ローン{pid[4:]}", "url": "https://www.example-bank.jp/loan/",
                 "loan_variable": rate}
                for pid, rate in loans.items()
            ],
        }

    def all_at(self, rate, count):
        return {f"loan{n}": rate for n in range(count)}

    def entries(self, history, product_id):
        row = next((r for r in history["rows"] if r["product_id"] == product_id), None)
        return None if row is None else row["history"]

    def setUp(self):
        freeze_clock(self, update_rate_history.datetime(2026, 11, 2).date(), 9, 15)

    def test_held_first_version_is_recorded_and_newer_version_reimports(self):
        # (i) 07:00 版で 1 項目保留 → 版が記録される → 09:00 版で取り込み直される
        history = self.history(products=2)
        update_history(history, self.report(self.V7, {"loan0": 2.1, "loan1": 9.9}), self.DAY)
        self.assertEqual(self.V7, history["last_feed_generated_at"])
        self.assertTrue(update_rate_history.import_decision(history, self.DAY, self.V9)[0])
        update_history(history, self.report(self.V9, {"loan0": 2.1, "loan1": 2.2}), self.DAY)
        self.assertEqual({"rate": 2.2, "observed_on": self.DAY}, self.entries(history, "loan1")[0])
        self.assertEqual(self.V9, history["last_feed_generated_at"])

    def test_blank_in_newer_version_clears_that_days_value_only(self):
        # (ii) 07:00 は値あり、09:00 版では空欄 → 当日の値が消え、前週の値は残る
        history = self.history(products=4)
        update_history(history, self.report(self.V7, self.all_at(2.1, 4)), self.DAY)
        summary = update_history(
            history, self.report(self.V9, {**self.all_at(2.1, 3), "loan3": None}), self.DAY
        )
        self.assertEqual([{"rate": 2.0, "observed_on": "2026-10-26"}], self.entries(history, "loan3"))
        self.assertEqual(1, summary["cleared"])
        self.assertEqual(self.DAY, history["generated_at"])
        validate_history(history)

    def test_product_missing_from_newer_version_is_cleared(self):
        # (iii) 09:00 版に載っていない商品の当日値が消える（その日だけの行は行ごと消える）
        history = self.history(products=4)
        update_history(history, self.report(self.V7, {**self.all_at(2.1, 4), "loan9": 1.5}), self.DAY)
        self.assertIsNotNone(self.entries(history, "loan9"))
        update_history(history, self.report(self.V9, self.all_at(2.1, 4)), self.DAY)
        self.assertIsNone(self.entries(history, "loan9"))
        validate_history(history)

    def test_partial_hold_on_reimport_clears_only_held_items(self):
        # (iv) 同じ日の取り直しで一部だけ保留 → 保留項目の当日値だけ消え、ほかは新しい版の値
        history = self.history(products=4)
        update_history(history, self.report(self.V7, self.all_at(2.1, 4)), self.DAY)
        summary = update_history(
            history, self.report(self.V9, {**self.all_at(2.3, 3), "loan3": 42.0}), self.DAY
        )
        self.assertEqual(1, len(summary["held"]))
        self.assertEqual(2.3, self.entries(history, "loan0")[0]["rate"])
        self.assertEqual("2026-10-26", self.entries(history, "loan3")[0]["observed_on"])
        self.assertEqual(self.V9, history["last_feed_generated_at"])

    def test_low_confirmed_ratio_keeps_the_previous_version(self):
        # (iv-4) 07:00 に値があった項目の 6 割しか取れない → 置き換えず前の版のまま
        #        7 割・8 割取れた → 置き換え、取れなかった項目の当日値は消える
        self.assertEqual(0.7, update_rate_history.SAME_DAY_MIN_CONFIRMED_RATIO)
        for confirmed, replaced in ((6, False), (7, True), (8, True)):
            with self.subTest(confirmed=confirmed):
                history = self.history(products=10)
                update_history(history, self.report(self.V7, self.all_at(2.1, 10)), self.DAY)
                before = copy.deepcopy(history)
                loans = {f"loan{n}": (2.4 if n < confirmed else None) for n in range(10)}
                summary = update_history(history, self.report(self.V9, loans), self.DAY)
                if replaced:
                    self.assertNotIn("kept_previous", summary)
                    self.assertEqual(2.4, self.entries(history, "loan0")[0]["rate"])
                    self.assertEqual("2026-10-26", self.entries(history, "loan9")[0]["observed_on"])
                    self.assertEqual(self.V9, history["last_feed_generated_at"])
                else:
                    self.assertIn("kept_previous", summary)
                    self.assertEqual(before, history)

    def test_all_held_on_reimport_keeps_the_previous_version(self):
        # 全項目が保留・空欄なら前の版のまま。版も前のままなので、さらに新しい版なら取り込み直せる
        history = self.history(products=2)
        update_history(history, self.report(self.V7, self.all_at(2.1, 2)), self.DAY)
        before = copy.deepcopy(history)
        summary = update_history(history, self.report(self.V9, {"loan0": 42.0, "loan1": None}), self.DAY)
        self.assertIn("kept_previous", summary)
        self.assertEqual(before, history)
        self.assertEqual(self.V7, history["last_feed_generated_at"])

    def test_manual_lock_and_unversioned_history_are_still_protected(self):
        # (v)(vi) 手直しの印の日と、版の記録が無い旧形式の履歴は、同じ日の新しい版を断る（緩めない）
        history = self.history()
        update_history(history, self.report(self.V7, {"loan0": 2.1}), self.DAY)
        locked = copy.deepcopy(history)
        locked["manual_lock"] = {self.DAY: "手直し"}
        self.assertFalse(update_rate_history.import_decision(locked, self.DAY, self.V9)[0])
        unversioned = copy.deepcopy(history)
        unversioned.pop("last_feed_generated_at")
        self.assertFalse(update_rate_history.import_decision(unversioned, self.DAY, self.V9)[0])


class SameDayReplaceMainTest(unittest.TestCase):
    """G23 の終了コード: (iv-2) 片方の種別だけ前の版のまま → 取り込めた種別は書いて 2。
    (iv-3) どの種別も取り込めない → 書き込まずに 3（workflow は 2 以外ですぐ赤・公開しない）。"""

    def feed(self, day, version, housing_rate, car_rate):
        housing = json.loads(update_rate_history.HISTORY_PATH.read_text(encoding="utf-8"))["rows"][0]
        car = json.loads(update_rate_history.CAR_HISTORY_PATH.read_text(encoding="utf-8"))["rows"][0]
        housing_key = next(k for k, v in update_rate_history.RATE_KEY_MAP.items() if v == housing["rate_type"])
        car_key = next(k for k, v in update_rate_history.CAR_RATE_KEY_MAP.items() if v == car["rate_type"])
        return {
            "rate_contract": update_rate_history.expected_rate_contract_metadata_with_car(),
            "survey_date": day,
            "feed_generated_at": version,
            "loan_table": [{
                "bank_id": housing["bank_id"], "bank_name": housing["bank_name"],
                "product_id": housing["product_id"], "product_name": housing["product_name"],
                housing_key: housing_rate,
            }],
            "car_loan_table": [{
                "bank_id": car["bank_id"], "bank_name": car["bank_name"],
                "product_id": car["product_id"], "product_name": car["product_name"],
                car_key: car_rate,
            }],
        }

    def run_pair(self, housing9, car9):
        day = next_survey_day()
        helper = SecurityAuditFixTest()
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "feed.json"
            clock = freeze_clock(self, day, 7, 15)
            report.write_text(json.dumps(self.feed(day.isoformat(), f"{day}T07:10:00+09:00", 2.0, 3.0)), encoding="utf-8")
            helper._run_main([str(report)], tmp)
            clock.stop()
            housing7 = (Path(tmp) / "rate-history.json").read_text(encoding="utf-8")
            car7 = (Path(tmp) / "car-loan-history.json").read_text(encoding="utf-8")
            freeze_clock(self, day, 9, 15)
            report.write_text(json.dumps(self.feed(day.isoformat(), f"{day}T09:10:00+09:00", housing9, car9)), encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                helper._run_main([str(report), "--only-newer"], tmp, keep=True)
            housing = (Path(tmp) / "rate-history.json").read_text(encoding="utf-8")
            car = (Path(tmp) / "car-loan-history.json").read_text(encoding="utf-8")
        return raised.exception.code, housing7 == housing, car7 == car

    def test_housing_kept_car_updated_exits_partial(self):
        # (iv-2) 住宅が全項目空欄・マイカー正常 → 住宅は前の版のまま、マイカーは更新
        code, housing_same, car_same = self.run_pair(None, 3.1)
        self.assertEqual(update_rate_history.EXIT_PARTIAL, code)
        self.assertTrue(housing_same)
        self.assertFalse(car_same)

    def test_nothing_imported_exits_without_writing(self):
        # (iv-3) 全種別が取り込めない → 書き込まず終了コード 3
        code, housing_same, car_same = self.run_pair(None, 42.0)
        self.assertEqual(update_rate_history.EXIT_KEPT_PREVIOUS, code)
        self.assertTrue(housing_same)
        self.assertTrue(car_same)


if __name__ == "__main__":
    unittest.main()
