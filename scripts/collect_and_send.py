"""
scripts/collect_and_send.py — 金融機関新着情報収集・レポート送信
GitHub Actions で週次実行される

収集方式:
  - 標準サイト: BeautifulSoup（プログラム解析）
  - 複雑サイト: Claude API（config.json で use_claude: true を指定）
"""
import os
import json
import re
import calendar
import shutil
import sys
import tempfile
import warnings
from dataclasses import dataclass
try:
    # XXE・billion-laughs 対策。本番(GitHub Actions)では defusedxml をインストール済み
    from defusedxml import ElementTree as ET
except ImportError:  # ローカルにdefusedxml未導入の場合のフォールバック
    import xml.etree.ElementTree as ET
    warnings.warn(
        "defusedxml が未導入のため標準XML解析へ縮退します。本番では必ずdefusedxmlを導入してください。",
        RuntimeWarning,
        stacklevel=2,
    )
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import anthropic

try:
    # requests の apparent_encoding と同じ判定実装（requestsの必須依存・lockでハッシュ固定済み）
    import charset_normalizer as _charset_detector
except ImportError:  # chardet 構成の requests 向けフォールバック
    import chardet as _charset_detector

try:
    from .emailer import send_resend_email
except ImportError:
    from emailer import send_resend_email

# 実行環境(GitHub Actions)はUTCで動くため、日付の基準は日本時間(JST)に固定する。
# cron は日曜20:00 UTC = 月曜05:00 JST 実行なので、JST化しないとレポートが日曜日付になる。
JST = timezone(timedelta(hours=9))

# 機関別ヴューアー用の全期間集約は、この月数までに制限してサイズを頭打ちにする。
# 日付別レポートは対象外のため、過去分も従来どおりすべて閲覧できる。
INSTITUTION_WINDOW_MONTHS = 24
DEFAULT_STAR_KEYWORDS = ("金利", "キャンペーン")

# 除外ルールの照合先。既定は記事名。記事名では区別できない区分（JAおとふけの
# ホクレン給油所ニュースなど）だけ、URLを見て落とすために "url" を使う。
EXCLUDE_TARGET_TITLE = "title"
EXCLUDE_TARGET_URL = "url"
EXCLUDE_TARGETS = (EXCLUDE_TARGET_TITLE, EXCLUDE_TARGET_URL)
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_XML_BYTES = 5 * 1024 * 1024
REQUEST_TIMEOUT = (5, 30)  # 接続待ち・読取無通信の上限（秒）
VIEWER_READY_MARKER_NAME = "viewer-json-ready.txt"
FAILED_STATUSES = {"fetch_failed", "parse_failed", "extract_failed"}


class FetchError(RuntimeError):
    """外部ページを安全に取得できなかった。"""


class ExtractionError(RuntimeError):
    """取得ページから記事一覧を抽出できなかった。"""


@dataclass
class InstitutionResult:
    """1金融機関分の収集結果と、正常0件を区別できる状態。"""

    name: str
    passed: list
    excluded: list
    method: str
    status: str = "ok"
    error: str = ""


def now_jst():
    """JST基準の現在時刻（tz-aware）"""
    return datetime.now(JST)


def viewer_ready_marker_path(data_dir="output/data"):
    """JSON三点セットの成功マーカーのパス。unlink側と作成側で同じ導出を使う。"""
    return Path(data_dir).parent / VIEWER_READY_MARKER_NAME


def validate_config(config):
    """config.json の必須項目・型・重複・収集方式を検証する。"""
    if not isinstance(config, dict):
        raise ValueError("config.json のルートはオブジェクトにしてください。")

    lookback_days = config.get("lookback_days", 30)
    if isinstance(lookback_days, bool) or not isinstance(lookback_days, int) or lookback_days < 0:
        raise ValueError("lookback_days は0以上の整数にしてください。")

    star_keywords = config.get("star_keywords", list(DEFAULT_STAR_KEYWORDS))
    if not isinstance(star_keywords, list) or not all(
        isinstance(value, str) and value for value in star_keywords
    ):
        raise ValueError("star_keywords は空でない文字列の配列にしてください。")

    institutions = config.get("institutions")
    if not isinstance(institutions, list) or not institutions:
        raise ValueError("institutions は1件以上の配列にしてください。")

    seen_names = set()
    for index, institution in enumerate(institutions, start=1):
        if not isinstance(institution, dict):
            raise ValueError(f"institutions[{index}] はオブジェクトにしてください。")
        name = institution.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"institutions[{index}].name は空でない文字列にしてください。")
        if name in seen_names:
            raise ValueError(f"金融機関名が重複しています: {name}")
        seen_names.add(name)

        url = institution.get("url")
        parsed_url = urlparse(url) if isinstance(url, str) else None
        if not parsed_url or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"{name} の url はHTTP(S)の絶対URLにしてください。")

        scraper = institution.get("scraper", "programmatic")
        if not isinstance(scraper, str) or scraper not in VALID_SCRAPERS:
            raise ValueError(f"{name} の scraper が未対応です: {scraper}")
        if not isinstance(institution.get("use_claude", False), bool):
            raise ValueError(f"{name} の use_claude は真偽値にしてください。")

        include_keywords = institution.get("include_keywords", [])
        if not isinstance(include_keywords, list) or not all(
            isinstance(value, str) and value for value in include_keywords
        ):
            raise ValueError(f"{name} の include_keywords は文字列の配列にしてください。")

        exclude_rules = institution.get("exclude_rules", [])
        if not isinstance(exclude_rules, list):
            raise ValueError(f"{name} の exclude_rules は配列にしてください。")
        for rule_index, rule in enumerate(exclude_rules, start=1):
            if not isinstance(rule, dict) or not isinstance(rule.get("keyword"), str) or not rule["keyword"]:
                raise ValueError(f"{name} の exclude_rules[{rule_index}].keyword が不正です。")
            unless = rule.get("unless", [])
            if not isinstance(unless, list) or not all(
                isinstance(value, str) and value for value in unless
            ):
                raise ValueError(f"{name} の exclude_rules[{rule_index}].unless は文字列の配列にしてください。")
            if rule.get("target", EXCLUDE_TARGET_TITLE) not in EXCLUDE_TARGETS:
                raise ValueError(
                    f"{name} の exclude_rules[{rule_index}].target は "
                    f"{' / '.join(sorted(EXCLUDE_TARGETS))} のいずれかにしてください。"
                )


