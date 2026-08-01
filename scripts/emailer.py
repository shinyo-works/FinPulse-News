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


def send_resend_email(
    subject,
    body,
    *,
    html_body=False,
    api_key=None,
    to_addr=None,
    raise_on_error=False,
    idempotency_key=None,
):
    """Resend APIでメールを送る。失敗時はFalse、raise_on_error時は例外も送出する。"""
    api_key = (api_key if api_key is not None else os.environ.get("RESEND_API_KEY", "")).strip()
    to_addr = (to_addr if to_addr is not None else os.environ.get("REPORT_TO", "")).strip()
    if not api_key:
        print("エラー: RESEND_API_KEY が設定されていません")
        return False
    if not to_addr:
        print("エラー: REPORT_TO が設定されていません")
        return False
    if idempotency_key is not None:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256:
            raise ValueError("idempotency_key は1〜256文字にしてください。")
        try:
            idempotency_key.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("idempotency_key はASCII文字にしてください。") from exc

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

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    try:
        response = requests.post(
            RESEND_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=30,
        )
        if not response.ok:
            request_id = response.headers.get("x-request-id", "不明")
            print(f"Resend送信失敗: status={response.status_code}, request_id={request_id}")
            response.raise_for_status()
        try:
            response_data = response.json()
        except ValueError:
            response_data = {}
        print(f"メール送信成功: ID={response_data.get('id', '不明')}")
        return True
    except requests.Timeout:
        print("Resend送信結果不明: タイムアウトしました（同じ冪等キーで安全に再実行できます）")
        if raise_on_error:
            raise
        return False
    except requests.RequestException as error:
        status = error.response.status_code if error.response is not None else "不明"
        print(f"Resend通信失敗: type={type(error).__name__}, status={status}")
        if raise_on_error:
            raise
        return False
