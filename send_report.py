"""
send_report.py — 最新の収集レポートをResend経由でメール送信

使い方:
  python send_report.py              # output/ 内の最新ファイルを送信
  python send_report.py 2026-05-17   # 日付指定
"""

import sys
from pathlib import Path

from scripts.emailer import load_dotenv, send_resend_email

load_dotenv()


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
    return send_resend_email(subject, body, html_body=True)


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