def load_config():
    with open("config.json", encoding="utf-8") as f:
        config = json.load(f)
    validate_config(config)
    return config


def read_limited_response(response, *, max_bytes, allowed_content_types, url):
    """外部応答を上限付きで読み、想定外の種類・サイズを拒否する。"""
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type and content_type not in allowed_content_types:
        raise FetchError(f"想定外のContent-Typeです ({content_type}): {url}")

    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            parsed_length = int(content_length)
            if parsed_length < 0:
                raise FetchError(f"Content-Length が不正です: {url}")
            if parsed_length > max_bytes:
                raise FetchError(f"応答サイズが上限を超えています: {url}")
        except ValueError:
            raise FetchError(f"Content-Length が不正です: {url}") from None

    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise FetchError(f"応答サイズが上限を超えています: {url}")
        chunks.append(chunk)
    return b"".join(chunks)


def decode_html(content, encoding=None):
    """取得したbytesを文字列へデコードする。
    優先順は config の encoding 指定 → 実体からの自動判定 → utf-8。
    HTTPヘッダ宣言より実体判定を優先するのは、従来の apparent_encoding 既定
    （北洋の cp932 文字化け回避の経緯）を維持するため。"""
    if not encoding:
        detected = _charset_detector.detect(content) or {}
        encoding = detected.get("encoding") or "utf-8"
    try:
        return str(content, encoding, errors="replace")
    except (LookupError, TypeError):
        return str(content, errors="replace")


