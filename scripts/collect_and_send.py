"""
scripts/collect_and_send.py — 金融機関新着情報収集・レポート送信
GitHub Actions で週次実行される
"""
import os
import json
import re
from datetime import datetime
from pathlib import Path

import requests
import anthropic


def load_config():
    with open("config.json", encoding="utf-8") as f:
        return json.load(f)


def fetch_page(url):
    """HTMLページを取得"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        return resp.text
    except Exception as e:
        print(f"  取得失敗 ({url}): {e}")
        return None


def extract_news_with_claude(client, name, url, html, mode):
    """Claude APIでHTMLからニュース一覧を抽出"""
    cutoff_note = (
        "過去7日以内の記事のみ抽出してください。"
        if mode == "weekly"
        else "すべての記事を抽出してください。"
    )
    today = datetime.now().strftime("%Y-%m-%d")
    html_truncated = html[:15000]

    prompt = f"""以下は「{name}」（{url}）の新着情報ページのHTMLです。
今日の日付: {today}
{cutoff_note}

ニュース記事の一覧を抽出し、以下のJSON形式のみ返してください（説明文不要）。
記事が見つからない場合は空配列 [] を返してください。
URLは絶対URLに変換してください（/path/... → {url.rstrip('/')}/../）。

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


def apply_filters(items, institution):
    """include_keywords・exclude_rules でフィルタ適用"""
    include_kw = institution.get("include_keywords", [])
    exclude_rules = institution.get("exclude_rules", [])

    passed = []
    excluded = []

    for item in items:
        title = item.get("title", "")

        # 除外チェック
        excluded_by = None
        for rule in exclude_rules:
            kw = rule["keyword"]
            if kw in title:
                unless = rule.get("unless", [])
                if unless and any(u in title for u in unless):
                    continue  # unless条件一致 → このルールは適用しない
                excluded_by = kw
                break

        if excluded_by:
            item["exclude_keyword"] = excluded_by
            excluded.append(item)
            continue

        # 通過チェック
        if not include_kw or any(kw in title for kw in include_kw):
            if any(k in title for k in ["金利", "キャンペーン"]):
                item["star"] = True
            passed.append(item)
        else:
            excluded.append(item)

    return passed, excluded


def format_report(results, today, mode):
    """Markdown形式のレポートを生成"""
    lines = [f"# 金融機関新着情報レポート — {today}", ""]
    total_passed = 0
    total_excluded = 0

    for name, passed, excluded in results:
        lines.append(f"## {name}")
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

    lines.append(f"*収集日時: {today} / モード: {mode}*")
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
    mode = config.get("mode", "weekly")

    print(f"=== 金融機関新着情報収集 ({today} / {mode}モード) ===")

    client = anthropic.Anthropic()
    results = []

    for institution in config["institutions"]:
        name = institution["name"]
        url = institution["url"]
        print(f"\n▶ {name}")

        html = fetch_page(url)
        if not html:
            results.append((name, [], []))
            continue

        items = extract_news_with_claude(client, name, url, html, mode)
        print(f"  取得: {len(items)}件")

        passed, excluded = apply_filters(items, institution)
        print(f"  通過: {len(passed)}件 / 除外: {len(excluded)}件")
        results.append((name, passed, excluded))

    # レポート生成・保存
    report = format_report(results, today, mode)
    output_path = Path("output") / f"{today}.md"
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"\nレポート保存: {output_path}")

    # メール送信
    total_passed = sum(len(p) for _, p, _ in results)
    subject = f"【金融機関新着情報】{today}（通過 {total_passed}件）"
    print(f"送信中: {subject}")
    send_email(subject, report)

    print("\n完了！")
