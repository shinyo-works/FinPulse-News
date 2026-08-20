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


def main(argv=None) -> int:
    argv = sys.argv if argv is None else argv
    if len(argv) != 3:
        print("使い方: python send_resend.py \"件名\" \"本文\"")
        return 1

    return 0 if send_via_resend(argv[1], argv[2]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