def fetch_page(url, encoding=None):
    """HTMLページを取得してデコードする"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        with requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, stream=True) as resp:
            content = read_limited_response(
                resp,
                max_bytes=MAX_HTML_BYTES,
                allowed_content_types={"text/html", "application/xhtml+xml"},
                url=url,
            )
        return decode_html(content, encoding)
    except Exception as e:
        print(f"  取得失敗 ({url}): {e}")
        if isinstance(e, FetchError):
            raise
        raise FetchError(f"ページ取得に失敗しました: {url}") from e


# 実在しない日付候補（電話番号の誤認など）は破棄する。同じ値を何度も出力しないための記録。
_REJECTED_DATE_CANDIDATES = set()


def _log_rejected_date(candidate, source):
    """破棄した日付候補を、同一値につき1回だけログへ残す（黙殺防止）。"""
    if candidate not in _REJECTED_DATE_CANDIDATES:
        _REJECTED_DATE_CANDIDATES.add(candidate)
        print(f"  日付候補を破棄（実在しない日付）: {candidate}（{source}）")


def extract_date_from_text(text):
    """テキストから日付（YYYY-MM-DD）を抽出。実在する日付だけを返す。
    電話番号「0155-24-1234」等がパターンに一致しても、実在日でなければ破棄して
    次の候補を探す。不正日付を通すと下流のJSON検証（validate_optional_date）が
    レポート全体を失敗させるため、ここで止める。"""
    patterns = [
        r'(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})',
        r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日',
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            y, mo, d = m.group(1), m.group(2), m.group(3)
            candidate = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            if parse_ymd(candidate):
                return candidate
            _log_rejected_date(candidate, "テキスト")
    return ""


def date_n_months_ago(n, base=None):
    """base（未指定ならJST今日）から n ヶ月前の同日を返す。
    月末日はその月の最終日にクランプ（例: 5/31 の3ヶ月前 → 2/28）。"""
    d = (base or now_jst().date())
    month = d.month - n
    year = d.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def parse_ymd(value):
    """YYYY-MM-DD形式の日付をdateへ変換し、不正値はNoneを返す。"""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def validate_optional_date(value, field_name):
    """空欄またはYYYY-MM-DDの実在日だけを許可する。"""
    if value in (None, ""):
        return
    if not isinstance(value, str) or parse_ymd(value) is None:
        raise ValueError(f"{field_name} は YYYY-MM-DD 形式にしてください: {value!r}")


def validate_required_date(value, field_name):
    """YYYY-MM-DDの実在日を必須とする。"""
    if not isinstance(value, str) or parse_ymd(value) is None:
        raise ValueError(f"{field_name} は必須の YYYY-MM-DD 形式です: {value!r}")


def validate_optional_http_url(value, field_name):
    """空欄またはHTTP(S)の絶対URLだけを許可する。"""
    if value in (None, ""):
        return
    if not isinstance(value, str):
        raise ValueError(f"{field_name} は文字列にしてください。")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} はHTTP(S)の絶対URLにしてください: {value!r}")


def validate_report_document(report, source="レポートJSON"):
    """日付別ニュースJSONの後方互換を保った最小スキーマ検証。"""
    if not isinstance(report, dict):
        raise ValueError(f"{source} のルートはオブジェクトにしてください。")
    if report.get("schema_version", 1) != 1:
        raise ValueError(f"{source} の schema_version は未対応です。")
    validate_required_date(report.get("date"), f"{source}.date")
    lookback_days = report.get("lookback_days")
    if isinstance(lookback_days, bool) or not isinstance(lookback_days, int) or lookback_days < 0:
        raise ValueError(f"{source}.lookback_days は0以上の整数にしてください。")

    institutions = report.get("institutions")
    if not isinstance(institutions, list):
        raise ValueError(f"{source}.institutions は配列にしてください。")
    seen_names = set()
    for institution_index, institution in enumerate(institutions, start=1):
        prefix = f"{source}.institutions[{institution_index}]"
        if not isinstance(institution, dict):
            raise ValueError(f"{prefix} はオブジェクトにしてください。")
        name = institution.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{prefix}.name は空でない文字列にしてください。")
        if name in seen_names:
            raise ValueError(f"{source} の金融機関名が重複しています: {name}")
        seen_names.add(name)
        if not isinstance(institution.get("method", ""), str):
            raise ValueError(f"{prefix}.method は文字列にしてください。")
        status = institution.get("status", "ok")
        if status not in {"ok", "empty", *FAILED_STATUSES}:
            raise ValueError(f"{prefix}.status が不正です: {status!r}")
        error = institution.get("error", "")
        if not isinstance(error, str):
            raise ValueError(f"{prefix}.error は文字列にしてください。")
        if status in FAILED_STATUSES and not error:
            raise ValueError(f"{prefix}.error は失敗時に必須です。")

        for list_name in ("passed", "excluded"):
            items = institution.get(list_name)
            if not isinstance(items, list):
                raise ValueError(f"{prefix}.{list_name} は配列にしてください。")
            for item_index, item in enumerate(items, start=1):
                item_prefix = f"{prefix}.{list_name}[{item_index}]"
                if not isinstance(item, dict):
                    raise ValueError(f"{item_prefix} はオブジェクトにしてください。")
                if not isinstance(item.get("title"), str) or not item["title"]:
                    raise ValueError(f"{item_prefix}.title は空でない文字列にしてください。")
                validate_optional_date(item.get("date"), f"{item_prefix}.date")
                if list_name == "passed":
                    validate_optional_http_url(item.get("url"), f"{item_prefix}.url")
                    for flag_name in ("star", "fallback", "date_inferred"):
                        if flag_name in item and not isinstance(item[flag_name], bool):
                            raise ValueError(f"{item_prefix}.{flag_name} は真偽値にしてください。")
                elif not isinstance(item.get("exclude_keyword", ""), str):
                    raise ValueError(f"{item_prefix}.exclude_keyword は文字列にしてください。")


def validate_manifest(manifest):
    """日付一覧JSONを検証する。"""
    if not isinstance(manifest, dict) or manifest.get("schema_version", 1) != 1:
        raise ValueError("index.json の形式またはschema_versionが不正です。")
    reports = manifest.get("reports")
    if not isinstance(reports, list) or len(reports) != len(set(reports)):
        raise ValueError("index.json の reports は重複のない配列にしてください。")
    for index, report_date in enumerate(reports, start=1):
        validate_required_date(report_date, f"index.json.reports[{index}]")
    if reports != sorted(reports, reverse=True):
        raise ValueError("index.json の reports は新しい日付順にしてください。")


def validate_institution_index(document):
    """機関別集約JSONの最低限の構造を検証する。"""
    if not isinstance(document, dict) or document.get("schema_version", 1) != 1:
        raise ValueError("by-institution.json の形式またはschema_versionが不正です。")
    institutions = document.get("institutions")
    if not isinstance(institutions, list):
        raise ValueError("by-institution.json の institutions は配列にしてください。")
    seen_names = set()
    for institution_index, institution in enumerate(institutions, start=1):
        prefix = f"by-institution.json.institutions[{institution_index}]"
        if not isinstance(institution, dict):
            raise ValueError(f"{prefix} はオブジェクトにしてください。")
        name = institution.get("name")
        if not isinstance(name, str) or not name or name in seen_names:
            raise ValueError(f"{prefix}.name が空または重複しています。")
        seen_names.add(name)
        items = institution.get("items")
        if not isinstance(items, list):
            raise ValueError(f"{prefix}.items は配列にしてください。")
        for item_index, item in enumerate(items, start=1):
            item_prefix = f"{prefix}.items[{item_index}]"
            if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"]:
                raise ValueError(f"{item_prefix} が不正です。")
            validate_optional_date(item.get("date"), f"{item_prefix}.date")
            validate_optional_http_url(item.get("url"), f"{item_prefix}.url")
            reports = item.get("reports")
            if (
                not isinstance(reports, list)
                or not reports
                or len(reports) != len(set(reports))
            ):
                raise ValueError(f"{item_prefix}.reports は重複のない1件以上の配列にしてください。")
            for report_index, report_date in enumerate(reports, start=1):
                validate_required_date(report_date, f"{item_prefix}.reports[{report_index}]")
            if reports != sorted(reports, reverse=True):
                raise ValueError(f"{item_prefix}.reports は新しい日付順にしてください。")


def is_recent_excluded(item, today):
    """除外項目が配信日から3ヶ月以内かを判定する。不明・不正な日付は残す。"""
    today_date = parse_ymd(today) or now_jst().date()
    item_date = parse_ymd(item.get("date", ""))
    return item_date is None or item_date >= date_n_months_ago(3, today_date)


def extract_date_from_url(url):
    """URL 内の YYYYMMDD パターンから日付を抽出（例: /detail/20260528_xxx.html）。
    記事IDなどの8桁数字を日付と誤認しないよう、実在日だけを返す。"""
    m = re.search(r'/(\d{8})[_/]', url)
    if m:
        d = m.group(1)
        candidate = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        if parse_ymd(candidate):
            return candidate
        _log_rejected_date(candidate, url)
    return ""


# 「今月のローン金利」のように、日付がどこにも無い常設ページを表す相対表現。
# これらは常に当月時点の情報なので、無日付のまま素通りさせず現在月の日付を補う。
_CURRENT_MONTH_WORDS = ["今月", "当月", "本月"]


def infer_date_from_relative_text(title):
    """タイトルに「今月」等の相対表現があれば、現在月の初日(YYYY-MM-01)を補う。
    十勝信組「今月のローン金利」など、ページ本体にも日付が無い常設項目が
    日付空欄のまま lookback を素通りするのを防ぎ、当月の項目として正しく扱う。"""
    if any(w in title for w in _CURRENT_MONTH_WORDS):
        return now_jst().replace(day=1).strftime("%Y-%m-%d")
    return ""


# ナビゲーションや不要リンクのキーワード
_SKIP_WORDS = [
    "ホーム", "トップ", "サイトマップ", "お問い合わせ", "アクセス", "採用",
    "English", "プライバシー", "個人情報", "免責事項", "著作権", "ログイン",
    "会員登録", "資料請求", "店舗", "ATM", "もっと見る", "一覧へ", "詳しくはこちら",
]


def scrape_news_programmatic(html, base_url):
    """BeautifulSoupでニュース一覧を汎用的に抽出"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen_articles = set()

    for a in soup.find_all("a", href=True):
        title = a.get_text(strip=True)

        if not (8 <= len(title) <= 150):
            continue
        if any(w in title for w in _SKIP_WORDS):
            continue

        href = a["href"]
        if not href or href.startswith(("#", "javascript", "mailto", "tel")):
            continue

        url = urljoin(base_url, href)
        article_key = (title, url)
        if article_key in seen_articles:
            continue
        seen_articles.add(article_key)

        date = ""
        for candidate in [a, a.parent, a.parent.parent if a.parent else None]:
            if candidate:
                date = extract_date_from_text(candidate.get_text())
                if date:
                    break
        if not date:
            date = extract_date_from_url(url)
        date_inferred = False
        if not date:
            date = infer_date_from_relative_text(title)
            date_inferred = bool(date)

        items.append({"date": date, "title": title, "url": url, "date_inferred": date_inferred})

    return items[:60]


