"""
send_resend.py — Resend API経由でメール送信

使い方:
  python send_resend.py "件名" "本文"
  .env に RESEND_API_KEY が設定されていれば自動で読み込まれる
"""

import sys

from scripts.emailer import load_dotenv, send_resend_email

load_dotenv()


def send_via_resend(subject: str, body: str) -> bool:
    return send_resend_email(subject, body, html_body=True)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("使い方: python send_resend.py \"件名\" \"本文\"")
        sys.exit(1)

    ok = send_via_resend(sys.argv[1], sys.argv[2])
    sys.exit(0 if ok else 1)
