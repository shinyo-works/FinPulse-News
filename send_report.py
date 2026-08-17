"""
send_report.py — 最新の収集レポートをResend経由でメール送信

使い方:
  python send_report.py              # output/ 内の最新ファイルを送信
  python send_report.py 2026-05-17   # 日付指定
"""

import re
import sys
from datetime import datetime
from pathlib import Path

from scripts.emailer import load_dotenv, send_resend_email

load_dotenv()


def find_report(date_str=None):
    output_dir = (Path(__file__).resolve().parent / "output").resolve()
    if date_str:
        try:
            normalized_date = datetime.strptime(date_str, "%Y-%m-%d").date().isoformat()
        except (TypeError, ValueError):
            print("エラー: 日付は実在する YYYY-MM-DD 形式で指定してください")
            return None, None
        if normalized_date != date_str:
            print("エラー: 日付は YYYY-MM-DD 形式で指定してください")
            return None, None

        path = (output_dir / f"{normalized_date}.md").resolve()
        if not path.is_relative_to(output_dir):
            print("エラー: output/ 外のファイルは指定できません")
            return None, None
        if not path.exists():
            print(f"エラー: {path} が見つかりません")
            return None, None
        return path, normalized_date
    # glob の ? は任意1文字のため、数字のみの日付名に絞る（list_report_dates と同じ流儀）
    files = sorted(
        path
        for path in output_dir.glob("????-??-??.md")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem)
    )
    if not files:
        print("エラー: output/ にレポートファイルがありません")
        return None, None
    latest = files[-1]
    return latest, latest.stem


def send_via_resend(subject, body):
    return send_resend_email(subject, body, html_body=True)


if __name__ == "__main__":
    if len(sys.argv) > 2:
        print("使い方: python send_report.py [YYYY-MM-DD]")
        sys.exit(2)
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
