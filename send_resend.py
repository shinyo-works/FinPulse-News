"""
send_resend.py — Resend SDK経由でメール送信

使い方:
  python send_resend.py "件名" "本文"
  .env に RESEND_API_KEY が設定されていれば自動で読み込まれる
"""

import sys
import os


def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

_load_dotenv()


def send_via_resend(subject: str, body: str) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        print("エラー: RESEND_API_KEY が設定されていません（.env を確認してください）")
        return False

    try:
        import resend
        resend.api_key = api_key
        r = resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": "redacted@example.com",
            "subject": subject,
            "html": f"<pre style='font-family:sans-serif;white-space:pre-wrap'>{body}</pre>"
        })
        print(f"Resendメール送信成功: ID={r.get('id', '不明')}")
        return True
    except ImportError:
        print("エラー: resend パッケージが未インストールです。以下を実行してください:")
        print("  pip install resend")
        return False
    except Exception as e:
        print(f"Resend エラー: {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("使い方: python send_resend.py \"件名\" \"本文\"")
        sys.exit(1)

    ok = send_via_resend(sys.argv[1], sys.argv[2])
    sys.exit(0 if ok else 1)
