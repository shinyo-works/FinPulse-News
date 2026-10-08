import json
import os
import re
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
    def __init__(self, chunks, *, content_type="text/html", content_length=None, status_code=200):
        self._chunks = chunks
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise collector.requests.HTTPError(f"{self.status_code} error", response=self)
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        del exc_type, exc_value, traceback
        return False


JA_OBIHIROKAWANISI_HTML = """
<section class="content_section news_list">
  <ul>
    <li class="news_content">
      <a href="/wp/fee.pdf">
        <time class="news_content__date">2026.07.31</time>
        <span class="news_content__label">お知らせ 重要なお知らせ ＪＡバンク 貯金</span>
        <span class="news_content__text">各種手数料改定のお知らせ（再案内）</span>
      </a>
    </li>
    <li class="news_content">
      <a href="/wp/api-rule.pdf">
        <time class="news_content__date">2026.06.26</time>
        <span class="news_content__label">お知らせ 重要なお知らせ ＪＡバンク 貯金</span>
        <span class="news_content__text">「APIサービスに関する規定」にかかる改正について</span>
      </a>
    </li>
    <li class="news_content">
      <a href="/wp/security.pdf">
        <time class="news_content__date">2026.06.23</time>
        <span class="news_content__label">お知らせ 重要なお知らせ ＪＡバンク 貯金</span>
        <span class="news_content__text">預貯金等の不正な払戻しへのJAバンクの対応について</span>
      </a>
    </li>
    <li class="news_content">
      <a href="/wp/campaign.pdf">
        <time class="news_content__date">2026.06.10</time>
        <span class="news_content__label">お知らせ ＪＡバンク 貯金</span>
        <span class="news_content__text">夏の定期貯金 金利上乗せキャンペーン</span>
      </a>
    </li>
    <li class="news_content">
      <a href="/wp/car-loan.pdf">
        <time class="news_content__date">2026.04.01</time>
        <span class="news_content__label">お知らせ キャンペーン ＪＡバンク ローン</span>
        <span class="news_content__text">マイカーローン金利情報</span>
      </a>
    </li>
  </ul>
</section>
"""


JA_KINO_HTML = """
<section class="l-news-list">
  <ul class="news">
    <li id="post_833">
      <a href="/wp-content/uploads/2026/08/jakino_info_20260824.pdf">
        <div class="text-block">
          <div class="meta">
            <div class="date">2026.08.25</div>
            <div class="category">JAバンク/借りる</div>
          </div>
          <div class="title">短期プライムレートおよび住宅ローンプライムレートの見直しについて</div>
        </div>
      </a>
    </li>
    <li id="post_829">
      <a href="/wp-content/uploads/2026/08/jakino_info_20260810.pdf">
        <div class="text-block">
          <div class="meta">
            <div class="date">2026.08.10</div>
            <div class="category">JAバンク/貯める</div>
          </div>
          <div class="title">貯金金利の引き上げについて</div>
        </div>
      </a>
    </li>
    <li id="post_824">
      <a href="/wp-content/uploads/2026/07/jakino_info_20260728.pdf">
        <div class="text-block">
          <div class="meta">
            <div class="date">2026.07.28</div>
            <div class="category">JAバンク/貯める</div>
          </div>
          <div class="title">手形・小切手の全面的な電子化に向けた対応について</div>
        </div>
      </a>
    </li>
    <li id="post_820">
      <a href="/wp-content/uploads/2026/07/jakino_info_20260723.pdf">
        <div class="text-block">
          <div class="meta">
            <div class="date">2026.07.23</div>
            <div class="category">JAバンク/借りる</div>
          </div>
          <div class="title">JA木野住宅ローン借換キャンペーン実施中！</div>
        </div>
      </a>
    </li>
    <li id="post_815">
      <a href="/about/#letter">
        <div class="text-block">
          <div class="meta">
            <div class="date">2026.07.21</div>
            <div class="category">お知らせ</div>
          </div>
          <div class="title">組合だよりを更新しました！</div>
        </div>
      </a>
    </li>
    <li id="post_790">
      <a href="/wp-content/uploads/2026/02/jakino_info_20260206.pdf">
        <div class="text-block">
          <div class="meta">
            <div class="date">2026.02.06</div>
            <div class="category">JAバンク/借りる</div>
          </div>
          <div class="title">短期プライムレートの引き上げについて</div>
        </div>
      </a>
    </li>
  </ul>
</section>
"""


