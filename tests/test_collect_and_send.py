import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from scripts import collect_and_send as collector


def minimal_config():
    return {
        "lookback_days": 30,
        "star_keywords": ["金利"],
        "institutions": [{
            "name": "テスト銀行",
            "url": "https://example.com/news/",
            "include_keywords": ["金利"],
            "exclude_rules": [],
        }],
    }


class FakeResponse:
    def __init__(self, chunks, *, content_type="text/html", content_length=None):
        self._chunks = chunks
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self._chunks


class ValidationTest(unittest.TestCase):
    def test_accepts_current_config_shape(self):
        collector.validate_config(minimal_config())

    def test_rejects_duplicate_name_and_unknown_scraper(self):
        duplicate = minimal_config()
        duplicate["institutions"].append(dict(duplicate["institutions"][0]))
        with self.assertRaisesRegex(ValueError, "重複"):
            collector.validate_config(duplicate)

        unknown = minimal_config()
        unknown["institutions"][0]["scraper"] = "unknown"
        with self.assertRaisesRegex(ValueError, "未対応"):
            collector.validate_config(unknown)

    def test_limited_response_rejects_declared_and_streamed_oversize(self):
        with self.assertRaisesRegex(collector.FetchError, "上限"):
            collector.read_limited_response(
                FakeResponse([b"x"], content_length=11),
                max_bytes=10,
                allowed_content_types={"text/html"},
                url="https://example.com",
            )
        with self.assertRaisesRegex(collector.FetchError, "不正"):
            collector.read_limited_response(
                FakeResponse([b"x"], content_length=-1),
                max_bytes=10,
                allowed_content_types={"text/html"},
                url="https://example.com",
            )
        with self.assertRaisesRegex(collector.FetchError, "上限"):
            collector.read_limited_response(
                FakeResponse([b"123456", b"78901"]),
                max_bytes=10,
                allowed_content_types={"text/html"},
                url="https://example.com",
            )

    def test_limited_response_rejects_unexpected_content_type(self):
        with self.assertRaisesRegex(collector.FetchError, "Content-Type"):
            collector.read_limited_response(
                FakeResponse([b"{}"], content_type="application/json"),
                max_bytes=10,
                allowed_content_types={"text/html"},
                url="https://example.com",
            )

    def test_repository_config_and_existing_json_are_valid(self):
        repository_root = Path(__file__).resolve().parents[1]
        config = json.loads(
            (repository_root / "config.json").read_text(encoding="utf-8")
        )
        collector.validate_config(config)
        self.assertGreater(len(config["institutions"]), 0)

        data_dir = repository_root / "output" / "data"
        for report_path in sorted(data_dir.glob("????-??-??.json")):
            report = json.loads(report_path.read_text(encoding="utf-8"))
            collector.validate_report_document(report)

        manifest = json.loads((data_dir / "index.json").read_text(encoding="utf-8"))
        institution = json.loads(
            (data_dir / "by-institution.json").read_text(encoding="utf-8")
        )
        collector.validate_manifest(manifest)
        collector.validate_institution_index(institution)

    def test_report_requires_date_and_failure_reason(self):
        report = collector.build_report_data(
            [collector.InstitutionResult("テスト銀行", [], [], "プログラム")],
            "2026-08-01",
            30,
        )
        report.pop("date")
        with self.assertRaisesRegex(ValueError, "必須"):
            collector.validate_report_document(report)

        failed_report = collector.build_report_data(
            [collector.InstitutionResult(
                "テスト銀行",
                [],
                [],
                "プログラム",
                status="fetch_failed",
            )],
            "2026-08-01",
            30,
        )
        with self.assertRaisesRegex(ValueError, "失敗時に必須"):
            collector.validate_report_document(failed_report)


class CollectionStateTest(unittest.TestCase):
    def test_fetch_failure_has_distinct_status(self):
        institution = minimal_config()["institutions"][0]
        with mock.patch.object(
            collector,
            "fetch_page",
            side_effect=collector.FetchError("test"),
        ):
            result, _ = collector.collect_institution(
                institution,
                30,
                ["金利"],
            )

        self.assertEqual("fetch_failed", result.status)
        self.assertEqual([], result.passed)
        self.assertIn("取得できません", result.error)

    def test_partial_failure_keeps_outputs_and_returns_failure(self):
        failed = collector.InstitutionResult(
            "テスト銀行",
            [],
            [],
            "プログラム",
            status="fetch_failed",
            error="ページを取得できませんでした。",
        )
        with tempfile.TemporaryDirectory() as directory:
            previous_cwd = Path.cwd()
            os.chdir(directory)
            try:
                with mock.patch.object(
                    collector,
                    "collect_institution",
                    return_value=(failed, None),
                ), mock.patch.object(collector, "send_email", return_value=True):
                    exit_code = collector.run_collection(minimal_config(), "2026-08-01")

                self.assertEqual(1, exit_code)
                self.assertTrue(Path("output/2026-08-01.md").exists())
                self.assertTrue(Path("output/data/2026-08-01.json").exists())
                self.assertTrue(Path("output/viewer-json-ready.txt").exists())
                report = Path("output/2026-08-01.md").read_text(encoding="utf-8")
                self.assertIn("収集失敗", report)
            finally:
                os.chdir(previous_cwd)


