"""
send_report.py — 最新の収集レポートをResend経由でメール送信

使い方:
  python send_report.py              # output/ 内の最新ファイルを送信
  python send_report.py 2026-05-17   # 日付指定
"""

import sys
import os
import glob
from pathlib import Path


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


def find_report(date_str=None):
    output_dir = Path(__file__).parent / "output"
    if date_str:
        path = output_dir / f"{date_str}.md"
        if not path.exists():
            print(f"エラー: {path} が見つかりません")
            return None, None
        return path, date_str
    files = sorted(output_dir.glob("????-??-??.md"))
    if not files:
        print("エラー: output/ にレポートファイルがありません")
        return None, None
    latest = files[-1]
    return latest, latest.stem


def send_via_resend(subject, body):
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        print("エラー: RESEND_API_KEY が設定されていません")
        return False
    to_addr = os.environ.get("REPORT_TO", "").strip()
    if not to_addr:
        print("エラー: REPORT_TO が設定されていません")
        return False
    try:
        import resend
        resend.api_key = api_key
        r = resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": to_addr,
            "subject": subject,
            "html": f"<pre style='font-family:sans-serif;white-space:pre-wrap'>{body}</pre>"
        })
        print(f"送信成功: ID={r.get('id', '不明')}")
        return True
    except ImportError:
        print("エラー: pip install resend を実行してください")
        return False
    except Exception as e:
        print(f"Resend エラー: {e}")
        return False


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    report_path, date_str = find_report(date_arg)
    if not report_path:
        sys.exit(1)

    body = report_path.read_text(encoding="utf-8")
    subject = f"【金融機関新着情報】{date_str}"

    print(f"送信対象: {report_path.name}")
    print(f"件名: {subject}")
    ok = send_via_resend(subject, body)
    sys.exit(0 if ok else 1)
