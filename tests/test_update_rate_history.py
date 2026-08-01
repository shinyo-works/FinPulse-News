import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.update_rate_history import update_history, write_history_atomic


class UpdateRateHistoryTest(unittest.TestCase):
    def setUp(self):
        self.history = {
            "schema_version": 1,
            "rate_type_order": ["variable"],
            "rate_type_labels": {"variable": "変動"},
            "rows": [],
        }
        self.report = {
            "loan_table": [{
                "bank_id": "test-bank",
                "bank_name": "テスト銀行",
                "product_id": "test-product",
                "product_name": "テスト商品",
                "loan_variable": 1.25,
            }]
        }

    def test_same_day_update_is_idempotent(self):
        update_history(self.history, self.report, "2026-08-01")
        update_history(self.history, self.report, "2026-08-01")

        entries = self.history["rows"][0]["history"]
        self.assertEqual(1, len(entries))
        self.assertEqual(1.25, entries[0]["rate"])

    def test_rejects_older_survey_date(self):
        update_history(self.history, self.report, "2026-08-01")

        with self.assertRaisesRegex(ValueError, "より古いため"):
            update_history(self.history, self.report, "2026-07-01")

    def test_rejects_invalid_ids_and_rates(self):
        invalid_reports = [
            {"loan_table": [{"bank_id": "", "product_id": "p", "loan_variable": 1.0}]},
            {"loan_table": [{"bank_id": "b", "product_id": "p", "loan_variable": "1.0"}]},
            {"loan_table": [{"bank_id": "b", "product_id": "p", "loan_variable": -1.0}]},
            {"loan_table": [
                {"bank_id": "b", "product_id": "p", "loan_variable": 1.0},
                {"bank_id": "b", "product_id": "p", "loan_variable": 1.1},
            ]},
        ]

        for report in invalid_reports:
            with self.subTest(report=report), self.assertRaises(ValueError):
                update_history(copy.deepcopy(self.history), report, "2026-08-01")

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


if __name__ == "__main__":
    unittest.main()
