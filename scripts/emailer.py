"""Resend送信とローカル.env読込を共通化するモジュール。"""

import html
import os
from pathlib import Path

import requests


RESEND_ENDPOINT = "https://api.resend.com/emails"
RESEND_FROM = "onboarding@resend.dev"


def load_dotenv(env_path=None):
    """リポジトリ直下の.envを読み込む。既存の環境変数は上書きしない。"""
    path = Path(env_path) if env_path else Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def send_resend_email(subject, body, *, html_body=False, api_key=None, to_addr=None, raise_on_error=False):
    """Resend APIでメールを送る。失敗時はFalse、raise_on_error時は例外も送出する。"""
    api_key = (api_key if api_key is not None else os.environ.get("RESEND_API_KEY", "")).strip()
    to_addr = (to_addr if to_addr is not None else os.environ.get("REPORT_TO", "")).strip()
    if not api_key:
        print("エラー: RESEND_API_KEY が設定されていません")
        return False
    if not to_addr:
        print("エラー: REPORT_TO が設定されていません")
        return False

    payload = {
        "from": RESEND_FROM,
        "to": [to_addr],
        "subject": subject,
    }
    if html_body:
        escaped_body = html.escape(body)
        payload["html"] = f"<pre style='font-family:sans-serif;white-space:pre-wrap'>{escaped_body}</pre>"
    else:
        payload["text"] = body

    try:
        response = requests.post(
            RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if not response.ok:
            print(f"Resendエラー {response.status_code}: {response.text}")
            response.raise_for_status()
        print(f"メール送信成功: ID={response.json().get('id')}")
        return True
    except requests.RequestException as error:
        print(f"Resend エラー: {error}")
        if raise_on_error:
            raise
        return False