class ValidationTest(unittest.TestCase):
    def test_accepts_current_config_shape(self):
        collector.validate_config(minimal_config())

    def test_frozen_claude_path_cannot_be_enabled_by_config(self):
        config = minimal_config()
        config["institutions"][0]["use_claude"] = True
        with self.assertRaisesRegex(ValueError, "凍結中"):
            collector.validate_config(config)

        config["institutions"][0]["use_claude"] = False
        collector.validate_config(config)

    def test_common_include_keywords_are_merged_into_each_institution(self):
        config = minimal_config()
        config["common_include_keywords"] = ["お知らせ", "金利"]
        config["institutions"][0]["include_keywords"] = ["金利", "手数料"]
        collector.validate_config(config)
        collector.normalize_config(config)
        self.assertEqual(
            ["お知らせ", "金利", "手数料"],
            config["institutions"][0]["include_keywords"],
        )

        config["common_include_keywords"] = ["お知らせ", ""]
        with self.assertRaisesRegex(ValueError, "common_include_keywords"):
            collector.validate_config(config)

    def test_repository_institutions_all_receive_common_keywords(self):
        repository_root = Path(__file__).resolve().parents[1]
        config = json.loads((repository_root / "config.json").read_text(encoding="utf-8"))
        common = config["common_include_keywords"]
        self.assertTrue(common)
        collector.normalize_config(config)
        for institution in config["institutions"]:
            if institution.get("include_all"):
                # 全件通過の機関（JA帯広かわにし）は語で判定しないため、何も合成しない。
                self.assertEqual([], institution["include_keywords"])
                self.assertEqual([], institution["common_exclude_keywords"])
                self.assertEqual([], institution["priority_keywords"])
                continue
            # 合成後の通過語が空だと apply_filters は全件を通すため、必ず共通語を含む。
            self.assertEqual(common, institution["include_keywords"][: len(common)])
            self.assertEqual(config["common_exclude_keywords"], institution["common_exclude_keywords"])
            self.assertEqual(config["common_priority_keywords"], institution["priority_keywords"])

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

    def test_limited_response_stops_slow_drip_after_deadline(self):
        # 監査 W9: 少しずつ返し続けるサイトで、収集全体が打ち切られないようにする
        ticks = iter([0, 10, 70, 80])
        with self.assertRaisesRegex(collector.FetchError, "60 秒"):
            collector.read_limited_response(
                FakeResponse([b"a", b"b", b"c"]),
                max_bytes=10,
                allowed_content_types={"text/html"},
                url="https://example.com",
                clock=lambda: next(ticks),
            )

    def test_fetch_limited_stops_real_slow_drip_server(self):
        # 監査 W9 の再レビュー: 塊がそろうまで戻らない読み取りでも、締切で見切れること
        import http.server
        import threading
        import time as time_module

        stop = threading.Event()

        class DripHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", "100000")
                self.end_headers()
                while not stop.is_set():
                    try:
                        self.wfile.write(b"a")
                        self.wfile.flush()
                    except OSError:
                        return
                    time_module.sleep(0.1)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), DripHandler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            started = time_module.monotonic()
            with self.assertRaisesRegex(collector.FetchError, "1 秒を超えた"):
                collector.fetch_limited(
                    f"http://127.0.0.1:{server.server_address[1]}/",
                    headers={},
                    max_bytes=10 * 1024 * 1024,
                    allowed_content_types={"text/html"},
                    max_seconds=1,
                )
            self.assertLess(time_module.monotonic() - started, 5)
        finally:
            stop.set()
            server.shutdown()
            server.server_close()

    def test_limited_response_within_deadline_is_read(self):
        ticks = iter([0, 1, 2, 3])
        content = collector.read_limited_response(
            FakeResponse([b"ab", b"c"]),
            max_bytes=10,
            allowed_content_types={"text/html"},
            url="https://example.com",
            clock=lambda: next(ticks),
        )
        self.assertEqual(content, b"abc")

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

    def test_programmatic_page_without_article_links_is_extraction_failure(self):
        institution = minimal_config()["institutions"][0]
        html = "<html><body><p>Access denied</p><a href='/'>TOP</a></body></html>"
        with mock.patch.object(collector, "fetch_page", return_value=html):
            result, _ = collector.collect_institution(institution, 30, ["金利"])

        self.assertEqual("extract_failed", result.status)
        self.assertIn(result.status, collector.FAILED_STATUSES)

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
    def test_fetch_page_uses_shared_connect_and_read_timeout(self):
        response = FakeResponse([b"<html>ok</html>"])
        with mock.patch.object(
            collector.requests, "get", return_value=response
        ) as request:
            collector.fetch_page("https://example.com/news/")

        self.assertEqual(collector.REQUEST_TIMEOUT, request.call_args.kwargs["timeout"])

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


class ProgrammaticScraperTest(unittest.TestCase):
    def test_deduplicates_only_the_same_title_and_url(self):
        html = """
        <a href="/news/1">住宅ローン金利のお知らせ</a>
        <a href="/news/1">住宅ローン金利のお知らせ</a>
        <a href="/news/2">住宅ローン金利のお知らせ</a>
        """

        items = collector.scrape_news_programmatic(html, "https://example.com/")

        self.assertEqual(
            ["https://example.com/news/1", "https://example.com/news/2"],
            [item["url"] for item in items],
        )


    def test_stops_at_the_item_limit(self):
        html = "".join(
            f'<a href="/news/{index}">住宅ローン金利のお知らせ{index:03d}</a>'
            for index in range(collector.MAX_PROGRAMMATIC_ITEMS + 5)
        )

        items = collector.scrape_news_programmatic(html, "https://example.com/")

        self.assertEqual(collector.MAX_PROGRAMMATIC_ITEMS, len(items))
        self.assertEqual("https://example.com/news/0", items[0]["url"])


class HokuyoXmlTest(unittest.TestCase):
    XML_WITH_ARTICLE = """<?xml version="1.0" encoding="UTF-8"?>
    <announcements><article><viewdate>2026.08.20</viewdate>
    <title href="detail/1.pdf">住宅ローン金利のお知らせ (PDF 2.4MB)</title>
    </article></announcements>""".encode("utf-8")
    EMPTY_XML = b"<?xml version=\"1.0\"?><announcements></announcements>"

    def xml_response(self, content):
        return FakeResponse([content], content_type="application/xml")

    def test_requires_both_yearly_feeds(self):
        with mock.patch.object(
            collector.requests,
            "get",
            side_effect=[
                self.xml_response(self.XML_WITH_ARTICLE),
                collector.requests.Timeout("前年フィードがタイムアウト"),
            ],
        ), self.assertRaisesRegex(collector.FetchError, "北洋銀行のXML"):
            collector.scrape_hokuyo_xml(
                "https://www.hokuyobank.co.jp/announcement/"
            )

    def not_found(self):
        return FakeResponse([b""], content_type="text/html", status_code=404)

    def scrape_at(self, month, responses):
        with mock.patch.object(
            collector, "now_jst", return_value=datetime(2027, month, 4, 5, 0)
        ), mock.patch.object(collector.requests, "get", side_effect=responses):
            return collector.scrape_hokuyo_xml("https://www.hokuyobank.co.jp/announcement/")

    def test_this_years_feed_may_be_missing_only_in_january(self):
        items = self.scrape_at(1, [self.not_found(), self.xml_response(self.XML_WITH_ARTICLE)])
        self.assertEqual(["住宅ローン金利のお知らせ"], [item["title"] for item in items])

        with self.assertRaisesRegex(collector.FetchError, "北洋銀行のXML"):
            self.scrape_at(2, [self.not_found(), self.xml_response(self.XML_WITH_ARTICLE)])

    def test_last_years_feed_is_still_required_in_january(self):
        with self.assertRaisesRegex(collector.FetchError, "北洋銀行のXML"):
            self.scrape_at(1, [self.xml_response(self.XML_WITH_ARTICLE), self.not_found()])

    def test_rejects_two_successful_but_empty_feeds(self):
        with mock.patch.object(
            collector.requests,
            "get",
            side_effect=[
                self.xml_response(self.EMPTY_XML),
                self.xml_response(self.EMPTY_XML),
            ],
        ), self.assertRaisesRegex(collector.ExtractionError, "ニュース記事"):
            collector.scrape_hokuyo_xml(
                "https://www.hokuyobank.co.jp/announcement/"
            )

    def test_keeps_normal_parsing_deduplication_and_pdf_suffix_removal(self):
        with mock.patch.object(
            collector.requests,
            "get",
            side_effect=[
                self.xml_response(self.XML_WITH_ARTICLE),
                self.xml_response(self.XML_WITH_ARTICLE),
            ],
        ) as request:
            items = collector.scrape_hokuyo_xml(
                "https://www.hokuyobank.co.jp/announcement/"
            )

        self.assertEqual(1, len(items))
        self.assertEqual("住宅ローン金利のお知らせ", items[0]["title"])
        self.assertEqual("2026-08-20", items[0]["date"])
        self.assertEqual(2, request.call_count)
        for call in request.call_args_list:
            self.assertEqual(collector.REQUEST_TIMEOUT, call.kwargs["timeout"])


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