def scrape_ja_obihirokawanisi(html, base_url):
    """JA帯広かわにしの金融ニュース一覧から記事名・日付・URLを個別に抽出する。"""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(".content_section.news_list li.news_content")
    if not rows:
        raise ExtractionError("JA帯広かわにしのニュース一覧が見つかりませんでした。")

    items = []
    for row in rows:
        link = row.find("a", href=True)
        title_element = row.select_one(".news_content__text")
        date_element = row.select_one(".news_content__date")
        if not link or not title_element:
            continue

        title = title_element.get_text(" ", strip=True)
        href = link.get("href", "").strip()
        if not title or not href or href.startswith(("#", "javascript", "mailto", "tel")):
            continue

        date_text = date_element.get_text(" ", strip=True) if date_element else ""
        date = extract_date_from_text(date_text) or extract_date_from_url(href)
        items.append({
            "date": date,
            "title": title,
            "url": urljoin(base_url, href),
            "date_inferred": False,
        })

    if not items:
        raise ExtractionError("JA帯広かわにしのニュース記事を抽出できませんでした。")
    if not any(item["date"] for item in items):
        raise ExtractionError("JA帯広かわにしのニュース記事から日付を抽出できませんでした。")
    return items


def scrape_ja_kino(html, base_url):
    """JA木野の新着情報一覧から記事名・日付・URLを個別に抽出する。
    記事1件が1リンクに日付・カテゴリ・記事名を並べる構造で、汎用抽出では
    「2026.08.10JAバンク/貯める貯金金利の引き上げについて」と連結されるため専用化する。"""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(".l-news-list ul.news > li")
    if not rows:
        raise ExtractionError("JA木野のニュース一覧が見つかりませんでした。")

    items = []
    for row in rows:
        link = row.find("a", href=True)
        title_element = row.select_one(".title")
        date_element = row.select_one(".meta .date")
        if not link or not title_element:
            continue

        title = title_element.get_text(" ", strip=True)
        href = link.get("href", "").strip()
        if not title or not href or href.startswith(("#", "javascript", "mailto", "tel")):
            continue

        # URL側の日付（jakino_info_20260824.pdf）はPDF自体の日付で、掲載日と
        # ずれることがあるため補完に使わない。掲載日はDOMの .date だけを正とする。
        date_text = date_element.get_text(" ", strip=True) if date_element else ""
        items.append({
            "date": extract_date_from_text(date_text),
            "title": title,
            "url": urljoin(base_url, href),
            "date_inferred": False,
        })

    if not items:
        raise ExtractionError("JA木野のニュース記事を抽出できませんでした。")
    if not any(item["date"] for item in items):
        raise ExtractionError("JA木野のニュース記事から日付を抽出できませんでした。")
    return items


