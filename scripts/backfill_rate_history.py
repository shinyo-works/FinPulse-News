# -*- coding: utf-8 -*-
"""複数の過去 report_data JSON から金利履歴を再構築する。

旧形式のレポートには商品IDが無いため、金融機関ごとに
「旧集計（商品区分なし）」という独立した商品へ変換する。
現在の商品へ推測で割り当てないことで、比較条件の違いを明示する。
"""
import argparse
import copy
import json
import re
from pathlib import Path

try:
    from .update_rate_history import (
        DEFAULT_LABELS,
        DEFAULT_ORDER,
        HISTORY_PATH,
        SCHEMA_VERSION,
        expected_rate_contract_metadata,
        normalize_date,
        update_history,
        write_history_atomic,
    )
except ImportError:  # ファイルを直接実行した場合
    from update_rate_history import (
        DEFAULT_LABELS,
        DEFAULT_ORDER,
        HISTORY_PATH,
        SCHEMA_VERSION,
        expected_rate_contract_metadata,
        normalize_date,
        update_history,
        write_history_atomic,
    )


def normalize_legacy_report(report_data: dict) -> dict:
    """商品IDの無い旧レポートを、推測を伴わない専用商品へ変換する。"""
    normalized = copy.deepcopy(report_data)
    # 契約情報導入前の保存済みレポートを、旧形式変換の入口で明示的に現行契約へ合わせる。
    normalized.setdefault("rate_contract", expected_rate_contract_metadata())
    for loan in normalized.get("loan_table", []):
        if loan.get("product_id"):
            continue
        bank_id = loan.get("bank_id")
        loan["product_id"] = f"{bank_id}_legacy"
        loan["product_name"] = "旧集計（商品区分なし）"
        loan["is_legacy"] = True
    return normalized


def report_date(report_data: dict, path: Path) -> str:
    """JSON内の日付を優先し、無い場合はファイル名の8桁日付を使う。"""
    raw_date = report_data.get("survey_date")
    if raw_date:
        return normalize_date(raw_date)
    match = re.search(r"(\d{8})", path.name)
    if not match:
        raise ValueError(f"調査日を特定できません: {path}")
    return normalize_date(match.group(1))


def rebuild_history(report_paths: list[Path]) -> dict:
    """入力を日付順に反映し、スキーマ2の履歴を返す。"""
    reports = []
    for path in report_paths:
        with path.open(encoding="utf-8") as file:
            report = normalize_legacy_report(json.load(file))
        reports.append((report_date(report, path), str(path), report))

    history = {
        "schema_version": SCHEMA_VERSION,
        "rate_type_order": DEFAULT_ORDER,
        "rate_type_labels": DEFAULT_LABELS,
        "observation_dates": [],
        "rows": [],
    }
    for survey_date, _path, report in sorted(reports):
        update_history(
            history,
            report,
            survey_date,
            allow_backfill=True,
        )
    return history


def main() -> None:
    parser = argparse.ArgumentParser(
        description="複数の report_data JSON から金利履歴を再構築する"
    )
    parser.add_argument("reports", nargs="+", type=Path, help="過去レポートJSON")
    parser.add_argument(
        "--output",
        type=Path,
        default=HISTORY_PATH,
        help="出力先（既定: docs/data/rate-history.json）",
    )
    args = parser.parse_args()

    history = rebuild_history(args.reports)
    write_history_atomic(history, args.output)
    print(
        f"金利履歴を再構築しました: {args.output}\n"
        f"  調査日 {len(history['observation_dates'])} / 履歴行 {len(history['rows'])}"
    )


if __name__ == "__main__":
    main()