class JaObihirokawanisiTest(unittest.TestCase):
    def setUp(self):
        repository_root = Path(__file__).resolve().parents[1]
        config = collector.normalize_config(json.loads(
            (repository_root / "config.json").read_text(encoding="utf-8")
        ))
        self.config = config
        self.institution = next(
            item
            for item in config["institutions"]
            if item["name"] == "JA帯広かわにし"
        )

    def test_extracts_clean_titles_and_keeps_recent_financial_news(self):
        items = collector.scrape_ja_obihirokawanisi(
            JA_OBIHIROKAWANISI_HTML,
            self.institution["url"],
        )

        self.assertEqual(5, len(items))
        self.assertEqual("各種手数料改定のお知らせ（再案内）", items[0]["title"])
        self.assertEqual("2026-07-31", items[0]["date"])
        self.assertEqual(
            "https://www.jaobihirokawanisi.or.jp/wp/fee.pdf",
            items[0]["url"],
        )
        self.assertNotIn("重要なお知らせ", items[0]["title"])
        self.assertNotIn("2026.07.31", items[0]["title"])

        passed_all, excluded = collector.apply_filters(
            items,
            self.institution,
            self.config["star_keywords"],
        )
        # 本人指示（2026-10-08）: JA帯広かわにしの金融ニュースは除外しない。
        # 以前は「規定」「不正」で落としていた記事も通す。
        self.assertTrue(self.institution["include_all"])
        self.assertEqual([item["title"] for item in items], [item["title"] for item in passed_all])
        self.assertEqual([], excluded)

        with mock.patch.object(
            collector,
            "now_jst",
            return_value=datetime(2026, 8, 20, tzinfo=collector.JST),
        ):
            passed = collector.filter_by_lookback(passed_all, 90)

        # 期間（90日）の絞り込みだけは他機関と同じく効く。
        self.assertEqual(
            [
                "各種手数料改定のお知らせ（再案内）",
                "「APIサービスに関する規定」にかかる改正について",
                "預貯金等の不正な払戻しへのJAバンクの対応について",
                "夏の定期貯金 金利上乗せキャンペーン",
            ],
            [item["title"] for item in passed],
        )
        self.assertFalse(passed[0].get("star", False))
        self.assertTrue(passed[3]["star"])
        self.assertEqual("utf-8", self.institution["encoding"])
        self.assertEqual("ja_obihirokawanisi", self.institution["scraper"])

        result = collector.InstitutionResult(
            self.institution["name"],
            passed,
            excluded,
            "プログラム",
        )
        report = collector.format_report([result], "2026-08-20", 90)
        report_data = collector.build_report_data([result], "2026-08-20", 90)
        self.assertIn("各種手数料改定のお知らせ（再案内）", report)
        self.assertNotIn("2026.07.31お知らせ", report)
        self.assertEqual(
            "各種手数料改定のお知らせ（再案内）",
            report_data["institutions"][0]["passed"][0]["title"],
        )

    def test_missing_news_structure_is_reported_as_extraction_failure(self):
        with self.assertRaisesRegex(collector.ExtractionError, "ニュース一覧"):
            collector.scrape_ja_obihirokawanisi(
                "<html><body>ニュース領域なし</body></html>",
                self.institution["url"],
            )

        with mock.patch.object(
            collector,
            "fetch_page",
            return_value="<html><body>ニュース領域なし</body></html>",
        ):
            result, _ = collector.collect_institution(
                self.institution,
                90,
                self.config["star_keywords"],
            )

        self.assertEqual("extract_failed", result.status)
        self.assertEqual("記事一覧を抽出できませんでした。", result.error)

    def test_all_missing_dates_are_reported_as_structure_failure(self):
        html_without_dates = re.sub(
            r'<time class="news_content__date">.*?</time>',
            "",
            JA_OBIHIROKAWANISI_HTML,
        )

        with self.assertRaisesRegex(collector.ExtractionError, "日付"):
            collector.scrape_ja_obihirokawanisi(
                html_without_dates,
                self.institution["url"],
            )

    def test_partial_missing_dates_keep_the_existing_fail_open_behavior(self):
        html_with_one_missing_date = JA_OBIHIROKAWANISI_HTML.replace(
            '<time class="news_content__date">2026.07.31</time>',
            "",
            1,
        )

        items = collector.scrape_ja_obihirokawanisi(
            html_with_one_missing_date,
            self.institution["url"],
        )

        self.assertEqual("", items[0]["date"])
        self.assertTrue(any(item["date"] for item in items[1:]))

    def test_date_can_be_recovered_from_article_url(self):
        html = """
        <section class="content_section news_list"><ul>
          <li class="news_content"><a href="/wp/20260820_campaign.pdf">
            <span class="news_content__text">定期貯金キャンペーンのお知らせ</span>
          </a></li>
        </ul></section>
        """

        items = collector.scrape_ja_obihirokawanisi(html, self.institution["url"])

        self.assertEqual("2026-08-20", items[0]["date"])