class DateExtractionTest(unittest.TestCase):
    def test_rejects_phone_number_and_impossible_dates(self):
        self.assertEqual("", collector.extract_date_from_text("お問い合わせ: 0155-24-1234"))
        self.assertEqual("", collector.extract_date_from_text("2026.13.01 のお知らせ"))
        self.assertEqual("", collector.extract_date_from_text("2026年2月30日"))

    def test_skips_invalid_candidate_and_finds_real_date(self):
        text = "TEL 0155-24-1234 / 2026年8月1日更新"
        self.assertEqual("2026-08-01", collector.extract_date_from_text(text))
        self.assertEqual("2026-08-01", collector.extract_date_from_text("2026.8.1"))

    def test_url_extraction_accepts_only_real_dates(self):
        self.assertEqual(
            "2026-05-28",
            collector.extract_date_from_url("https://example.com/detail/20260528_news.html"),
        )
        self.assertEqual(
            "",
            collector.extract_date_from_url("https://example.com/detail/12345678_news.html"),
        )


class DecodeHtmlTest(unittest.TestCase):
    def test_explicit_encoding_decodes_shift_jis(self):
        content = "<html><body>住宅ローン金利のお知らせ</body></html>".encode("cp932")
        self.assertIn("住宅ローン金利のお知らせ", collector.decode_html(content, "cp932"))

    def test_auto_detection_decodes_shift_jis_without_mojibake(self):
        # encoding 未指定時は実体からの自動判定（従来の apparent_encoding 相当）で復元できること
        content = ("<html><head><title>北洋銀行</title></head><body>"
                   + "住宅ローン金利とキャンペーンのお知らせ。振込手数料の改定について。" * 20
                   + "</body></html>").encode("cp932")
        decoded = collector.decode_html(content)
        self.assertIn("住宅ローン金利", decoded)
        self.assertNotIn("�", decoded)

    def test_unknown_encoding_falls_back_without_error(self):
        content = "<html>fallback</html>".encode("utf-8")
        self.assertIn("fallback", collector.decode_html(content, "no-such-codec"))


class SanitizeItemsTest(unittest.TestCase):
    def test_drops_invalid_items_and_blanks_bad_fields(self):
        items = [
            "文字列は項目ではない",
            {"title": "  ", "url": "https://example.com/1"},
            {"title": "正常な記事", "url": "https://example.com/2", "date": "2026-08-01"},
            {"title": "URLが不正", "url": "javascript:alert(1)", "date": "2026-08-01"},
            {"title": "日付が不正", "url": "https://example.com/3", "date": "1234-56-78"},
        ]

        cleaned = collector.sanitize_items(items, "テスト銀行")

        self.assertEqual(
            ["正常な記事", "URLが不正", "日付が不正"],
            [item["title"] for item in cleaned],
        )
        self.assertEqual("", cleaned[1]["url"])
        self.assertEqual("", cleaned[2]["date"])
        self.assertEqual("https://example.com/2", cleaned[0]["url"])
        self.assertEqual("2026-08-01", cleaned[0]["date"])

    def test_non_list_input_returns_empty(self):
        self.assertEqual([], collector.sanitize_items(None, "テスト銀行"))
        self.assertEqual([], collector.sanitize_items({"title": "dict"}, "テスト銀行"))


