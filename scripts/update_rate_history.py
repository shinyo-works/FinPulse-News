# -*- coding: utf-8 -*-
"""金利履歴データ（docs/data/rate-history.json）を更新するスクリプト。

報告自動化ツールが毎週出力する report_data_YYYYMMDD.json を入力に取り、
各(機関×商品×金利種別)について「金利が前回と変わったときだけ」履歴の先頭に
{ rate, effective_from } を1件追加する。金利が同じ週は何も足さない
（＝列はいたずらに増えず、金利が動いたタイミングだけが残る）。

依存: 標準ライブラリのみ（json）。スクレイピングやGeminiは不要。

使い方:
    python scripts/update_rate_history.py path/to/report_data_20260721.json
    # survey_date が JSON 内に無い場合は --date で明示:
    python scripts/update_rate_history.py report_data.json --date 2026-07-21
"""
import argparse
import json
import re
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parent.parent / "docs" / "data" / "rate-history.json"

# report_data.loan_table のキー -> 履歴側の rate_type
RATE_KEY_MAP = {
    "loan_variable": "variable",
    "loan_fixed_3y": "fixed_3y",
    "loan_fixed_5y": "fixed_5y",
    "loan_fixed_10y": "fixed_10y",
}

DEFAULT_LABELS = {
    "variable": "変動",
    "fixed_3y": "固定3年",
    "fixed_5y": "固定5年",
    "fixed_10y": "固定10年",
}
DEFAULT_ORDER = ["variable", "fixed_3y", "fixed_5y", "fixed_10y"]


def normalize_date(value: str) -> str:
    """'2026/07/21' や '20260721' を 'YYYY-MM-DD' に正規化する。"""
    if not value:
        raise ValueError("調査日が空です。--date で指定してください。")
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        raise ValueError(f"調査日の形式が不正です: {value}")
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"


def load_history() -> dict:
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("schema_version", 1)
        data.setdefault("rate_type_order", DEFAULT_ORDER)
        data.setdefault("rate_type_labels", DEFAULT_LABELS)
        data.setdefault("rows", [])
        return data
    return {
        "schema_version": 1,
        "rate_type_order": DEFAULT_ORDER,
        "rate_type_labels": DEFAULT_LABELS,
        "rows": [],
    }


def row_key(bank_id: str, product_id: str, rate_type: str) -> tuple:
    return (bank_id or "", product_id or "", rate_type)


def update_history(history: dict, report_data: dict, survey_date: str) -> dict:
    """report_data の住宅ローン金利を履歴へ反映する。返り値は変更サマリー。"""
    index = {
        row_key(r.get("bank_id"), r.get("product_id"), r.get("rate_type")): r
        for r in history["rows"]
    }
    summary = {"added_rows": 0, "changed": 0, "unchanged": 0}

    for loan in report_data.get("loan_table", []):
        bank_id = loan.get("bank_id")
        product_id = loan.get("product_id")
        for loan_key, rate_type in RATE_KEY_MAP.items():
            value = loan.get(loan_key)
            if value is None:
                continue
            key = row_key(bank_id, product_id, rate_type)
            row = index.get(key)
            if row is None:
                # 新規の(機関×商品×種別)。履歴を1件で作る。
                row = {
                    "bank_id": bank_id,
                    "bank_name": loan.get("bank_name", ""),
                    "product_id": product_id,
                    "product_name": loan.get("product_name", ""),
                    "url": loan.get("url"),
                    "rate_type": rate_type,
                    "history": [{"rate": value, "effective_from": survey_date}],
                }
                history["rows"].append(row)
                index[key] = row
                summary["added_rows"] += 1
                continue

            # 表示名・URLは最新に追従
            row["bank_name"] = loan.get("bank_name", row.get("bank_name", ""))
            row["product_name"] = loan.get("product_name", row.get("product_name", ""))
            if loan.get("url"):
                row["url"] = loan.get("url")

            hist = row.setdefault("history", [])
            if hist and hist[0].get("effective_from") == survey_date:
                # 同一週の再実行 → 先頭を上書き（列を重複させない）
                hist[0]["rate"] = value
                summary["unchanged"] += 1
            elif not hist or hist[0].get("rate") != value:
                # 金利が変わった → 先頭に新しい列を追加
                hist.insert(0, {"rate": value, "effective_from": survey_date})
                summary["changed"] += 1
            else:
                summary["unchanged"] += 1

    history["generated_at"] = survey_date
    history["is_demo"] = False
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="金利履歴JSONを更新する")
    parser.add_argument("report_data", help="報告自動化ツールの report_data_*.json のパス")
    parser.add_argument("--date", help="調査日(YYYY-MM-DD)。JSONに survey_date が無い場合に指定")
    args = parser.parse_args()

    with open(args.report_data, encoding="utf-8") as f:
        report_data = json.load(f)

    raw_date = args.date or report_data.get("survey_date", "")
    survey_date = normalize_date(raw_date)

    history = load_history()
    summary = update_history(history, report_data, survey_date)

    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"金利履歴を更新しました（調査日 {survey_date}）: {HISTORY_PATH}")
    print(
        f"  新規行 {summary['added_rows']} / 金利変更 {summary['changed']} / "
        f"据え置き {summary['unchanged']}"
    )


if __name__ == "__main__":
    main()