class JaOtofukeExcludeTest(unittest.TestCase):
    """JAおとふけはホクレン給油所の記事が金融の記事と同じ一覧に混ざる。
    記事名だけでは分けられないため、URL照合の除外ルールと併用する。"""

    # 実レポート（output/data/*.json）に通過として残っていた13本。
    REAL_PASSED_ARTICLES = [
        ("2026-09-04", "ホクレン灯油定期配送キャンペーンのお知らせ", "/2023/13123/", False),
        ("2026-08-11", "【イベント】ウェルカムキャンペーンのお知らせ", "/2023/13065/", False),
        ("2026-08-10", "金利改定のご案内（貯金）", "/2023/13055/", True),
        ("2026-08-03", "金利改定のご案内（貯金・貸付金）", "/2023/13045/", True),
        ("2026-07-13", "第1スタンド日曜日営業のお知らせ", "/hokurennews/13006/", False),
        ("2026-06-23", "【イベント】ウェルカムキャンペーンのお知らせ", "/2023/12965/", False),
        ("2026-06-03", "営業時間変更のお知らせ", "/hokurennews/12937/", False),
        ("2026-05-01", "キャンペーン金利のご案内（貸付金）", "/2023/12882/", True),
        ("2026-04-01", "定期貯金（独自取扱商品）キャンペーンのご案内", "/2023/12715/", True),
        ("2026-03-23", "金融店舗における昼休み時間帯（12時〜13時）の対応について", "/2023/12698/", True),
        ("2026-02-26", "金利改定のご案内（貯金）", "/2023/12629/", True),
        ("2026-02-24", "金融店舗の営業時間変更（お昼休み導入）について", "/2023/12618/", True),
        ("2026-02-03", "金利改定のご案内（貯金・貸付金）", "/2023/12596/", True),
    ]

    def setUp(self):
        repository_root = Path(__file__).resolve().parents[1]
        config = collector.normalize_config(json.loads(
            (repository_root / "config.json").read_text(encoding="utf-8")
        ))
        self.config = config
        self.institution = next(
            item for item in config["institutions"] if item["name"] == "JAおとふけ"
        )

    def _items(self):
        return [
            {
                "date": date,
                "title": title,
                "url": f"https://www.ja-otofuke.jp{path}",
            }
            for date, title, path, _ in self.REAL_PASSED_ARTICLES
        ]

    def test_real_articles_split_into_financial_and_gas_station(self):
        passed, excluded = collector.apply_filters(
            self._items(),
            self.institution,
            self.config["star_keywords"],
        )

        self.assertEqual(
            [title for _, title, _, keep in self.REAL_PASSED_ARTICLES if keep],
            [item["title"] for item in passed],
        )
        self.assertEqual(
            [title for _, title, _, keep in self.REAL_PASSED_ARTICLES if not keep],
            [item["title"] for item in excluded],
        )
        # 「営業時間変更のお知らせ」はURLで落とす。同名で始まる金融店舗の記事は残す。
        self.assertIn(
            "金融店舗の営業時間変更（お昼休み導入）について",
            [item["title"] for item in passed],
        )
        self.assertEqual(
            ["灯油", "【イベント】", "/hokurennews/", "【イベント】", "/hokurennews/"],
            [item["exclude_keyword"] for item in excluded],
        )

    def test_event_rule_keeps_financial_events(self):
        items = [{
            "date": "2026-09-01",
            "title": "【イベント】住宅ローン相談会のお知らせ",
            "url": "https://www.ja-otofuke.jp/2023/99999/",
        }]

        passed, excluded = collector.apply_filters(
            items,
            self.institution,
            self.config["star_keywords"],
        )

        self.assertEqual(1, len(passed))
        self.assertEqual([], excluded)

    def test_url_rule_ignores_the_same_word_in_the_title(self):
        items = [{
            "date": "2026-09-01",
            "title": "「/hokurennews/」表記を含む金利のお知らせ",
            "url": "https://www.ja-otofuke.jp/2023/99998/",
        }]

        passed, _ = collector.apply_filters(
            items,
            self.institution,
            self.config["star_keywords"],
        )

        self.assertEqual(1, len(passed))

    def test_rejects_unknown_exclude_target(self):
        config = minimal_config()
        config["institutions"][0]["exclude_rules"] = [
            {"keyword": "テスト", "target": "body"}
        ]

        with self.assertRaisesRegex(ValueError, "target"):
            collector.validate_config(config)

    def test_missing_target_defaults_to_title(self):
        config = minimal_config()
        config["institutions"][0]["exclude_rules"] = [{"keyword": "テスト"}]
        collector.validate_config(config)

        passed, excluded = collector.apply_filters(
            [{"title": "テストのお知らせ", "url": "https://example.com/a", "date": "2026-09-01"}],
            config["institutions"][0],
            self.config["star_keywords"],
        )

        self.assertEqual([], passed)
        self.assertEqual("テスト", excluded[0]["exclude_keyword"])


