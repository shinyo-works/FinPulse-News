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
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import anthropic


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


def extract_date_from_url(url):
    """URL 内の YYYYMMDD パターンから日付を抽出（例: /detail/20260528_xxx.html）"""
    m = re.search(r'/(\d{8})[_/]', url)
    if m:
        d = m.group(1)
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
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

        items.append({"date": date, "title": title, "url": url})

    return items[:60]


def extract_news_with_claude(client, name, url, html, lookback_days):
    """Claude APIでHTMLからニュース一覧を抽出（複雑サイト用）"""
    cutoff_note = f"過去{lookback_days}日以内の記事のみ抽出してください。" if lookback_days else "すべての記事を抽出してください。"
    today = datetime.now().strftime("%Y-%m-%d")
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
    cutoff = datetime.now() - timedelta(days=lookback_days)
    result = []
    for item in items:
        date_str = item.get("date", "")
        if not date_str:
            result.append(item)
            continue
        try:
            if datetime.strptime(date_str, "%Y-%m-%d") >= cutoff:
                result.append(item)
        except ValueError:
            result.append(item)
    return result


def apply_filters(items, institution):
    """include_keywords・exclude_rules でフィルタ適用"""
    include_kw = institution.get("include_keywords", [])
    exclude_rules = institution.get("exclude_rules", [])
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
            if any(k in title for k in ["金利", "キャンペーン"]):
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

    for name, passed, excluded, method in results:
        lines.append(f"## {name}　*（収集: {method}）*")
        lines.append("")
        lines.append(f"### ✅ 通過（{len(passed)}件）")
        if passed:
            lines.append("| 日付 | タイトル | URL |")
            lines.append("|---|---|---|")
            for item in passed:
                star = " ⭐金利・キャンペーン" if item.get("star") else ""
                lines.append(f"| {item.get('date','')} | {item['title']}{star} | {item.get('url','')} |")
        else:
            lines.append("（該当なし）")
        lines.append("")
        lines.append(f"### ❌ 除外（{len(excluded)}件）")
        if excluded:
            lines.append("| 日付 | タイトル | 除外キーワード |")
            lines.append("|---|---|---|")
            for item in excluded:
                lines.append(f"| {item.get('date','')} | {item['title']} | {item.get('exclude_keyword','')} |")
        lines.append("")
        lines.append("---")
        lines.append("")
        total_passed += len(passed)
        total_excluded += len(excluded)

    period = f"過去{lookback_days}日" if lookback_days else "全件"
    lines.append(f"*収集日時: {today} / 対象期間: {period}*")
    lines.append(f"*合計: 通過 {total_passed}件 / 除外 {total_excluded}件*")
    return "\n".join(lines)


def send_email(subject, body):
    """Resend APIでメールを送信"""
    api_key = os.environ["RESEND_API_KEY"].strip()
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": "onboarding@resend.dev",
            "to": ["redacted@example.com"],
            "subject": subject,
            "text": body,
        },
        timeout=30,
    )
    if not response.ok:
        print(f"Resendエラー {response.status_code}: {response.text}")
        response.raise_for_status()
    print(f"メール送信成功: ID={response.json().get('id')}")


if __name__ == "__main__":
    today = datetime.now().strftime("%Y-%m-%d")
    config = load_config()
    lookback_days = config.get("lookback_days", 30)

    print(f"=== 金融機関新着情報収集 ({today} / 過去{lookback_days}日) ===")

    claude_client = anthropic.Anthropic()
    results = []

    for institution in config["institutions"]:
        name = institution["name"]
        url = institution["url"]
        use_claude = institution.get("use_claude", False)
        method = "Claude API" if use_claude else "プログラム"
        print(f"\n▶ {name}（{method}）")

        html = fetch_page(url, encoding=institution.get("encoding"))
        if not html:
            results.append((name, [], [], method))
            continue

        if use_claude:
            items = extract_news_with_claude(claude_client, name, url, html, lookback_days)
        else:
            items = scrape_news_programmatic(html, url)
            items = filter_by_lookback(items, lookback_days)

        print(f"  取得: {len(items)}件")

        passed, excluded = apply_filters(items, institution)
        print(f"  通過: {len(passed)}件 / 除外: {len(excluded)}件")
        results.append((name, passed, excluded, method))

    report = format_report(results, today, lookback_days)
    output_path = Path("output") / f"{today}.md"
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"\nレポート保存: {output_path}")

    total_passed = sum(len(p) for _, p, _, _ in results)
    subject = f"【金融機関新着情報】{today}（通過 {total_passed}件）"
    print(f"送信中: {subject}")
    send_email(subject, report)

    print("\n完了！")
