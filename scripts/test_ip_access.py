"""
scripts/test_ip_access.py — Cursor Automation の IP からアクセス可能か確認するテスト

実行方法:
  python scripts/test_ip_access.py

結果は output/test_ip_result.txt に書き出す。
"""
import urllib.request
import datetime
import os
from pathlib import Path

TARGETS = [
    ("帯広信用金庫", "https://www.shinkin.co.jp/obishin/news/"),
    ("JAめむろ",     "https://www.ja-memuro.or.jp/category/finance_info/"),
    ("JAおとふけ",   "https://www.ja-otofuke.jp/newslist/"),
    ("十勝信用組合", "https://www.tokachishinkumi.com/info/"),
    ("JA木野",       "https://ja-kino.com/news/"),
]

def check(name, url):
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            size = len(r.read())
            return f"✅ {name}: HTTP {r.status} ({size} bytes)"
    except Exception as e:
        return f"❌ {name}: {e}"

if __name__ == "__main__":
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"IP アクセステスト — {now}", ""]

    for name, url in TARGETS:
        result = check(name, url)
        print(result)
        lines.append(result)

    Path("output").mkdir(exist_ok=True)
    Path("output/test_ip_result.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n結果を output/test_ip_result.txt に書き出しました。")