def scrape_hokuyo_xml(base_url):
    """北洋銀行: 新着情報は JS で年別XMLフィード（announcement/{year}.xml）から描画される。
    静的HTMLには記事タイトルが無いため、XMLを直接取得して解析する。
    年またぎの lookback に備えて今年と前年の2ファイルを取得する。"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    items = []
    seen = set()  # 同一記事が複数カテゴリで重複登録されるため除去
    this_year = now_jst().year
    for year in (this_year, this_year - 1):
        xml_url = urljoin(base_url, f"{year}.xml")
        try:
            with requests.get(
                xml_url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                stream=True,
            ) as resp:
                content = read_limited_response(
                    resp,
                    max_bytes=MAX_XML_BYTES,
                    allowed_content_types={
                        "application/xml",
                        "application/rss+xml",
                        "text/xml",
                    },
                    url=xml_url,
                )
            root = ET.fromstring(content)
        except Exception as e:
            print(f"  XML取得失敗 ({xml_url}): {e}")
            if isinstance(e, FetchError):
                raise
            raise FetchError(f"北洋銀行のXMLを取得・解析できませんでした: {xml_url}") from e
        for art in root.findall("article"):
            title_el = art.find("title")
            if title_el is None:
                continue
            title = (title_el.text or "").strip()
            title = re.sub(r"\s*\(PDF[^)]*\)\s*$", "", title)  # 「 (PDF 2.4MB)」を除去
            if not title:
                continue
            href = title_el.get("href", "")
            url = urljoin(base_url, href) if href else ""
            date = extract_date_from_text(art.findtext("viewdate", ""))
            if not date:
                date = extract_date_from_url(url)
            key = (title, date)  # 同一告知が複数カテゴリ・別IDで登録されるため
            if key in seen:
                continue
            seen.add(key)
            items.append({"date": date, "title": title, "url": url})
    if not items:
        raise ExtractionError("北洋銀行のXMLからニュース記事を抽出できませんでした。")
    return items


def _scrape_programmatic_institution(institution, url):
    html = fetch_page(url, encoding=institution.get("encoding"))
    return scrape_news_programmatic(html, url)


def _scrape_hokuyo_institution(institution, url):
    del institution
    return scrape_hokuyo_xml(url)


def _scrape_ja_obihirokawanisi_institution(institution, url):
    html = fetch_page(url, encoding=institution.get("encoding"))
    return scrape_ja_obihirokawanisi(html, url)


def _scrape_ja_kino_institution(institution, url):
    html = fetch_page(url, encoding=institution.get("encoding"))
    return scrape_ja_kino(html, url)


# スクレイパーの登録簿。設定検証（VALID_SCRAPERS）とディスパッチが自動で揃うよう、
# 新方式を追加する場合はこの辞書へ1エントリ足すだけにする。
SCRAPERS = {
    "programmatic": _scrape_programmatic_institution,
    "hokuyo_xml": _scrape_hokuyo_institution,
    "ja_obihirokawanisi": _scrape_ja_obihirokawanisi_institution,
    "ja_kino": _scrape_ja_kino_institution,
}
VALID_SCRAPERS = set(SCRAPERS)


def sanitize_items(items, source_name):
    """収集直後の外部由来アイテムを検証し、不正なものは項目単位で落とす入口防御。
    1件の不正が下流のJSON検証（validate_report_document）でレポート全体を
    失敗させないため、URL・日付は不正部分だけ空欄化して項目自体は残す。
    凍結中のClaude経路には適用しない（再有効化時の一括堅牢化でここへ合流させる）。"""
    cleaned = []
    dropped = 0
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            dropped += 1
            continue
        entry = dict(item)
        entry["title"] = title.strip()

        item_url = entry.get("url", "")
        if item_url:
            parsed = urlparse(item_url) if isinstance(item_url, str) else None
            if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                print(f"  不正なURLを空欄化: {item_url!r}（{source_name}）")
                entry["url"] = ""

        date = entry.get("date", "")
        if date and (not isinstance(date, str) or parse_ymd(date) is None):
            print(f"  不正な日付を空欄化: {date!r}（{source_name}）")
            entry["date"] = ""

        cleaned.append(entry)
    if dropped:
        print(f"  不正な項目を除外: {dropped}件（{source_name}）")
    return cleaned


def extract_news_with_claude(client, name, url, html, lookback_days):
    """Claude APIでHTMLからニュース一覧を抽出（複雑サイト用）"""
    cutoff_note = f"過去{lookback_days}日以内の記事のみ抽出してください。" if lookback_days else "すべての記事を抽出してください。"
    today = now_jst().strftime("%Y-%m-%d")
    html_truncated = html[:15000]

    prompt = f"""以下は「{name}」（{url}）の新着情報ページのHTMLです。
今日の日付: {today}
{cutoff_note}

ニュース記事の一覧を抽出し、以下のJSON形式のみ返してください（説明文不要）。
記事が見つからない場合は空配列 [] を返してください。
URLは絶対URLに変換してください。

[
  {{"date": "YYYY-MM-DD", "title": "記事タイトル", "url": "https://..."}}
]

HTML:
{html_truncated}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return []
    except Exception as e:
        print(f"  Claude抽出失敗: {e}")
        raise ExtractionError("Claude APIで記事一覧を抽出できませんでした。") from e


def filter_by_lookback(items, lookback_days):
    """lookback_days日以内の記事のみ通過（0=全件）。
    日付が空・不正な項目はfail-openで残す（既存仕様・テストで固定）。"""
    if not lookback_days:
        return items
    cutoff = (now_jst() - timedelta(days=lookback_days)).date()
    return [
        item
        for item in items
        if (item_date := parse_ymd(item.get("date", ""))) is None or item_date >= cutoff
    ]