class FilteringTest(unittest.TestCase):
    def test_ja_obihirokawanisi_keeps_financial_news_only(self):
        repository_root = Path(__file__).resolve().parents[1]
        config = json.loads(
            (repository_root / "config.json").read_text(encoding="utf-8")
        )
        institution = next(
            item
            for item in config["institutions"]
            if item["name"] == "JA帯広かわにし"
        )
        items = [
            {
                "date": "2026-07-31",
                "title": "ＪＡバンク貯金 各種手数料改定のお知らせ（再案内）",
                "url": "https://example.com/fee.pdf",
            },
            {
                "date": "2026-06-10",
                "title": "夏の定期貯金 金利上乗せキャンペーン",
                "url": "https://example.com/campaign.pdf",
            },
            {
                "date": "",
                "title": "キャンペーン情報",
                "url": "https://example.com/campaign/",
            },
            {
                "date": "2026-06-26",
                "title": "APIサービスに関する規定の改正について",
                "url": "https://example.com/rule.pdf",
            },
            {
                "date": "2026-06-23",
                "title": "預貯金等の不正な払戻しへの対応について",
                "url": "https://example.com/security.pdf",
            },
            {
                "date": "",
                "title": "各種手数料はこちら",
                "url": "https://example.com/fee/",
            },
        ]

        passed, excluded = collector.apply_filters(
            items,
            institution,
            config["star_keywords"],
        )

        self.assertEqual(
            [
                "ＪＡバンク貯金 各種手数料改定のお知らせ（再案内）",
                "夏の定期貯金 金利上乗せキャンペーン",
            ],
            [item["title"] for item in passed],
        )
        self.assertFalse(passed[0].get("star", False))
        self.assertTrue(passed[1]["star"])
        self.assertEqual(4, len(excluded))
        self.assertEqual("utf-8", institution["encoding"])

    def test_include_exclude_unless_and_star_rules(self):
        institution = minimal_config()["institutions"][0]
        institution["exclude_rules"] = [{"keyword": "規定", "unless": ["改定"]}]
        items = [
            {"date": "2026-08-01", "title": "規定のお知らせ", "url": "https://example.com/1"},
            {"date": "2026-08-01", "title": "規定改定と金利", "url": "https://example.com/2"},
            {"date": "2026-08-01", "title": "採用情報", "url": "https://example.com/3"},
        ]

        passed, excluded = collector.apply_filters(items, institution, ["金利"])

        self.assertEqual(["規定改定と金利"], [item["title"] for item in passed])
        self.assertTrue(passed[0]["star"])
        self.assertEqual("規定", excluded[0]["exclude_keyword"])
        self.assertEqual(2, len(excluded))

    def test_title_annotations_roundtrip(self):
        # format_report が付けた注記を clean_report_title が正確に剥がせること
        # （ANNOTATION_BY_FLAG が付与・除去の唯一の対応表であることの検査）
        item = {
            "date": "2026-08-01",
            "title": "住宅ローン金利のお知らせ",
            "url": "https://example.com/1",
            "star": True,
            "fallback": True,
            "date_inferred": True,
        }
        result = collector.InstitutionResult("テスト銀行", [item], [], "プログラム")

        report = collector.format_report([result], "2026-08-01", 30)

        line = next(l for l in report.splitlines() if "住宅ローン金利のお知らせ" in l)
        for annotation in collector.ANNOTATION_BY_FLAG.values():
            self.assertIn(annotation, line)
        title_cell = line.split("|")[2].strip()
        self.assertEqual("住宅ローン金利のお知らせ", collector.clean_report_title(title_cell))

    def test_lookback_keeps_cutoff_and_unknown_date(self):
        items = [
            {"date": "2026-07-02", "title": "境界日"},
            {"date": "2026-07-01", "title": "期間外"},
            {"date": "", "title": "日付不明"},
        ]
        with mock.patch.object(
            collector,
            "now_jst",
            return_value=datetime(2026, 8, 1, tzinfo=collector.JST),
        ):
            filtered = collector.filter_by_lookback(items, 30)

        self.assertEqual(["境界日", "日付不明"], [item["title"] for item in filtered])


class ViewerJsonWriteTest(unittest.TestCase):
    def setUp(self):
        self.result = collector.InstitutionResult(
            "テスト銀行",
            [{
                "date": "2026-08-01",
                "title": "住宅ローン金利のお知らせ",
                "url": "https://example.com/news/1",
            }],
            [],
            "プログラム",
        )

    def test_writes_valid_three_file_set_and_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "output" / "data"
            collector.write_json_viewer_data(
                [self.result],
                "2026-08-01",
                30,
                data_dir=data_dir,
                institution_order=["テスト銀行"],
            )

            report = json.loads((data_dir / "2026-08-01.json").read_text(encoding="utf-8"))
            manifest = json.loads((data_dir / "index.json").read_text(encoding="utf-8"))
            institution = json.loads((data_dir / "by-institution.json").read_text(encoding="utf-8"))
            collector.validate_report_document(report)
            collector.validate_manifest(manifest)
            collector.validate_institution_index(institution)
            self.assertTrue((data_dir.parent / "viewer-json-ready.txt").exists())

    def test_generation_failure_preserves_existing_files_without_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "output" / "data"
            data_dir.mkdir(parents=True)
            existing = {
                "index.json": b"old-index",
                "by-institution.json": b"old-institution",
            }
            for name, content in existing.items():
                (data_dir / name).write_bytes(content)

            with mock.patch.object(
                collector,
                "build_institution_index",
                side_effect=ValueError("test"),
            ), self.assertRaisesRegex(ValueError, "test"):
                collector.write_json_viewer_data(
                    [self.result],
                    "2026-08-01",
                    30,
                    data_dir=data_dir,
                    institution_order=["テスト銀行"],
                )

            for name, content in existing.items():
                self.assertEqual(content, (data_dir / name).read_bytes())
            self.assertFalse((data_dir.parent / "viewer-json-ready.txt").exists())


if __name__ == "__main__":
    unittest.main()
