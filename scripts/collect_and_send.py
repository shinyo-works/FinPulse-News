"""
scripts/collect_and_send.py — Resend でメール送信

2つのモードで動作:
  repository_dispatch: ルーティンからの dispatch イベントを受け取り送信
  workflow_dispatch:   output/ の最新 .md ファイルを送信
"""
import os
import sys
import json
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
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")

    if event_name == "repository_dispatch":
        # ルーティンが dispatch したペイロードを読み込む
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        with open(event_path, encoding="utf-8") as f:
            event = json.load(f)
        payload = event.get("client_payload", {})
        subject = payload.get("subject", "【金融機関新着情報】")
        body = payload.get("body", "")
        if not body:
            print("エラー: dispatch payload に body がありません")
            sys.exit(1)
        print(f"dispatch モード / 件名: {subject}")
    else:
        # workflow_dispatch: output/ の最新ファイルを使用
        output_dir = Path("output")
        files = sorted(output_dir.glob("20??-??-??.md"))
        if not files:
            print("送信対象の output/*.md ファイルがありません")
            sys.exit(0)
        latest = files[-1]
        subject = f"【金融機関新着情報】{latest.stem}"
        body = latest.read_text(encoding="utf-8")
        print(f"ファイルモード / 送信対象: {latest.name}")

    send_email(subject, body)
    print("完了！")