def get_fallback_item(passed_all, lookback_days):
    """期間内に通過記事がない場合、期間外で最も新しい通過記事を返す"""
    if not lookback_days:
        return None
    cutoff = (now_jst() - timedelta(days=lookback_days)).date()
    older = [
        item
        for item in passed_all
        if (item_date := parse_ymd(item.get("date", ""))) is not None and item_date < cutoff
    ]
    if not older:
        return None
    best = max(older, key=lambda x: x["date"])
    best = dict(best)
    best["fallback"] = True
    return best


def apply_filters(items, institution, star_keywords=None):
    """include_keywords・exclude_rules でフィルタ適用"""
    include_kw = institution.get("include_keywords", [])
    exclude_rules = institution.get("exclude_rules", [])
    if star_keywords is None:
        star_keywords = DEFAULT_STAR_KEYWORDS
    passed = []
    excluded = []

    for item in items:
        title = item.get("title", "")

        excluded_by = None
        for rule in exclude_rules:
            kw = rule["keyword"]
            # 照合先はルール単位。unless も同じ文字列に対して見て、1ルール内で
            # 判断材料が2か所に割れないようにする。
            haystack = (
                item.get("url", "")
                if rule.get("target") == EXCLUDE_TARGET_URL
                else title
            )
            if kw in haystack:
                unless = rule.get("unless", [])
                if unless and any(u in haystack for u in unless):
                    continue
                excluded_by = kw
                break

        if excluded_by:
            item["exclude_keyword"] = excluded_by
            excluded.append(item)
            continue

        if not include_kw or any(kw in title for kw in include_kw):
            if any(k in title for k in star_keywords):
                item["star"] = True
            passed.append(item)
        else:
            excluded.append(item)

    return passed, excluded


def format_report(results, today, lookback_days):
    """Markdown形式のレポートを生成"""
    lines = [f"# 金融機関新着情報レポート — {today}", ""]
    total_passed = 0
    total_excluded = 0
    failed_count = 0

    # 通過セクション（全機関）
    for result in results:
        lines.append(f"## {result.name}　*（収集: {result.method}）*")
        lines.append("")
        if result.status in FAILED_STATUSES:
            failed_count += 1
            lines.append(f"> ⚠️ 収集失敗: {result.error or '原因を確認してください。'}")
            lines.append("")
        lines.append(f"### ✅ 通過（{len(result.passed)}件）")
        if result.passed:
            lines.append("| 日付 | タイトル | URL |")
            lines.append("|---|---|---|")
            for item in result.passed:
                star = f" {ANNOTATION_BY_FLAG['star']}" if item.get("star") else ""
                note = f" {ANNOTATION_BY_FLAG['fallback']}" if item.get("fallback") else ""
                if item.get("date_inferred"):
                    note += f" {ANNOTATION_BY_FLAG['date_inferred']}"
                lines.append(f"| {item.get('date','')} | {item['title']}{star}{note} | {item.get('url','')} |")
        else:
            lines.append("（該当なし）")
        lines.append("")
        lines.append("---")
        lines.append("")
        total_passed += len(result.passed)

    # 除外セクション（全機関まとめて末尾）
    lines.append("# 除外一覧")
    lines.append("")
    for result in results:
        excluded_recent = [it for it in result.excluded if is_recent_excluded(it, today)]
        total_excluded += len(excluded_recent)
        lines.append(f"## {result.name}　❌ 除外（{len(excluded_recent)}件）")
        if excluded_recent:
            lines.append("| 日付 | タイトル | 除外キーワード |")
            lines.append("|---|---|---|")
            for item in excluded_recent:
                lines.append(f"| {item.get('date','')} | {item['title']} | {item.get('exclude_keyword','')} |")
        lines.append("")

    period = f"過去{lookback_days}日" if lookback_days else "全件"
    lines.append(f"*収集日時: {today} / 対象期間: {period}*")
    lines.append(f"*合計: 通過 {total_passed}件 / 除外 {total_excluded}件 / 収集失敗 {failed_count}機関*")
    return "\n".join(lines)


# Markdown表示用の注記。JSONでは文字列ではなく真偽値のフラグとして保持する。
# 付与（format_report）と除去（clean_report_title）の唯一の対応表。
# 値を1文字でも変えると既存Markdown・by-institution.json の重複除去キーと
# ズレるため、参照の追加はよいが値は変更しないこと。
ANNOTATION_BY_FLAG = {
    "star": "⭐金利・キャンペーン",
    "fallback": "※1ヵ月超・最新",
    "date_inferred": "※当月分（日付はページに記載なし・当月初で補完）",
}
_TITLE_ANNOTATIONS = list(ANNOTATION_BY_FLAG.values())


