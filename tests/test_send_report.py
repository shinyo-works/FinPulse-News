import tempfile
import unittest
from pathlib import Path
from unittest import mock

import send_report


class FindReportTest(unittest.TestCase):
    def test_accepts_only_existing_date_report_under_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            report = output / "2026-08-01.md"
            report.write_text("test", encoding="utf-8")

            with mock.patch.object(send_report, "__file__", str(root / "send_report.py")):
                path, date = send_report.find_report("2026-08-01")

            self.assertEqual(report.resolve(), path)
            self.assertEqual("2026-08-01", date)

    def test_latest_selection_ignores_non_date_filenames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            (output / "2026-08-01.md").write_text("real", encoding="utf-8")
            # glob の ? は任意1文字にマッチするため、数字でない偽名を混入させる
            (output / "abcd-ef-gh.md").write_text("fake", encoding="utf-8")

            with mock.patch.object(send_report, "__file__", str(root / "send_report.py")):
                path, date = send_report.find_report()

            self.assertEqual("2026-08-01", date)
            self.assertEqual("2026-08-01.md", path.name)

    def test_rejects_traversal_and_invalid_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "output").mkdir()
            with mock.patch.object(send_report, "__file__", str(root / "send_report.py")):
                for value in ("../CLAUDE", "2026-02-30", "2026-8-1"):
                    with self.subTest(value=value):
                        self.assertEqual((None, None), send_report.find_report(value))


if __name__ == "__main__":
    unittest.main()