class JaKinoTest(unittest.TestCase):
    def setUp(self):
        repository_root = Path(__file__).resolve().parents[1]
        config = collector.normalize_config(json.loads(
            (repository_root / "config.json").read_text(encoding="utf-8")
        ))
        self.config = config
        self.institution = next(
            item for item in config["institutions"] if item["name"] == "JA木野"
        )

    def test_extracts_clean_titles_without_date_or_category(self):
        items = collector.scrape_ja_kino(JA_KINO_HTML, self.institution["url"])

        self.assertEqual(6, len(items))
        self.assertEqual(
            "短期プライムレートおよび住宅ローンプライムレートの見直しについて",
            items[0]["title"],
        )
        self.assertEqual("2026-08-25", items[0]["date"])
        self.assertEqual(
            "https://ja-kino.com/wp-content/uploads/2026/08/jakino_info_20260824.pdf",
            items[0]["url"],
        )
        # 汎用抽出では日付とパンくずが記事名へ連結されていた。
        for item in items:
            self.assertNotIn("JAバンク", item["title"])
            self.assertNotIn("2026.", item["title"])
        self.assertEqual("ja_kino", self.institution["scraper"])

    def test_filters_judge_the_article_name_only(self):
        items = collector.scrape_ja_kino(JA_KINO_HTML, self.institution["url"])

        passed, excluded = collector.apply_filters(
            items,
            self.institution,
            self.config["star_keywords"],
        )

        self.assertEqual(
            [
                "短期プライムレートおよび住宅ローンプライムレートの見直しについて",
                "貯金金利の引き上げについて",
                # 「金利」も「ローン」も含まないが、手形・小切手の電子化は
                # 当座勘定を持つ利用者に直接影響するため通過させる。
                "手形・小切手の全面的な電子化に向けた対応について",
                "JA木野住宅ローン借換キャンペーン実施中！",
                # 「ローン」を含まないプライムレート記事も通過させる。
                "短期プライムレートの引き上げについて",
            ],
            [item["title"] for item in passed],
        )
        self.assertEqual(
            ["組合だよりを更新しました！"],
            [item["title"] for item in excluded],
        )
        self.assertEqual("組合だより", excluded[0]["exclude_keyword"])
        self.assertIn("プライムレート", self.institution["include_keywords"])
        self.assertIn("手形", self.institution["include_keywords"])

    def test_missing_news_structure_is_reported_as_extraction_failure(self):
        with self.assertRaisesRegex(collector.ExtractionError, "ニュース一覧"):
            collector.scrape_ja_kino(
                "<html><body>ニュース領域なし</body></html>",
                self.institution["url"],
            )

        with mock.patch.object(
            collector,
            "fetch_page",
            return_value="<html><body>ニュース領域なし</body></html>",
        ):
            result, _ = collector.collect_institution(
                self.institution,
                90,
                self.config["star_keywords"],
            )

        self.assertEqual("extract_failed", result.status)
        self.assertEqual("記事一覧を抽出できませんでした。", result.error)

    def test_all_missing_dates_are_reported_as_structure_failure(self):
        html_without_dates = re.sub(
            r'<div class="date">.*?</div>',
            "",
            JA_KINO_HTML,
        )

        with self.assertRaisesRegex(collector.ExtractionError, "日付"):
            collector.scrape_ja_kino(html_without_dates, self.institution["url"])

    def test_partial_missing_dates_keep_the_existing_fail_open_behavior(self):
        # URL側は日付抽出の型（/8桁/）に一致させ、それでも補完しないことを確かめる。
        html_with_one_missing_date = JA_KINO_HTML.replace(
            '<div class="date">2026.08.10</div>',
            "",
            1,
        ).replace(
            "/2026/08/jakino_info_20260810.pdf",
            "/2026/08/20260809/jakino_info.pdf",
            1,
        )
        self.assertEqual(
            "2026-08-09",
            collector.extract_date_from_url("https://example.com/2026/08/20260809/jakino_info.pdf"),
        )

        items = collector.scrape_ja_kino(
            html_with_one_missing_date,
            self.institution["url"],
        )

        # JA木野のURL内の数字は掲載日ではないため補完に使わない。
        self.assertEqual("", items[1]["date"])
        self.assertTrue(any(item["date"] for item in items))


class FilteringTest(unittest.TestCase):

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


# 北海道銀行 お知らせ一覧（/info/）の実構造を縮めたもの（2026-10-08 取得）。
HOKKAIDOBANK_INFO_HTML = """
<nav class="c-tab c-tab-column-2"><ul class="c-tab-list">
  <li class="c-tab-list__item js-tab" data-tab="1"><span class="c-tab-list__item-inner">お知らせ</span></li>
  <li class="c-tab-list__item js-tab" data-tab="2"><span class="c-tab-list__item-inner">ニュース<br>リリース</span></li>
  <li class="c-tab-list__item js-tab" data-tab="3"><span class="c-tab-list__item-inner">EB情報</span></li>
  <li class="c-tab-list__item js-tab" data-tab="4"><span class="c-tab-list__item-inner">カーリング</span></li>
</ul></nav>
<div class="c-tab-area">
  <div class="c-tab-area__item js-tab-area" data-tab="1"><div class="tab-content"><ul class="p-news-list">
    <li><a href="/info/uploads/a0bb.pdf" target="_blank">
      <div class="news-date"><p>2026.09.14</p><p class="category kojin">個人のお客さま</p><p class="category houjin">法人のお客さま</p></div>
      <p class="news-lead">各種預金規定改定のお知らせ</p></a></li>
    <li><a href="/info/6148.html">
      <div class="news-date"><p>2026.09.30</p><p class="category kojin">個人のお客さま</p></div>
      <p class="news-lead">「エコノミクス甲子園 北海道大会」エントリー締切延長のお知らせ</p></a></li>
  </ul></div></div>
  <div class="c-tab-area__item js-tab-area" data-tab="2"><div class="tab-content"><ul class="p-news-list">
    <li><a href="/business/news/uploads/d441.pdf" target="_blank">
      <div class="news-date"><p>2026.10.06</p><p class="category kojin">個人のお客さま</p></div>
      <p class="news-lead">「こどもNISAプレスタート応援キャンペーン」の実施について</p></a></li>
  </ul></div></div>
  <div class="c-tab-area__item js-tab-area" data-tab="3"><div class="tab-content"><ul class="p-news-list">
    <li><a href="/ebinfo/6112.html">
      <div class="news-date"><p>2026.09.25</p><p class="category houjin">法人のお客さま</p></div>
      <p class="news-lead">道銀ビジネスWEBサービスにおける納付エラー発生のお知らせ</p></a></li>
  </ul></div></div>
  <div class="c-tab-area__item js-tab-area" data-tab="4"><div class="tab-content"><ul class="p-news-list">
    <li><a href="/company/curling/6082.html">
      <div class="news-date"><p>2026.09.09</p><p class="category kojin">個人のお客さま</p></div>
      <p class="news-lead">北海道銀行Lilersは、カナダ遠征へ出発のお知らせ</p></a></li>
  </ul></div></div>
</div>
"""


# 帯広信用金庫 お知らせ一覧の実構造を縮めたもの（2026-10-08 取得）。
# 「⼈」は康熙部首（U+2F08）。サイトの表記のまま残している。
OBISHIN_NEWS_HTML = """
<ul>
  <li class="cat-general"><dl>
    <dt>2026年09月03日 <a href="/obishin/news?category_id=2" class="cat">お知らせ</a></dt>
    <dd><a href="/obishin/pdf/news/2026.09.03-tokachi_jinjibu.pdf">帯広信⽤⾦庫「とかちの⼈事部」の取り組み開始について</a></dd>
  </dl></li>
  <li class="cat-wb"><dl>
    <dt>2026年08月19日 <a href="/obishin/news?category_id=4" class="cat">インターネットバンキング</a></dt>
    <dd><a href="/obishin/pdf/news/2026.08.19-limit.pdf">WEBバンキングサービス 振込限度額等引き下げについて</a></dd>
  </dl></li>
  <li class="cat-gallery"><dl>
    <dt>2026年09月15日 <a href="/obishin/news?category_id=6" class="cat">ふれあいギャラリー</a></dt>
    <dd><a href="/obishin/pdf/news/2026.09.15-fureaigallery.pdf">おびしんふれあいギャラリー「書朋会書展」開催のお知らせ</a></dd>
  </dl></li>
</ul>
<nav><a href="/obishin/net/">インターネットバンキング</a></nav>
"""