def clean_report_title(title):
    """タイトルからMarkdown表示用の注記を取り除く。"""
    cleaned = title or ""
    for annotation in _TITLE_ANNOTATIONS:
        cleaned = cleaned.replace(annotation, "")
    # 初期の手動レポートでは「⭐金利」という短い注記も使っていた。
    cleaned = re.sub(r"⭐(?:金利(?:・キャンペーン)?|キャンペーン)", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def build_report_data(results, today, lookback_days):
    """収集結果から、ヴューアーで使う1レポート分のデータを組み立てる。"""
    institutions = []
    for result in results:
        passed_data = []
        for item in result.passed:
            passed_data.append({
                "date": item.get("date", ""),
                "title": clean_report_title(item.get("title", "")),
                "url": item.get("url", ""),
                "star": bool(item.get("star")),
                "fallback": bool(item.get("fallback")),
                "date_inferred": bool(item.get("date_inferred")),
            })

        excluded_data = []
        for item in result.excluded:
            if not is_recent_excluded(item, today):
                continue
            excluded_data.append({
                "date": item.get("date", ""),
                "title": clean_report_title(item.get("title", "")),
                "exclude_keyword": item.get("exclude_keyword", ""),
            })

        institutions.append({
            "name": result.name,
            "method": result.method,
            "status": result.status,
            "error": result.error,
            "passed": passed_data,
            "excluded": excluded_data,
        })

    return {
        "schema_version": 1,
        "date": today,
        "lookback_days": lookback_days,
        "institutions": institutions,
    }


def order_institutions(institutions, institution_order):
    """金融機関を設定順に並べ、設定にない機関は元の順で末尾に残す。"""
    order_by_name = {
        name: index
        for index, name in enumerate(institution_order or [])
    }
    fallback_index = len(order_by_name)
    return [
        institution
        for _, institution in sorted(
            enumerate(institutions),
            key=lambda pair: (
                order_by_name.get(pair[1].get("name", ""), fallback_index),
                pair[0],
            ),
        )
    ]


def list_report_dates(data_dir):
    """日付別JSONのファイル名から、新しい順の日付一覧を作る。"""
    data_path = Path(data_dir)
    dates = [
        path.stem
        for path in data_path.glob("*.json")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem)
    ]
    return sorted(dates, reverse=True)


def build_institution_index(
    data_dir,
    window_months=INSTITUTION_WINDOW_MONTHS,
    today=None,
    institution_order=None,
):
    """全日付のJSONを読み、通過記事を機関別に重複なくまとめる。
    window_months を指定すると、その月数より古いレポートを集約から除外する。"""
    data_path = Path(data_dir)
    institutions = {}

    cutoff = ""
    if window_months:
        base = today or now_jst().strftime("%Y-%m-%d")
        try:
            base_date = datetime.strptime(base, "%Y-%m-%d").date()
        except ValueError:
            base_date = now_jst().date()
        cutoff = date_n_months_ago(window_months, base_date).strftime("%Y-%m-%d")

    for report_date in list_report_dates(data_path):
        if cutoff and report_date < cutoff:
            continue
        report_path = data_path / f"{report_date}.json"
        with report_path.open(encoding="utf-8") as file:
            report = json.load(file)
        validate_report_document(report, str(report_path))

        for institution in report.get("institutions", []):
            name = institution.get("name", "")
            if not name:
                continue
            items_by_key = institutions.setdefault(name, {})
            for item in institution.get("passed", []):
                title = clean_report_title(item.get("title", ""))
                url = item.get("url", "")
                key = (title, url)
                if key not in items_by_key:
                    items_by_key[key] = {
                        "date": item.get("date", ""),
                        "title": title,
                        "url": url,
                        "star": bool(item.get("star")),
                        "fallback": bool(item.get("fallback")),
                        "date_inferred": bool(item.get("date_inferred")),
                        "reports": [],
                    }
                aggregate = items_by_key[key]
                item_date = item.get("date", "")
                if item_date and item_date > aggregate["date"]:
                    aggregate["date"] = item_date
                aggregate["star"] = aggregate["star"] or bool(item.get("star"))
                aggregate["fallback"] = aggregate["fallback"] or bool(item.get("fallback"))
                aggregate["date_inferred"] = aggregate["date_inferred"] or bool(item.get("date_inferred"))
                if report_date not in aggregate["reports"]:
                    aggregate["reports"].append(report_date)

    result = []
    for name, items_by_key in institutions.items():
        items = list(items_by_key.values())
        for item in items:
            item["reports"].sort(reverse=True)
        items.sort(key=lambda item: (bool(item["date"]), item["date"]), reverse=True)
        result.append({"name": name, "items": items})

    return {
        "schema_version": 1,
        "institutions": order_institutions(result, institution_order),
    }


