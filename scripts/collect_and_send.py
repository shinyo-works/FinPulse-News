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
try:
    # XXE・billion-laughs 対策。本番(GitHub Actions)では defusedxml をインストール済み
    from defusedxml import ElementTree as ET
except ImportError:  # ローカルにdefusedxml未導入の場合のフォールバック
    import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import anthropic

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


def now_jst():
    """JST基準の現在時刻（tz-aware）"""
    return datetime.now(JST)


def load_config():
    with open("config.json", encoding="utf-8") as f:
        return json.load(f)


def fetch_page(url, encoding=None):
    """HTMLページを取得"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        resp.encoding = encoding if encoding else resp.apparent_encoding
        return resp.text
    except Exception as e:
        print(f"  取得失敗 ({url}): {e}")
        return None


def extract_date_from_text(text):
    """テキストから日付（YYYY-MM-DD）を抽出"""
    patterns = [
        r'(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})',
        r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            y, mo, d = m.group(1), m.group(2), m.group(3)
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
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


def is_recent_excluded(item, today):
    """除外項目が配信日から3ヶ月以内かを判定する。不明・不正な日付は残す。"""
    today_date = parse_ymd(today) or now_jst().date()
    item_date = parse_ymd(item.get("date", ""))
    return item_date is None or item_date >= date_n_months_ago(3, today_date)


def extract_date_from_url(url):
    """URL 内の YYYYMMDD パターンから日付を抽出（例: /detail/20260528_xxx.html）"""
    m = re.search(r'/(\d{8})[_/]', url)
    if m:
        d = m.group(1)
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
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
    seen_titles = set()

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
        if title in seen_titles:
            continue
        seen_titles.add(title)

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
            resp = requests.get(xml_url, headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"  XML取得スキップ ({xml_url}): status {resp.status_code}")
                continue
            root = ET.fromstring(resp.content)
        except Exception as e:
            print(f"  XML取得失敗 ({xml_url}): {e}")
            continue
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
    return items


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
        return []


def filter_by_lookback(items, lookback_days):
    """lookback_days日以内の記事のみ通過（0=全件）"""
    if not lookback_days:
        return items
    cutoff = (now_jst() - timedelta(days=lookback_days)).date()
    result = []
    for item in items:
        date_str = item.get("date", "")
        if not date_str:
            result.append(item)
            continue
        try:
            if datetime.strptime(date_str, "%Y-%m-%d").date() >= cutoff:
                result.append(item)
        except ValueError:
            result.append(item)
    return result


def get_fallback_item(passed_all, lookback_days):
    """期間内に通過記事がない場合、期間外で最も新しい通過記事を返す"""
    if not lookback_days:
        return None
    cutoff = (now_jst() - timedelta(days=lookback_days)).date()
    older = []
    for item in passed_all:
        date_str = item.get("date", "")
        if not date_str:
            continue
        try:
            if datetime.strptime(date_str, "%Y-%m-%d").date() < cutoff:
                older.append(item)
        except ValueError:
            pass
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
            if kw in title:
                unless = rule.get("unless", [])
                if unless and any(u in title for u in unless):
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

    # 通過セクション（全機関）
    for name, passed, excluded, method in results:
        lines.append(f"## {name}　*（収集: {method}）*")
        lines.append("")
        lines.append(f"### ✅ 通過（{len(passed)}件）")
        if passed:
            lines.append("| 日付 | タイトル | URL |")
            lines.append("|---|---|---|")
            for item in passed:
                star = " ⭐金利・キャンペーン" if item.get("star") else ""
                note = " ※1ヵ月超・最新" if item.get("fallback") else ""
                if item.get("date_inferred"):
                    note += " ※当月分（日付はページに記載なし・当月初で補完）"
                lines.append(f"| {item.get('date','')} | {item['title']}{star}{note} | {item.get('url','')} |")
        else:
            lines.append("（該当なし）")
        lines.append("")
        lines.append("---")
        lines.append("")
        total_passed += len(passed)

    # 除外セクション（全機関まとめて末尾）
    lines.append("# 除外一覧")
    lines.append("")
    for name, passed, excluded, method in results:
        excluded_recent = [it for it in excluded if is_recent_excluded(it, today)]
        total_excluded += len(excluded_recent)
        lines.append(f"## {name}　❌ 除外（{len(excluded_recent)}件）")
        if excluded_recent:
            lines.append("| 日付 | タイトル | 除外キーワード |")
            lines.append("|---|---|---|")
            for item in excluded_recent:
                lines.append(f"| {item.get('date','')} | {item['title']} | {item.get('exclude_keyword','')} |")
        lines.append("")

    period = f"過去{lookback_days}日" if lookback_days else "全件"
    lines.append(f"*収集日時: {today} / 対象期間: {period}*")
    lines.append(f"*合計: 通過 {total_passed}件 / 除外 {total_excluded}件*")
    return "\n".join(lines)


# Markdown表示用の注記。JSONでは文字列ではなく真偽値のフラグとして保持する。
_TITLE_ANNOTATIONS = [
    "⭐金利・キャンペーン",
    "※1ヵ月超・最新",
    "※当月分（日付はページに記載なし・当月初で補完）",
]


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
    for name, passed, excluded, method in results:
        passed_data = []
        for item in passed:
            passed_data.append({
                "date": item.get("date", ""),
                "title": clean_report_title(item.get("title", "")),
                "url": item.get("url", ""),
                "star": bool(item.get("star")),
                "fallback": bool(item.get("fallback")),
                "date_inferred": bool(item.get("date_inferred")),
            })

        excluded_data = []
        for item in excluded:
            if not is_recent_excluded(item, today):
                continue
            excluded_data.append({
                "date": item.get("date", ""),
                "title": clean_report_title(item.get("title", "")),
                "exclude_keyword": item.get("exclude_keyword", ""),
            })

        institutions.append({
            "name": name,
            "method": method,
            "passed": passed_data,
            "excluded": excluded_data,
        })

    return {
        "date": today,
        "lookback_days": lookback_days,
        "institutions": institutions,
    }


def list_report_dates(data_dir):
    """日付別JSONのファイル名から、新しい順の日付一覧を作る。"""
    data_path = Path(data_dir)
    dates = [
        path.stem
        for path in data_path.glob("*.json")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem)
    ]
    return sorted(dates, reverse=True)


def build_institution_index(data_dir, window_months=INSTITUTION_WINDOW_MONTHS, today=None):
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

    return {"institutions": result}


def write_json_viewer_data(results, today, lookback_days, data_dir="output/data"):
    """日付別・日付一覧・機関別のJSONをまとめて書き出す。"""
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)

    report_data = build_report_data(results, today, lookback_days)
    report_path = data_path / f"{today}.json"
    report_path.write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest = {"reports": list_report_dates(data_path)}
    (data_path / "index.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    institution_index = build_institution_index(data_path)
    (data_path / "by-institution.json").write_text(
        json.dumps(institution_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def send_email(subject, body):
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
    )


if __name__ == "__main__":
    today = now_jst().strftime("%Y-%m-%d")
    config = load_config()
    lookback_days = config.get("lookback_days", 30)
    star_keywords = config.get("star_keywords", DEFAULT_STAR_KEYWORDS)

    print(f"=== 金融機関新着情報収集 ({today} / 過去{lookback_days}日) ===")

    claude_client = None  # use_claude機関がある時だけ遅延生成（現configでは未使用）
    results = []

    for institution in config["institutions"]:
        name = institution["name"]
        url = institution["url"]
        use_claude = institution.get("use_claude", False)
        scraper = institution.get("scraper", "programmatic")
        method = "Claude API" if use_claude else ("XML" if scraper == "hokuyo_xml" else "プログラム")
        print(f"\n▶ {name}（{method}）")

        if use_claude:
            html = fetch_page(url, encoding=institution.get("encoding"))
            if not html:
                results.append((name, [], [], method))
                continue
            if claude_client is None:
                claude_client = anthropic.Anthropic()
            items = extract_news_with_claude(claude_client, name, url, html, lookback_days)
            passed, excluded = apply_filters(items, institution, star_keywords)
        else:
            if scraper == "hokuyo_xml":
                items = scrape_hokuyo_xml(url)
            else:
                html = fetch_page(url, encoding=institution.get("encoding"))
                if not html:
                    results.append((name, [], [], method))
                    continue
                items = scrape_news_programmatic(html, url)
            passed_all, excluded = apply_filters(items, institution, star_keywords)
            passed = filter_by_lookback(passed_all, lookback_days)
            if not passed:
                fallback = get_fallback_item(passed_all, lookback_days)
                if fallback:
                    passed = [fallback]

        print(f"  取得: {len(items)}件")
        print(f"  通過: {len(passed)}件 / 除外: {len(excluded)}件")
        results.append((name, passed, excluded, method))

    report = format_report(results, today, lookback_days)
    output_path = Path("output") / f"{today}.md"
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"\nレポート保存: {output_path}")

    # JSONはヴューアー用の追加出力。失敗しても従来のメール送信は続ける。
    try:
        write_json_viewer_data(results, today, lookback_days)
        print("ヴューアー用JSON保存: output/data/")
    except Exception as e:
        print(f"ヴューアー用JSON保存失敗（メール送信には影響しません）: {e}")

    total_passed = sum(len(p) for _, p, _, _ in results)
    subject = f"【金融機関新着情報】{today}（通過 {total_passed}件）"
    print(f"送信中: {subject}")
    send_email(subject, report)

    print("\n完了！")