class CommonKeywordConfigTest(unittest.TestCase):
    def test_common_exclude_and_priority_are_distributed(self):
        config = minimal_config()
        config["common_exclude_keywords"] = ["書展"]
        config["common_priority_keywords"] = ["利用規定"]
        config["institutions"].append({
            "name": "全件銀行",
            "url": "https://example.com/all/",
            "include_all": True,
            "include_keywords": [],
            "exclude_rules": [],
        })
        collector.validate_config(config)
        collector.normalize_config(config)

        normal, include_all = config["institutions"]
        self.assertEqual(["書展"], normal["common_exclude_keywords"])
        self.assertEqual(["利用規定"], normal["priority_keywords"])
        self.assertEqual([], include_all["common_exclude_keywords"])
        self.assertEqual([], include_all["priority_keywords"])

    def test_rejects_invalid_common_keywords_and_include_all(self):
        for key in ("common_exclude_keywords", "common_priority_keywords"):
            config = minimal_config()
            config[key] = ["書展", ""]
            with self.assertRaisesRegex(ValueError, key):
                collector.validate_config(config)

        config = minimal_config()
        config["institutions"][0]["include_all"] = "yes"
        with self.assertRaisesRegex(ValueError, "真偽値"):
            collector.validate_config(config)

        # 全件通過なのに語が書いてあると「この語で絞っている」と誤読されるため拒否する。
        config = minimal_config()
        config["institutions"][0]["include_all"] = True
        with self.assertRaisesRegex(ValueError, "include_all"):
            collector.validate_config(config)


class FilterPrecedenceTest(unittest.TestCase):
    def setUp(self):
        self.institution = {
            "name": "テスト銀行",
            "url": "https://example.com/news/",
            "include_keywords": ["金利"],
            "exclude_rules": [{"keyword": "規定"}, {"keyword": "詐欺"}],
            "common_exclude_keywords": ["子会社化"],
            "priority_keywords": ["利用規定", "とかちの人事部", "インターネットバンキング"],
        }

    def judge(self, title, date="2026-09-01", **extra):
        passed, excluded = collector.apply_filters(
            [{"date": date, "title": title, "url": "https://example.com/1", **extra}],
            self.institution,
            ["金利"],
        )
        return ("通過", "") if passed else ("除外", excluded[0].get("exclude_keyword", ""))

    def test_priority_beats_institution_exclude_rules(self):
        self.assertEqual(("通過", ""), self.judge("北洋銀行アプリ「利用規定」の一部変更について"))
        self.assertEqual(("除外", "規定"), self.judge("API規定の改定について"))
        # 共通の除外語は優先語より強い（詐欺の注意喚起はインターネットバンキング関連でも除外）。
        self.institution["common_exclude_keywords"].append("詐欺")
        self.assertEqual(("除外", "詐欺"), self.judge("インターネットバンキングを狙った詐欺にご注意ください"))

    def test_common_exclude_beats_priority_and_include(self):
        self.assertEqual(("除外", "子会社化"), self.judge("株式会社の完全子会社化に関する利用規定のお知らせ"))
        self.assertEqual(("除外", "子会社化"), self.judge("子会社化と金利のお知らせ"))

    def test_priority_needs_a_date_to_skip_menu_links(self):
        # メニューの「インターネットバンキング」には日付が無い。通常の判定に回り、通過語が無いので除外。
        self.assertEqual(("除外", ""), self.judge("インターネットバンキング TOP", date=""))

    def test_matching_absorbs_lookalike_characters(self):
        # 帯広信金は「⼈」（康熙部首 U+2F08）で書く。NFKC で「人」とそろえて照合する。
        self.assertEqual(("通過", ""), self.judge("帯広信⽤⾦庫「とかちの⼈事部」の取り組み開始について"))
        # 全角英字も半角とそろう（除外語を半角で書いても全角の記事名に効く）。
        self.institution["exclude_rules"] = [{"keyword": "ATM"}]
        self.assertEqual(("除外", "ATM"), self.judge("ＡＴＭ金利のお知らせ"))

    def test_priority_also_reads_site_categories(self):
        self.assertEqual(("除外", ""), self.judge("WEBバンキングサービス 振込限度額等引き下げについて"))
        self.assertEqual(
            ("通過", ""),
            self.judge("WEBバンキングサービス 振込限度額等引き下げについて", categories=["インターネットバンキング"]),
        )

    def test_include_all_passes_everything_and_keeps_star(self):
        institution = {"name": "全件", "include_all": True, "include_keywords": [], "exclude_rules": []}
        items = [
            {"date": "2026-09-01", "title": "「APIサービスに関する規定」にかかる改正について"},
            {"date": "2026-09-01", "title": "預貯金等の不正な払戻しへの対応について"},
            {"date": "2026-09-01", "title": "貯金金利の引き上げのご案内"},
        ]
        passed, excluded = collector.apply_filters(items, institution, ["金利"])
        self.assertEqual(3, len(passed))
        self.assertEqual([], excluded)
        self.assertTrue(passed[2]["star"])
        self.assertNotIn("star", passed[0])


