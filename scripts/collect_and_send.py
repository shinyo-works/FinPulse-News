"""
scripts/collect_and_send.py — output/ の最新レポートを Resend でメール送信
GitHub Actions (push トリガー) で実行される

スクレイピングは Claude ルーティンが担当し、このスクリプトは送信のみ行う。
"""
import os
import sys
import requests
from pathlib import Path


def send_email(subject, body):
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        print("エラー: RESEND_API_KEY が設定されていません")
        sys.exit(1)

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
    output_dir = Path("output")
    files = sorted(output_dir.glob("20??-??-??.md"))

    if not files:
        print("送信対象の output/*.md ファイルがありません")
        sys.exit(0)

    latest = files[-1]
    date_str = latest.stem
    body = latest.read_text(encoding="utf-8")

    subject = f"【金融機関新着情報】{date_str}"
    print(f"送信対象: {latest.name}")
    print(f"件名: {subject}")
    send_email(subject, body)
    print("完了！")
