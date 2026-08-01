# -*- coding: utf-8 -*-
"""金利履歴データ（docs/data/rate-history.json）を更新するスクリプト。

報告自動化ツールが毎週出力する report_data_YYYYMMDD.json を入力に取り、
各(機関×商品×金利種別)について、調査日ごとの確認値を
{ rate, observed_on } として保存する。値を取得できなかった項目は保存しないため、
画面側で「据え置き」と「未確認」を混同せずに表示できる。

依存: 標準ライブラリのみ（json）。スクレイピングやGeminiは不要。

使い方:
    python scripts/update_rate_history.py path/to/report_data_20260721.json
    # survey_date が JSON 内に無い場合は --date で明示:
    python scripts/update_rate_history.py report_data.json --date 2026-07-21
"""
import argparse
import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parent.parent / "docs" / "data" / "rate-history.json"

# report_data.loan_table のキー -> 履歴側の rate_type
RATE_KEY_MAP = {
    "loan_variable": "variable",
    "loan_fixed_3y": "fixed_3y",
    "loan_fixed_5y": "fixed_5y",
    "loan_fixed_10y": "fixed_10y",
}
REPORT_RATE_CONTRACT_VERSION = 1


def expected_rate_contract_metadata() -> dict:
    """報告自動化ツールから受け入れる金利項目契約を返す。"""
    return {
        "version": REPORT_RATE_CONTRACT_VERSION,
        "loan_rate_fields": list(RATE_KEY_MAP),
    }

DEFAULT_LABELS = {
    "variable": "変動",
    "fixed_3y": "固定3年",
    "fixed_5y": "固定5年",
    "fixed_10y": "固定10年",
}
DEFAULT_ORDER = ["variable", "fixed_3y", "fixed_5y", "fixed_10y"]
MAX_RATE_PERCENT = 100.0
SCHEMA_VERSION = 2


def normalize_date(value: str) -> str:
    """'2026/07/21' や '20260721' を 'YYYY-MM-DD' に正規化する。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("調査日が空です。--date で指定してください。")
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        raise ValueError(f"調査日の形式が不正です: {value}")
    normalized = f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"調査日が実在しません: {value}") from exc


def validate_id(value, field_name: str) -> str:
    """機関ID・商品IDが空でない文字列であることを確認する。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} は空でない文字列にしてください。")
    return value.strip()