class RepositoryKeywordRequestTest(unittest.TestCase):
    """本人指示（2026-10-08）の対象語・除外語を、実在の記事名で固定する。"""

    def setUp(self):
        repository_root = Path(__file__).resolve().parents[1]
        self.config = collector.normalize_config(json.loads(
            (repository_root / "config.json").read_text(encoding="utf-8")
        ))
        self.by_name = {item["name"]: item for item in self.config["institutions"]}

    def judge(self, name, title, date="2026-09-01", **extra):
        passed, excluded = collector.apply_filters(
            [{"date": date, "title": title, "url": "https://example.com/1", **extra}],
            self.by_name[name],
            self.config["star_keywords"],
        )
        return "通過" if passed else f"除外（{excluded[0].get('exclude_keyword', '')}）"

    def test_requested_topics_pass_even_where_rules_excluded_them(self):
        cases = [
            ("帯広信用金庫", "帯広信⽤⾦庫「とかちの⼈事部」の取り組み開始について"),
            ("帯広信用金庫", "相続専用定期預金の取扱開始について"),
            ("帯広信用金庫", "帯広信用金庫ディスクロージャー2026を公開しました"),
            ("北海道銀行", "各種預金規定改定のお知らせ"),
            ("北海道銀行", "「どうぎんアプリご利用規定」等の改定について"),
            ("北洋銀行", "北洋銀行アプリ「利用規定」の一部変更について"),
            ("北洋銀行", "北洋ダイレクト（個人向けインターネットバンキング）の終了について"),
            ("十勝信用組合", "2026年版ディスクロージャー誌を掲載いたしました"),
            ("十勝信用組合", "定期性預金規定の改訂について"),
            ("十勝信用組合", "インターネットバンキングの画面デザインの変更について"),
            ("JA木野", "ペイジー口座振替受付サービス利用規定にかかる改正について"),
            # 北海道銀行のニュースリリース（本人指示 2026-10-08 で対象化）
            ("北海道銀行", "「年末ジャンボ宝くじ付き定期預金」の取り扱い開始について"),
            ("北海道銀行", "「資金管理 PayMaster」のサービス提供開始について"),
            ("北海道銀行", "「企業価値担保権」を活用した金融支援の実施について"),
        ]
        for name, title in cases:
            with self.subTest(name=name, title=title):
                self.assertEqual("通過", self.judge(name, title))

    def test_requested_exclusions_and_unrelated_topics_are_excluded(self):
        cases = [
            ("帯広信用金庫", "おびしんふれあいギャラリー「書朋会書展」開催のお知らせ", "書展"),
            ("帯広信用金庫", "おびしんふれあいギャラリー「白華個展」開催のお知らせ", "ふれあいギャラリー"),
            ("北海道銀行", "東京都水道局 水道料金等の収納に関するお知らせ", "東京都水道局"),
            ("北海道銀行", "「エコノミクス甲子園 北海道大会」エントリー締切延長のお知らせ", "エコノミクス甲子園"),
            ("北洋銀行", "キャリアバンク株式会社の完全子会社化に関するお知らせ", "子会社化"),
            ("JAおとふけ", "ホクレンSS公式アプリのリリース及びキャンペーン実施のお知らせについて", "ホクレン"),
            # 詐欺・不審電話の注意喚起は優先語（インターネットバンキング）より強く除外する（本人指示 2026-10-08）。
            ("北洋銀行", "事業者向けインターネットバンキングを狙った詐欺にご注意ください", "詐欺"),
            ("北洋銀行", "インターネットバンキングの登録に関する不審電話にご注意願います！", "不審電話"),
            ("十勝信用組合", "当組合を騙ったフィッシングへの注意喚起について", "フィッシング"),
            ("北海道銀行", "「寄付金」と称する詐欺の取り扱い開始のお知らせ", "詐欺"),
        ]
        for name, title, keyword in cases:
            with self.subTest(name=name, title=title):
                self.assertEqual(f"除外（{keyword}）", self.judge(name, title))

    def test_ja_obihirokawanisi_excludes_nothing(self):
        for title in (
            "「APIサービスに関する規定」にかかる改正について",
            "預貯金等の不正な払戻しへのJAバンクの対応について",
            "組合員・利用者本位の業務運営に関する令和7年度取組状況について",
        ):
            with self.subTest(title=title):
                self.assertEqual("通過", self.judge("JA帯広かわにし", title))

    def test_menu_link_named_internet_banking_stays_excluded(self):
        self.assertEqual("除外（）", self.judge("北海道銀行", "インターネットバンキング TOP", date=""))


