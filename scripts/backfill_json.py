"""
使い捨てスクリプト。

既存のMarkdownレポートをヴューアー用JSONへ変換する。
将来分は collect_and_send.py が直接JSONを出すため、この処理は不要。
"""
import json
import re
import sys
from pathlib import Path

# リポジトリ直下からでも scripts/ 配下からでも共通関数を読み込めるようにする。
SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from collect_and_send import build_institution_index, clean_report_title, list_report_dates


REPORT_FILE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
PASSED_HEADING_PATTERN = re.compile(r"^##\s+(.+?)\s+\*（収集:\s*(.+?)）\*$")
EXCLUDED_HEADING_PATTERN = re.compile(r"^##\s+(.+?)\s+❌\s*除外（\d+件）$")


def parse_table_row(line):
    """Markdown表のデータ行を列ごとに分解する。"""
    if not line.startswith("|") or re.match(r"^\|\s*:?-+", line):
        return None
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) < 3 or cells[0] == "日付":
        return None
    return cells


def parse_title_flags(title):
    """タイトル末尾の表示注記をフラグへ戻す。"""
    return {
        "title": clean_report_title(title),
        "star": "⭐" in title,
        "fallback": "※1ヵ月超・最新" in title,
        "date_inferred": "※当月分（日付はページに記載なし・当月初で補完）" in title,
    }


def parse_report(path, method_by_name):
    """1件のMarkdownレポートを日付別JSONの形へ復元する。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    institutions = []
    institutions_by_name = {}
    current_institution = None
    table_mode = None

    def get_or_create_institution(name, method=None):
        institution = institutions_by_name.get(name)
        if institution is None:
            institution = {
                "name": name,
                "method": method or method_by_name.get(name, "不明"),
                "passed": [],
                "excluded": [],
            }
            institutions.append(institution)
            institutions_by_name[name] = institution
        elif method:
            institution["method"] = method
        return institution

    for line in lines:
        if line == "# 除外一覧":
            table_mode = None
            current_institution = None
            continue

        heading = PASSED_HEADING_PATTERN.match(line)
        if heading:
            name, method = heading.groups()
            current_institution = get_or_create_institution(name.strip(), method.strip())
            table_mode = None
            continue

        heading = EXCLUDED_HEADING_PATTERN.match(line)
        if heading:
            name = heading.group(1).strip()
            current_institution = get_or_create_institution(name)
            table_mode = "excluded"
            continue

        # 初期の手動レポートには収集方式を含まない単純な機関見出しもある。
        if line.startswith("## "):
            name = line[3:].strip()
            if name:
                current_institution = get_or_create_institution(name)
                table_mode = None
            continue

        if line.startswith("### ✅ 通過（"):
            table_mode = "passed"
            continue
        if line.startswith("### ❌ 除外（"):
            table_mode = "excluded"
            continue

        row = parse_table_row(line)
        if row is None or current_institution is None:
            continue

        if table_mode == "passed":
            date = row[0]
            title = row[-2]
            url = row[-1]
            flags = parse_title_flags(title)
            current_institution["passed"].append({
                "date": date,
                "title": flags["title"],
                "url": url,
                "star": flags["star"],
                "fallback": flags["fallback"],
                "date_inferred": flags["date_inferred"],
            })
        elif table_mode == "excluded":
            date, title, third_column = row[0], row[1], row[2]
            current_institution["excluded"].append({
                "date": date,
                "title": clean_report_title(title),
                "exclude_keyword": third_column,
            })

    lookback_match = re.search(r"対象期間:\s*過去(\d+)日", text)
    lookback_days = int(lookback_match.group(1)) if lookback_match else 90
    if "対象期間: 全件" in text:
        lookback_days = 0

    return {
        "date": path.stem,
        "lookback_days": lookback_days,
        "institutions": institutions,
    }


def write_json(path, data):
    """日本語をエスケープせず、読みやすいJSONとして保存する。"""
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main():
    output_dir = REPOSITORY_ROOT / "output"
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    config = json.loads((REPOSITORY_ROOT / "config.json").read_text(encoding="utf-8"))
    method_by_name = {}
    for institution in config.get("institutions", []):
        if institution.get("use_claude"):
            method = "Claude API"
        elif institution.get("scraper") == "hokuyo_xml":
            method = "XML"
        else:
            method = "プログラム"
        method_by_name[institution["name"]] = method

    report_paths = sorted(
        (path for path in output_dir.glob("*.md") if REPORT_FILE_PATTERN.fullmatch(path.name)),
        key=lambda path: path.name,
    )

    for report_path in report_paths:
        report_data = parse_report(report_path, method_by_name)
        write_json(data_dir / f"{report_path.stem}.json", report_data)
        print(f"変換: {report_path.name}")

    reports = list_report_dates(data_dir)
    write_json(data_dir / "index.json", {"reports": reports})
    write_json(data_dir / "by-institution.json", build_institution_index(data_dir))

    print(f"完了: 日付別JSON {len(report_paths)}件 + index.json + by-institution.json")


if __name__ == "__main__":
    main()