def validate_rate(value, field_name: str) -> float:
    """金利が有限の数値かつ現実的な範囲内であることを確認する。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} は数値にしてください: {value!r}")
    rate = float(value)
    if not math.isfinite(rate) or not 0 <= rate <= MAX_RATE_PERCENT:
        raise ValueError(
            f"{field_name} は 0〜{MAX_RATE_PERCENT:g} の有限数にしてください: {value!r}"
        )
    return rate


def validate_report_data(report_data: dict) -> None:
    """入力レポートの住宅ローン行を、履歴変更前に検証する。"""
    if not isinstance(report_data, dict):
        raise ValueError("入力JSONのルートはオブジェクトにしてください。")
    contract = report_data.get("rate_contract")
    expected_contract = expected_rate_contract_metadata()
    if contract != expected_contract:
        raise ValueError(
            "rate_contract がFinPulseの対応契約と一致しません。"
            f"期待値={expected_contract!r} / 入力値={contract!r}"
        )
    loans = report_data.get("loan_table")
    if not isinstance(loans, list) or not loans:
        raise ValueError("loan_table は1件以上の配列にしてください。")

    seen_products = set()
    for index, loan in enumerate(loans, start=1):
        if not isinstance(loan, dict):
            raise ValueError(f"loan_table[{index}] はオブジェクトにしてください。")
        bank_id = validate_id(loan.get("bank_id"), f"loan_table[{index}].bank_id")
        product_id = validate_id(loan.get("product_id"), f"loan_table[{index}].product_id")
        product_key = (bank_id, product_id)
        if product_key in seen_products:
            raise ValueError(f"機関IDと商品IDが重複しています: {bank_id}/{product_id}")
        seen_products.add(product_key)

        for loan_key in RATE_KEY_MAP:
            value = loan.get(loan_key)
            if value is not None:
                validate_rate(value, f"loan_table[{index}].{loan_key}")


def migrate_history(history: dict) -> None:
    """旧形式の変更時点履歴を、調査日ごとの観測形式へ移行する。"""
    history.setdefault("rate_type_order", DEFAULT_ORDER)
    history.setdefault("rate_type_labels", DEFAULT_LABELS)
    history.setdefault("rows", [])

    raw_observation_dates = history.get("observation_dates", [])
    if not isinstance(raw_observation_dates, list):
        raise ValueError("履歴JSONの observation_dates は配列にしてください。")
    observed_dates = set(raw_observation_dates)
    for row in history.get("rows", []):
        for entry in row.get("history", []):
            if "observed_on" not in entry and "effective_from" in entry:
                entry["observed_on"] = entry.pop("effective_from")
            if entry.get("observed_on"):
                observed_dates.add(entry["observed_on"])
    if history.get("generated_at"):
        observed_dates.add(history["generated_at"])

    history["observation_dates"] = sorted(observed_dates, reverse=True)
    history["schema_version"] = SCHEMA_VERSION


def validate_history(history: dict) -> None:
    """履歴の行キー・金利・日付順を検証する。"""
    if not isinstance(history, dict):
        raise ValueError("履歴JSONのルートはオブジェクトにしてください。")
    if history.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"履歴JSONの schema_version は {SCHEMA_VERSION} にしてください。")

    observation_dates = history.get("observation_dates")
    if not isinstance(observation_dates, list):
        raise ValueError("履歴JSONの observation_dates は配列にしてください。")
    normalized_dates = [normalize_date(value) for value in observation_dates]
    if normalized_dates != observation_dates:
        raise ValueError("observation_dates は YYYY-MM-DD にしてください。")
    if len(set(observation_dates)) != len(observation_dates):
        raise ValueError("observation_dates に重複があります。")
    if observation_dates != sorted(observation_dates, reverse=True):
        raise ValueError("observation_dates は新しい日付順にしてください。")
    observation_date_set = set(observation_dates)

    rows = history.get("rows")
    if not isinstance(rows, list):
        raise ValueError("履歴JSONの rows は配列にしてください。")

    seen_rows = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"rows[{index}] はオブジェクトにしてください。")
        bank_id = validate_id(row.get("bank_id"), f"rows[{index}].bank_id")
        product_id = validate_id(row.get("product_id"), f"rows[{index}].product_id")
        rate_type = row.get("rate_type")
        if rate_type not in DEFAULT_ORDER:
            raise ValueError(f"rows[{index}].rate_type が不正です: {rate_type!r}")
        key = row_key(bank_id, product_id, rate_type)
        if key in seen_rows:
            raise ValueError(f"履歴行が重複しています: {'/'.join(key)}")
        seen_rows.add(key)

        entries = row.get("history")
        if not isinstance(entries, list):
            raise ValueError(f"rows[{index}].history は配列にしてください。")
        previous_date = None
        seen_dates = set()
        for entry_index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise ValueError(f"rows[{index}].history[{entry_index}] はオブジェクトにしてください。")
            observed_on = normalize_date(entry.get("observed_on"))
            if observed_on != entry.get("observed_on"):
                raise ValueError(
                    f"rows[{index}].history[{entry_index}].observed_on は YYYY-MM-DD にしてください。"
                )
            if observed_on not in observation_date_set:
                raise ValueError(f"rows[{index}] の調査日が observation_dates にありません: {observed_on}")
            if observed_on in seen_dates:
                raise ValueError(f"rows[{index}] に同じ調査日が重複しています: {observed_on}")
            if previous_date is not None and observed_on > previous_date:
                raise ValueError(f"rows[{index}].history は新しい日付順にしてください。")
            seen_dates.add(observed_on)
            previous_date = observed_on
            validate_rate(entry.get("rate"), f"rows[{index}].history[{entry_index}].rate")

    generated_at = history.get("generated_at")
    if generated_at and normalize_date(generated_at) != generated_at:
        raise ValueError("generated_at は YYYY-MM-DD にしてください。")
    if generated_at and not observation_dates:
        raise ValueError("generated_at がある場合は observation_dates も必要です。")
    if observation_dates and generated_at != observation_dates[0]:
        raise ValueError("generated_at は最新の observation_dates と一致させてください。")


def latest_history_date(history: dict) -> str:
    """履歴全体で最も新しい日付を返す。"""
    return max(history.get("observation_dates", []), default="")


def load_history() -> dict:
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        migrate_history(data)
        return data
    return {
        "schema_version": SCHEMA_VERSION,
        "rate_type_order": DEFAULT_ORDER,
        "rate_type_labels": DEFAULT_LABELS,
        "observation_dates": [],
        "rows": [],
    }


def row_key(bank_id: str, product_id: str, rate_type: str) -> tuple:
    return (bank_id or "", product_id or "", rate_type)


def extend_automated_source_period(history: dict, survey_date: str) -> None:
    """週次自動化の出典期間を、最新の調査日まで延長する。"""
    for source in history.get("data_sources", []):
        if source.get("kind") != "financial_report_tool":
            continue
        current_end = source.get("period_end", "")
        if not current_end or survey_date > current_end:
            source["period_end"] = survey_date


def update_history(
    history: dict,
    report_data: dict,
    survey_date: str,
    *,
    allow_backfill: bool = False,
) -> dict:
    """report_data の住宅ローン金利を履歴へ反映する。返り値は変更サマリー。"""
    survey_date = normalize_date(survey_date)
    migrate_history(history)
    validate_history(history)
    validate_report_data(report_data)
    replaced_demo = history.get("is_demo") is True
    if replaced_demo:
        # デモ金利を実績として残さない。入力検証後にだけ初期化する。
        history["rows"] = []
        history["observation_dates"] = []
        history.pop("generated_at", None)
    latest_date = latest_history_date(history)
    if latest_date and survey_date < latest_date and not allow_backfill:
        raise ValueError(
            f"調査日 {survey_date} は既存の最新日 {latest_date} より古いため更新できません。"
        )

    if survey_date not in history["observation_dates"]:
        history["observation_dates"].append(survey_date)
        history["observation_dates"].sort(reverse=True)

    index = {
        row_key(r.get("bank_id"), r.get("product_id"), r.get("rate_type")): r
        for r in history["rows"]
    }
    summary = {
        "replaced_demo": replaced_demo,
        "added_rows": 0,
        "changed": 0,
        "unchanged": 0,
    }

    for loan in report_data.get("loan_table", []):
        # 手動入力の機関（サイトから取得できずconfig/manual_rates.jsonに手書きした値）は
        # 履歴に入れない。毎週同じ値がコピーされるだけで「その日に確認した金利」ではなく、
        # 自動取得分と並べると確認済みに見えてしまうため。金利調査レポート本体でも
        # ローン表から除外しており、それに揃える。
        if loan.get("manual"):
            continue
        bank_id = validate_id(loan.get("bank_id"), "bank_id")
        product_id = validate_id(loan.get("product_id"), "product_id")
        for loan_key, rate_type in RATE_KEY_MAP.items():
            value = loan.get(loan_key)
            if value is None:
                continue
            value = validate_rate(value, loan_key)
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
                    "history": [{"rate": value, "observed_on": survey_date}],
                }
                if loan.get("is_legacy"):
                    row["is_legacy"] = True
                history["rows"].append(row)
                index[key] = row
                summary["added_rows"] += 1
                continue

            hist = row.setdefault("history", [])
            row_latest_date = hist[0].get("observed_on") if hist else ""
            if not row_latest_date or survey_date >= row_latest_date:
                # 過去データの投入で現在の商品名・URLを巻き戻さない。
                row["bank_name"] = loan.get("bank_name", row.get("bank_name", ""))
                row["product_name"] = loan.get("product_name", row.get("product_name", ""))
                if loan.get("url"):
                    row["url"] = loan.get("url")

            same_day = next(
                (entry for entry in hist if entry.get("observed_on") == survey_date),
                None,
            )
            older = next(
                (entry for entry in hist if entry.get("observed_on", "") < survey_date),
                None,
            )
            if same_day is not None:
                previous_value = same_day.get("rate")
                same_day["rate"] = value
                if previous_value == value:
                    summary["unchanged"] += 1
                else:
                    summary["changed"] += 1
            else:
                hist.append({"rate": value, "observed_on": survey_date})
                hist.sort(key=lambda entry: entry["observed_on"], reverse=True)
                if older and older.get("rate") == value:
                    summary["unchanged"] += 1
                else:
                    summary["changed"] += 1

    history["generated_at"] = history["observation_dates"][0]
    history["is_demo"] = False
    extend_automated_source_period(history, survey_date)
    validate_history(history)
    return summary


def write_history_atomic(history: dict, path: Path = HISTORY_PATH) -> None:
    """完全な一時JSONを検証してから、履歴の正本へ置き換える。"""
    validate_history(history)
    serialized = json.dumps(history, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(serialized)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_file.name)

        with temporary_path.open(encoding="utf-8") as file:
            written = json.load(file)
        validate_history(written)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="金利履歴JSONを更新する")
    parser.add_argument("report_data", help="報告自動化ツールの report_data_*.json のパス")
    parser.add_argument("--date", help="調査日(YYYY-MM-DD)。JSONに survey_date が無い場合に指定")
    parser.add_argument(
        "--allow-backfill",
        action="store_true",
        help="既存の最新日より前の調査日を追加する（過去データ投入専用）",
    )
    args = parser.parse_args()

    with open(args.report_data, encoding="utf-8") as f:
        report_data = json.load(f)

    raw_date = args.date or report_data.get("survey_date", "")
    survey_date = normalize_date(raw_date)

    history = load_history()
    summary = update_history(
        history,
        report_data,
        survey_date,
        allow_backfill=args.allow_backfill,
    )

    write_history_atomic(history)

    print(f"金利履歴を更新しました（調査日 {survey_date}）: {HISTORY_PATH}")
    if summary["replaced_demo"]:
        print("  デモ履歴を削除し、実データで初期化しました")
    print(
        f"  新規行 {summary['added_rows']} / 金利変更 {summary['changed']} / "
        f"据え置き {summary['unchanged']}"
    )


if __name__ == "__main__":
    main()