def write_json_viewer_data(
    results,
    today,
    lookback_days,
    data_dir="output/data",
    institution_order=None,
):
    """三点セットを一時領域で全検証し、成功後だけ正本へ反映する。"""
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    marker_path = viewer_ready_marker_path(data_dir)
    marker_path.unlink(missing_ok=True)

    report_data = build_report_data(results, today, lookback_days)
    report_data["institutions"] = order_institutions(
        report_data["institutions"],
        institution_order,
    )
    validate_report_document(report_data)

    with tempfile.TemporaryDirectory(
        dir=data_path.parent,
        prefix=".viewer-json-",
    ) as temporary_directory:
        staging_path = Path(temporary_directory)
        for source in data_path.glob("*.json"):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", source.stem):
                shutil.copy2(source, staging_path / source.name)

        staged_report_path = staging_path / f"{today}.json"
        staged_report_path.write_text(
            json.dumps(report_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        manifest = {
            "schema_version": 1,
            "reports": list_report_dates(staging_path),
        }
        validate_manifest(manifest)
        staged_manifest_path = staging_path / "index.json"
        staged_manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        institution_index = build_institution_index(
            staging_path,
            institution_order=institution_order,
        )
        validate_institution_index(institution_index)
        staged_institution_path = staging_path / "by-institution.json"
        staged_institution_path.write_text(
            json.dumps(institution_index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # 公開入口のindex.jsonは最後に置換する。
        replacements = [
            (staged_report_path, data_path / staged_report_path.name),
            (staged_institution_path, data_path / "by-institution.json"),
            (staged_manifest_path, data_path / "index.json"),
        ]
        originals = {
            target: target.read_bytes() if target.exists() else None
            for _, target in replacements
        }
        replaced = []
        try:
            for staged, target in replacements:
                os.replace(staged, target)
                replaced.append(target)
        except Exception:
            for target in reversed(replaced):
                original = originals[target]
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(original)
            raise

    marker_path.write_text(today + "\n", encoding="utf-8")


def send_email(subject, body, *, idempotency_key=None):
    """Resend APIでメールを送信"""
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    to_addr = os.environ.get("REPORT_TO", "").strip()
    if not api_key or not to_addr:
        print("送信設定が不足しているため、メール送信をスキップします")
        return False
    return send_resend_email(
        subject,
        body,
        api_key=api_key,
        to_addr=to_addr,
        raise_on_error=True,
        idempotency_key=idempotency_key,
    )


def collect_institution(institution, lookback_days, star_keywords, claude_client=None):
    """1機関を収集し、正常0件と失敗を区別した結果を返す。"""
    name = institution["name"]
    url = institution["url"]
    use_claude = institution.get("use_claude", False)
    scraper = institution.get("scraper", "programmatic")
    method = "Claude API" if use_claude else ("XML" if scraper == "hokuyo_xml" else "プログラム")
    print(f"\n--- {name}（{method}）")

    try:
        if use_claude:
            html = fetch_page(url, encoding=institution.get("encoding"))
            if claude_client is None:
                try:
                    claude_client = anthropic.Anthropic()
                except Exception as exc:
                    raise ExtractionError("Claude APIクライアントを初期化できませんでした。") from exc
            items = extract_news_with_claude(
                claude_client,
                name,
                url,
                html,
                lookback_days,
            )
            passed, excluded = apply_filters(items, institution, star_keywords)
        else:
            items = sanitize_items(SCRAPERS[scraper](institution, url), name)
            passed_all, excluded = apply_filters(items, institution, star_keywords)
            passed = filter_by_lookback(passed_all, lookback_days)
            if not passed:
                fallback = get_fallback_item(passed_all, lookback_days)
                if fallback:
                    passed = [fallback]
    except FetchError as exc:
        print(f"  収集失敗: {exc}")
        return InstitutionResult(
            name,
            [],
            [],
            method,
            status="fetch_failed",
            error="ページまたはXMLを取得できませんでした。",
        ), claude_client
    except ExtractionError as exc:
        print(f"  抽出失敗: {exc}")
        return InstitutionResult(
            name,
            [],
            [],
            method,
            status="extract_failed",
            error="記事一覧を抽出できませんでした。",
        ), claude_client
    except Exception as exc:
        print(f"  解析失敗 ({name}): {type(exc).__name__}: {exc}")
        return InstitutionResult(
            name,
            [],
            [],
            method,
            status="parse_failed",
            error="取得内容を解析できませんでした。",
        ), claude_client

    status = "empty" if not items else "ok"
    print(f"  取得: {len(items)}件")
    print(f"  通過: {len(passed)}件 / 除外: {len(excluded)}件")
    return InstitutionResult(name, passed, excluded, method, status=status), claude_client


def run_collection(config, today):
    """収集から保存・送信まで実行し、Actionsへ返す終了コードを返す。"""
    lookback_days = config.get("lookback_days", 30)
    star_keywords = config.get("star_keywords", DEFAULT_STAR_KEYWORDS)
    institution_order = [
        institution["name"]
        for institution in config.get("institutions", [])
    ]

    print(f"=== 金融機関新着情報収集 ({today} / 過去{lookback_days}日) ===")
    viewer_ready_marker_path().unlink(missing_ok=True)

    claude_client = None  # use_claude機関がある時だけ遅延生成（現configでは未使用）
    results = []

    for institution in config["institutions"]:
        result, claude_client = collect_institution(
            institution,
            lookback_days,
            star_keywords,
            claude_client,
        )
        results.append(result)

    report = format_report(results, today, lookback_days)
    output_path = Path("output") / f"{today}.md"
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"\nレポート保存: {output_path}")

    # JSONはヴューアー用の追加出力。失敗してもメール送信とMarkdown保存は続ける。
    viewer_json_ok = False
    try:
        write_json_viewer_data(
            results,
            today,
            lookback_days,
            institution_order=institution_order,
        )
        viewer_json_ok = True
        print("ヴューアー用JSON保存: output/data/")
    except Exception as e:
        print(f"ヴューアー用JSON保存失敗（メール送信には影響しません）: {e}")

    failed_results = [result for result in results if result.status in FAILED_STATUSES]
    total_passed = sum(len(result.passed) for result in results)
    failure_note = f" / 失敗 {len(failed_results)}機関" if failed_results else ""
    subject = f"【金融機関新着情報】{today}（通過 {total_passed}件{failure_note}）"
    print(f"送信中: {subject}")
    email_ok = False
    try:
        email_ok = send_email(
            subject,
            report,
            idempotency_key=f"finpulse-news/{today}",
        )
    except Exception as exc:
        print(f"メール送信処理失敗: {type(exc).__name__}")

    issues = []
    if failed_results:
        issues.append(f"収集失敗 {len(failed_results)}機関")
    if not viewer_json_ok:
        issues.append("ヴューアーJSON生成失敗")
    if not email_ok:
        issues.append("メール送信失敗")
    if issues:
        print("\n処理は一部失敗しました: " + " / ".join(issues))
        print("生成済み成果物は保存済みです。")
        return 1

    print("\n完了！")
    return 0


def main():
    today = now_jst().strftime("%Y-%m-%d")
    config = load_config()
    return run_collection(config, today)


if __name__ == "__main__":
    sys.exit(main())