class HokkaidobankInfoTest(unittest.TestCase):
    URL = "https://www.hokkaidobank.co.jp/info/"

    def test_reads_the_same_tabs_as_the_top_page_all(self):
        items = collector.scrape_hokkaidobank_info(HOKKAIDOBANK_INFO_HTML, self.URL)

        self.assertEqual(
            [
                ("2026-09-14", "各種預金規定改定のお知らせ", "https://www.hokkaidobank.co.jp/info/uploads/a0bb.pdf"),
                ("2026-09-30", "「エコノミクス甲子園 北海道大会」エントリー締切延長のお知らせ",
                 "https://www.hokkaidobank.co.jp/info/6148.html"),
                ("2026-10-06", "「こどもNISAプレスタート応援キャンペーン」の実施について",
                 "https://www.hokkaidobank.co.jp/business/news/uploads/d441.pdf"),
                ("2026-09-09", "北海道銀行Lilersは、カナダ遠征へ出発のお知らせ",
                 "https://www.hokkaidobank.co.jp/company/curling/6082.html"),
            ],
            [(item["date"], item["title"], item["url"]) for item in items],
        )
        # EB情報はトップページの ALL に含まれない。
        self.assertNotIn("納付エラー", " ".join(item["title"] for item in items))
        config = json.loads(
            (Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8")
        )
        institution = next(item for item in config["institutions"] if item["name"] == "北海道銀行")
        self.assertEqual("hokkaidobank_info", institution["scraper"])
        self.assertEqual(self.URL, institution["url"])

    def test_curling_is_collected_but_excluded_by_url(self):
        config = collector.normalize_config(json.loads(
            (Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8")
        ))
        institution = next(item for item in config["institutions"] if item["name"] == "北海道銀行")
        items = collector.scrape_hokkaidobank_info(HOKKAIDOBANK_INFO_HTML, self.URL)
        passed, excluded = collector.apply_filters(items, institution, config["star_keywords"])

        self.assertEqual(
            ["各種預金規定改定のお知らせ", "「こどもNISAプレスタート応援キャンペーン」の実施について"],
            [item["title"] for item in passed],
        )
        self.assertEqual(
            ["エコノミクス甲子園", "/company/curling/"],
            [item["exclude_keyword"] for item in excluded],
        )

    def test_missing_tabs_are_extraction_failures(self):
        without_news_release = HOKKAIDOBANK_INFO_HTML.replace("ニュース<br>リリース", "おしらせ２")
        with self.assertRaisesRegex(collector.ExtractionError, "ニュースリリース"):
            collector.scrape_hokkaidobank_info(without_news_release, self.URL)
        with self.assertRaisesRegex(collector.ExtractionError, "タブ"):
            collector.scrape_hokkaidobank_info("<html><body>改装中</body></html>", self.URL)

    def test_legacy_titles_from_generic_extraction_are_cleaned(self):
        self.assertEqual(
            "各種預金規定改定のお知らせ",
            collector.clean_report_title("2026.09.14個人のお客さま法人のお客さま各種預金規定改定のお知らせ"),
        )
        self.assertEqual(
            "東京都水道局 水道料金等の収納に関するお知らせ",
            collector.clean_report_title("2026.09.28個人のお客さま東京都水道局 水道料金等の収納に関するお知らせ"),
        )
        # 対象区分が続かない日付（JAめむろ等の形式）は従来どおり触らない。
        self.assertEqual(
            "2026.09.14重要なお知らせ貯金商品一覧「金利表」を更新しました",
            collector.clean_report_title("2026.09.14重要なお知らせ貯金商品一覧「金利表」を更新しました"),
        )


class ObishinNewsTest(unittest.TestCase):
    URL = "https://www.shinkin.co.jp/obishin/news/"

    def test_extracts_articles_with_categories_and_skips_label_links(self):
        items = collector.scrape_obishin_news(OBISHIN_NEWS_HTML, self.URL)

        self.assertEqual(
            [
                ("2026-09-03", "帯広信⽤⾦庫「とかちの⼈事部」の取り組み開始について", ["お知らせ"]),
                ("2026-08-19", "WEBバンキングサービス 振込限度額等引き下げについて", ["インターネットバンキング"]),
                ("2026-09-15", "おびしんふれあいギャラリー「書朋会書展」開催のお知らせ", ["ふれあいギャラリー"]),
            ],
            [(item["date"], item["title"], item["categories"]) for item in items],
        )
        # 分類ラベルやメニューの「インターネットバンキング」は記事として拾わない。
        self.assertNotIn("インターネットバンキング", [item["title"] for item in items])
        self.assertEqual(
            "https://www.shinkin.co.jp/obishin/pdf/news/2026.09.03-tokachi_jinjibu.pdf",
            items[0]["url"],
        )

    def test_repository_filters_use_the_internet_banking_category(self):
        config = collector.normalize_config(json.loads(
            (Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8")
        ))
        institution = next(item for item in config["institutions"] if item["name"] == "帯広信用金庫")
        self.assertEqual("obishin_news", institution["scraper"])
        items = collector.sanitize_items(
            collector.scrape_obishin_news(OBISHIN_NEWS_HTML, self.URL),
            institution["name"],
        )
        passed, excluded = collector.apply_filters(items, institution, config["star_keywords"])

        self.assertEqual(
            [
                "帯広信⽤⾦庫「とかちの⼈事部」の取り組み開始について",
                "WEBバンキングサービス 振込限度額等引き下げについて",
            ],
            [item["title"] for item in passed],
        )
        self.assertEqual(["書展"], [item["exclude_keyword"] for item in excluded])

    def test_missing_structure_is_extraction_failure(self):
        with self.assertRaisesRegex(collector.ExtractionError, "一覧"):
            collector.scrape_obishin_news("<html><body>メンテナンス中</body></html>", self.URL)

    def test_malformed_categories_are_cleared(self):
        cleaned = collector.sanitize_items(
            [{"date": "2026-09-01", "title": "記事", "categories": "インターネットバンキング"}],
            "テスト",
        )
        self.assertEqual([], cleaned[0]["categories"])


class InstitutionListUrlTest(unittest.TestCase):
    def test_list_url_reaches_report_and_institution_index(self):
        result = collector.InstitutionResult(
            "テスト銀行",
            [{"date": "2026-08-01", "title": "金利のお知らせ", "url": "https://example.com/news/1"}],
            [],
            "プログラム",
            url="https://example.com/news/",
        )
        report = collector.format_report([result], "2026-08-01", 30)
        self.assertIn("ニュース一覧: https://example.com/news/", report)

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "output" / "data"
            collector.write_json_viewer_data(
                [result],
                "2026-08-01",
                30,
                data_dir=data_dir,
                institution_order=["テスト銀行"],
                institution_urls={"テスト銀行": "https://example.com/news/"},
            )
            index = json.loads((data_dir / "by-institution.json").read_text(encoding="utf-8"))
            daily = json.loads((data_dir / "2026-08-01.json").read_text(encoding="utf-8"))

        self.assertEqual("https://example.com/news/", index["institutions"][0]["url"])
        # 日付別JSONは当時の記録なので、URLは持たせない（現在の正は by-institution.json）。
        self.assertNotIn("url", daily["institutions"][0])

    def test_collect_institution_keeps_url_on_failure(self):
        institution = minimal_config()["institutions"][0]
        with mock.patch.object(collector, "fetch_page", side_effect=collector.FetchError("test")):
            result, _ = collector.collect_institution(institution, 30, ["金利"])
        self.assertEqual("https://example.com/news/", result.url)

    def test_institution_index_rejects_non_http_url(self):
        document = {"schema_version": 1, "institutions": [
            {"name": "テスト銀行", "url": "javascript:alert(1)", "items": []},
        ]}
        with self.assertRaisesRegex(ValueError, "HTTP"):
            collector.validate_institution_index(document)

    def test_repository_list_urls_cover_every_institution(self):
        config = json.loads(
            (Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8")
        )
        urls = collector.institution_list_urls(config)
        self.assertEqual([item["name"] for item in config["institutions"]], list(urls))
        published = json.loads(
            (Path(__file__).resolve().parents[1] / "output" / "data" / "by-institution.json").read_text(
                encoding="utf-8"
            )
        )
        # 公開中の集約にも、現在の設定と同じURLが載っている（ヴューアーの機関名リンクの元）。
        for institution in published["institutions"]:
            with self.subTest(name=institution["name"]):
                self.assertEqual(urls.get(institution["name"]), institution.get("url"))


if __name__ == "__main__":
    unittest.main()
