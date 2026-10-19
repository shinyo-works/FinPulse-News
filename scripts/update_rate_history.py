# -*- coding: utf-8 -*-
"""金利履歴データ（docs/data/*.json）を更新するスクリプト。

報告自動化ツールが毎週出力する report_data_YYYYMMDD.json を入力に取り、
各(機関×商品×金利種別)について、調査日ごとの確認値を
{ rate, observed_on } として保存する。値を取得できなかった項目は保存しないため、
画面側で「据え置き」と「未確認」を混同せずに表示できる。

住宅ローン（loan_table -> rate-history.json）とマイカーローン
（car_loan_table -> car-loan-history.json）を同じ仕組みで扱う。違いは
DATASETS の定義だけに閉じ込め、積み上げ・検証の処理は共通にする。

依存: 標準ライブラリのみ（json）。スクレイピングやGeminiは不要。

使い方:
    python scripts/update_rate_history.py path/to/report_data_20260721.json
    # survey_date が JSON 内に無い場合は --date で明示:
    python scripts/update_rate_history.py report_data.json --date 2026-07-21
"""
import argparse
import copy
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

try:  # テストは scripts パッケージとして、本番は scripts/ を直接実行する
    from scripts.check_weekly_run import first_business_day
except ImportError:  # pragma: no cover - 直接実行のとき
    from check_weekly_run import first_business_day

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
HISTORY_PATH = DATA_DIR / "rate-history.json"
CAR_HISTORY_PATH = DATA_DIR / "car-loan-history.json"

# report_data.loan_table のキー -> 履歴側の rate_type
RATE_KEY_MAP = {
    "loan_variable": "variable",
    "loan_fixed_3y": "fixed_3y",
    "loan_fixed_5y": "fixed_5y",
    "loan_fixed_10y": "fixed_10y",
}
# report_data.car_loan_table のキー -> 履歴側の rate_type
CAR_RATE_KEY_MAP = {
    "car_loan_variable": "variable",
    "car_loan_fixed": "fixed",
}
# 1: 住宅ローンのみ。2: マイカーローンを追加。
# 上流とFinPulseは別リポジトリで別々に更新されるため、旧契約も受け入れ続ける。
REPORT_RATE_CONTRACT_VERSION = 1
REPORT_RATE_CONTRACT_VERSION_WITH_CAR = 2


@dataclass(frozen=True)
class Dataset:
    """1つの履歴ファイルが扱うローン種別の定義。"""

    key: str
    label: str
    table_key: str
    contract_field: str
    rate_key_map: dict
    rate_type_order: list
    rate_type_labels: dict
    history_path: Path


def expected_rate_contract_metadata() -> dict:
    """住宅ローンのみを扱う旧契約（version 1）を返す。"""
    return {
        "version": REPORT_RATE_CONTRACT_VERSION,
        "loan_rate_fields": list(RATE_KEY_MAP),
    }


def expected_rate_contract_metadata_with_car() -> dict:
    """マイカーローンを含む契約（version 2）を返す。"""
    return {
        "version": REPORT_RATE_CONTRACT_VERSION_WITH_CAR,
        "loan_rate_fields": list(RATE_KEY_MAP),
        "car_loan_rate_fields": list(CAR_RATE_KEY_MAP),
    }


def supported_rate_contracts() -> list:
    """受け入れ可能な契約を新しい順に返す。"""
    return [expected_rate_contract_metadata_with_car(), expected_rate_contract_metadata()]

# 上流の報告自動化ツールが渡す商品名が、実サイト上の区分を表さない場合の表示名。
# 上流の商品名をそのまま出すと表示が不都合になるものを、履歴へ書く直前に置き換える。
# 上流の名称は金利抽出の商品名照合にも使うため、上流は変えずここで吸収する。
# - 北海道労働金庫: 定額型・定率型で金利体系が別なのに、上流の名称では見分けられない。
# - 北洋銀行: 上流は「α（定額型）」だが、公式サイトに「定額型」の語はなく
#   金利一覧・商品ページとも「ほくようスペシャル住宅ローンα」（2026-08-29に実ページで確認）。
#   対になる「α定率型」は括弧を使わないため、括弧の有無も揃う。
PRODUCT_NAME_OVERRIDES = {
    ("hokkaido_rokin", "rokin_jutaku"): "住宅ローン（定額型）",
    ("hokkaido_rokin", "rokin_smile"): "すまいる上手（定率型）",
    ("northpacific", "northpacific_teigaku"): "ほくようスペシャル住宅ローンα",
}


def resolve_product_name(bank_id: str, product_id: str, product_name: str) -> str:
    """履歴へ保存する商品名を返す。上書き対象なら固定の表示名を使う。"""
    return PRODUCT_NAME_OVERRIDES.get((bank_id, product_id), product_name)


def clean_display_name(value) -> str | None:
    """上流の表示名を、空でない文字列のときだけ採用する（null・空欄・数値は None）。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def is_http_url(value) -> bool:
    """HTTP(S)の絶対URLか。画面側の safeUrl に頼らず、保存前にも絞る（多重防御）。"""
    if not isinstance(value, str) or not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


DEFAULT_LABELS = {
    "variable": "変動",
    "fixed_3y": "固定3年",
    "fixed_5y": "固定5年",
    "fixed_10y": "固定10年",
}
DEFAULT_ORDER = ["variable", "fixed_3y", "fixed_5y", "fixed_10y"]
CAR_DEFAULT_LABELS = {
    "variable": "変動",
    "fixed": "固定",
}
CAR_DEFAULT_ORDER = ["variable", "fixed"]
MAX_RATE_PERCENT = 100.0
# 取り込み時だけに使う、もっともらしい金利の範囲（%）。外れた項目はその項目だけ保留する（再レビュー #5）。
# MAX_RATE_PERCENT は履歴全体の検証（validate_history）と共用なので、こちらとは分けて変えない。
IMPORT_MIN_RATE_PERCENT = 0.1
IMPORT_MAX_RATE_PERCENT = 15.0
# 前回の値からの急な変化。金利調査ツールの get_rate_anomaly_reason と同じ基準
# （差が 3.0% を超えるか、10 倍以上・1/10 以下）。北洋の +0.25% のような正当な改定は通る。
IMPORT_MAX_RATE_JUMP = 3.0
IMPORT_MAX_RATE_RATIO = 10.0
# 保証料注記の上限文字数。表の1セルに収める前提で、長文が来ても画面を壊さない。
MAX_GUARANTEE_NOTE_CHARS = 80
SCHEMA_VERSION = 2

HOUSING_DATASET = Dataset(
    key="housing",
    label="住宅ローン金利",
    table_key="loan_table",
    contract_field="loan_rate_fields",
    rate_key_map=RATE_KEY_MAP,
    rate_type_order=DEFAULT_ORDER,
    rate_type_labels=DEFAULT_LABELS,
    history_path=HISTORY_PATH,
)
CAR_DATASET = Dataset(
    key="car",
    label="マイカーローン金利",
    table_key="car_loan_table",
    contract_field="car_loan_rate_fields",
    rate_key_map=CAR_RATE_KEY_MAP,
    rate_type_order=CAR_DEFAULT_ORDER,
    rate_type_labels=CAR_DEFAULT_LABELS,
    history_path=CAR_HISTORY_PATH,
)
DATASETS = {dataset.key: dataset for dataset in (HOUSING_DATASET, CAR_DATASET)}


# 「取り込めた分は公開してよいが、最後に赤で知らせる」ときの終了コード。workflow はこのコードの
# ときも取り込めた分を公開し、最後のステップで失敗にする（本人決定 2026-10-13）。使う場面は 2 つ。
#   - 約束（rate_contract）にある表のどれかが空で、残りの表だけ取り込んだ（再レビュー #6）
#   - 公開用データの調査日が、期待される最新の調査日より古いまま止まっている（再レビュー G-3）
#   - 同じ日の取り直しで、ある種別を前の版のまま残し、ほかの種別だけ取り込んだ（再レビュー G23）
EXIT_PARTIAL = 2
# 同じ日の取り直しで、どの種別も取り込まなかった（前の版のまま残した）ときの終了コード。
# workflow は 2 以外の 0 でない値ですぐ赤にし、公開しない（再レビュー G23・本人決定 2026-10-19）。
EXIT_KEPT_PREVIOUS = 3
# 同じ日の取り直しで、前の版に値があった項目のうち新しい版でも取れた割合がこれ未満なら、
# その種別は置き換えず前の版のまま残す（本人決定 2026-10-19 Q5）。サイト障害の日に、
# 07:00 に取れていた正しい値まで消えるのを防ぐため。
SAME_DAY_MIN_CONFIRMED_RATIO = 0.7
# 公開用データが古いままでも、期待される調査日のこの時刻（JST）までは警告だけにする。
# 金利調査ツールの控えが遅れて動く日でも、取り込みの控え（月曜 11:00・1〜7日 12:00）で拾えるため。
STALE_FEED_ERROR_HOUR_JST = 11
MAX_MANUAL_LOCK_REASON_CHARS = 200
# 公開用データの版（作った時刻）が今より先でも許す幅（時計のずれ）
FEED_VERSION_CLOCK_SKEW_MINUTES = 10


def report_has_dataset(report_data: dict, dataset: Dataset) -> bool:
    """入力レポートの約束（rate_contract）が、このローン種別を含むかを返す。

    表が空かどうかは見ない。約束にあるのに表が空の回を「対象外」として黙って
    素通りさせず、report_table_is_empty で拾って赤で知らせるため。
    """
    if not isinstance(report_data, dict):
        return False
    contract = report_data.get("rate_contract")
    return isinstance(contract, dict) and dataset.contract_field in contract


def report_table_is_empty(report_data: dict, dataset: Dataset) -> bool:
    """このローン種別の表が無いか空の配列なら True。"""
    table = report_data.get(dataset.table_key) if isinstance(report_data, dict) else None
    return not table


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


JST = timezone(timedelta(hours=9))


def today_jst() -> str:
    return datetime.now(JST).date().isoformat()


def reject_future_date(survey_date: str, today: str | None = None) -> None:
    """未来の調査日を拒否する。

    履歴は「最新日より古い日付」を拒否するため、未来日が一度入ると、以後の正しい週次更新が
    すべて止まる（2026-10-07 監査 W5。起動用の鍵が漏れた場合の止め方として悪用できた）。
    """
    today = today or today_jst()
    if survey_date > today:
        raise ValueError(f"調査日 {survey_date} は未来の日付のため更新できません（今日は {today}）。")


def validate_id(value, field_name: str) -> str:
    """機関ID・商品IDが空でない文字列であることを確認する。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} は空でない文字列にしてください。")
    return value.strip()


def normalize_guarantee_note(value) -> str:
    """保証料の注記を、表示に耐える短い1行の文字列へ整える。

    上流の設定ファイル由来の自由文なので、型と長さだけをここで確定させる。
    改行やタブが混ざると表の1セルが崩れるため空白に潰す。
    """
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text[:MAX_GUARANTEE_NOTE_CHARS]


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


def import_hold_reason(value: float, previous) -> str | None:
    """取り込むと危ない値なら保留の理由を返す。前回の値（previous）が無ければ範囲だけを見る。"""
    if not IMPORT_MIN_RATE_PERCENT <= value <= IMPORT_MAX_RATE_PERCENT:
        return f"{IMPORT_MIN_RATE_PERCENT:g}〜{IMPORT_MAX_RATE_PERCENT:g}% の範囲外"
    if not isinstance(previous, (int, float)) or isinstance(previous, bool) or previous <= 0:
        return None
    ratio = value / previous
    if ratio >= IMPORT_MAX_RATE_RATIO or ratio <= 1 / IMPORT_MAX_RATE_RATIO:
        return f"前回 {previous:g}% の約 {ratio:.2g} 倍"
    # 2.2 → 5.2 のような差ちょうど 3.0 が小数の誤差で保留側に倒れないよう丸めて比べる
    if round(abs(value - previous), 6) > IMPORT_MAX_RATE_JUMP:
        return f"前回 {previous:g}% との差が {abs(value - previous):.3f}%"
    return None


def log_safe(value, limit: int = 80) -> str:
    """公開用データ由来の文字列を Actions のログ 1 行に載せられる形にする。

    改行などの制御文字を残すと、次の行が ::error:: などのワークフローコマンドとして
    解釈されてしまう（他リポジトリ由来の値なので信用しない）。
    """
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))
    return text if len(text) <= limit else text[:limit] + "…"


def same_host(old_url, new_url) -> bool:
    """2 つの URL のホスト名が同じか（大文字小文字は区別しない）。"""
    return (urlparse(old_url).hostname or "").lower() == (urlparse(new_url).hostname or "").lower()


def parse_feed_version(value):
    """公開用データの版（feed_generated_at）を datetime にする。時差の無い値・読めない値は None。"""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def validate_report_data(report_data: dict, dataset: Dataset = HOUSING_DATASET) -> None:
    """入力レポートのローン行を、履歴変更前に検証する。"""
    if not isinstance(report_data, dict):
        raise ValueError("入力JSONのルートはオブジェクトにしてください。")
    contract = report_data.get("rate_contract")
    supported = supported_rate_contracts()
    if contract not in supported:
        raise ValueError(
            "rate_contract がFinPulseの対応契約と一致しません。"
            f"対応契約={supported!r} / 入力値={contract!r}"
        )
    if dataset.contract_field not in contract:
        raise ValueError(
            f"rate_contract に {dataset.contract_field} がないため"
            f"{dataset.label}は取り込めません。"
        )
    if "feed_generated_at" in report_data:
        version = parse_feed_version(report_data["feed_generated_at"])
        if version is None:
            raise ValueError("feed_generated_at は時差付きの ISO 形式の日時にしてください。")
        # 未来の版を受け付けると、その日の取り込み直し（新しい版の判定）がずっと止まる
        if version > datetime.now(JST) + timedelta(minutes=FEED_VERSION_CLOCK_SKEW_MINUTES):
            raise ValueError(f"feed_generated_at が未来の日時です: {report_data['feed_generated_at']}")
    table_key = dataset.table_key
    loans = report_data.get(table_key)
    if not isinstance(loans, list) or not loans:
        raise ValueError(f"{table_key} は1件以上の配列にしてください。")

    seen_products = set()
    for index, loan in enumerate(loans, start=1):
        if not isinstance(loan, dict):
            raise ValueError(f"{table_key}[{index}] はオブジェクトにしてください。")
        bank_id = validate_id(loan.get("bank_id"), f"{table_key}[{index}].bank_id")
        product_id = validate_id(loan.get("product_id"), f"{table_key}[{index}].product_id")
        product_key = (bank_id, product_id)
        if product_key in seen_products:
            raise ValueError(f"機関IDと商品IDが重複しています: {bank_id}/{product_id}")
        seen_products.add(product_key)

        if "manual" in loan and not isinstance(loan["manual"], bool):
            raise ValueError(f"{table_key}[{index}].manual は真偽値にしてください。")

        for loan_key in dataset.rate_key_map:
            value = loan.get(loan_key)
            if value is not None:
                validate_rate(value, f"{table_key}[{index}].{loan_key}")


def migrate_history(history: dict, dataset: Dataset = HOUSING_DATASET) -> None:
    """旧形式の変更時点履歴を、調査日ごとの観測形式へ移行する。"""
    history.setdefault("rate_type_order", list(dataset.rate_type_order))
    history.setdefault("rate_type_labels", dict(dataset.rate_type_labels))
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

    rate_type_order = history.get("rate_type_order")
    if (
        not isinstance(rate_type_order, list)
        or not rate_type_order
        or not all(isinstance(value, str) and value for value in rate_type_order)
    ):
        raise ValueError("rate_type_order は空でない文字列の配列にしてください。")
    if len(set(rate_type_order)) != len(rate_type_order):
        raise ValueError("rate_type_order に重複があります。")

    rate_type_labels = history.get("rate_type_labels")
    if not isinstance(rate_type_labels, dict) or not all(
        isinstance(key, str)
        and key
        and isinstance(value, str)
        and value
        for key, value in rate_type_labels.items()
    ):
        raise ValueError("rate_type_labels は空でない文字列の辞書にしてください。")
    if set(rate_type_labels) != set(rate_type_order):
        raise ValueError("rate_type_labels のキーは rate_type_order と一致させてください。")

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
        if rate_type not in rate_type_order:
            raise ValueError(f"rows[{index}].rate_type が不正です: {rate_type!r}")
        for name_key in ("bank_name", "product_name"):
            if clean_display_name(row.get(name_key)) is None:
                raise ValueError(f"rows[{index}].{name_key} は空でない文字列にしてください。")
        row_url = row.get("url")
        if row_url is not None and not is_http_url(row_url):
            raise ValueError(f"rows[{index}].url はHTTP(S)の絶対URLかnullにしてください。")
        guarantee_note = row.get("guarantee_note")
        if guarantee_note is not None and (
            not isinstance(guarantee_note, str)
            or not guarantee_note.strip()
            or len(guarantee_note) > MAX_GUARANTEE_NOTE_CHARS
        ):
            raise ValueError(
                f"rows[{index}].guarantee_note は{MAX_GUARANTEE_NOTE_CHARS}文字以内の"
                "空でない文字列にしてください。"
            )
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

    if "last_feed_generated_at" in history and parse_feed_version(history["last_feed_generated_at"]) is None:
        raise ValueError("last_feed_generated_at は時差付きの ISO 形式の日時にしてください。")
    manual_lock = history.get("manual_lock")
    if manual_lock is not None:
        if not isinstance(manual_lock, dict):
            raise ValueError('manual_lock は {"YYYY-MM-DD": "理由"} の形にしてください。')
        for locked_date, reason in manual_lock.items():
            if not isinstance(locked_date, str) or normalize_date(locked_date) != locked_date:
                raise ValueError(f"manual_lock の日付は YYYY-MM-DD にしてください: {locked_date!r}")
            if (
                not isinstance(reason, str)
                or not reason.strip()
                or len(reason) > MAX_MANUAL_LOCK_REASON_CHARS
            ):
                raise ValueError(
                    f"manual_lock[{locked_date}] の理由は{MAX_MANUAL_LOCK_REASON_CHARS}文字以内の"
                    "空でない文字列にしてください。"
                )

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


def load_history(dataset: Dataset = HOUSING_DATASET) -> dict:
    if dataset.history_path.exists():
        with open(dataset.history_path, encoding="utf-8") as f:
            data = json.load(f)
        migrate_history(data, dataset)
        return data
    return {
        "schema_version": SCHEMA_VERSION,
        "rate_type_order": list(dataset.rate_type_order),
        "rate_type_labels": dict(dataset.rate_type_labels),
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
    dataset: Dataset = HOUSING_DATASET,
) -> dict:
    """report_data のローン金利を履歴へ反映する。返り値は変更サマリー。"""
    survey_date = normalize_date(survey_date)
    reject_future_date(survey_date)
    migrate_history(history, dataset)
    validate_history(history)
    validate_report_data(report_data, dataset)
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

    added_survey_date = survey_date not in history["observation_dates"]
    # 最新の調査日を新しい版で取り込み直すときは、その日の列を置き換える（再レビュー G23・
    # 本人決定 2026-10-19）。新しい版で空欄・保留の項目と、載っていない商品の当日の値は消す。
    # 過去日の履歴は残す。取れた割合が低い回は、前の版のまま残すため写しを取っておく。
    same_day_reimport = not added_survey_date and survey_date == latest_date
    before_reimport = copy.deepcopy(history) if same_day_reimport else None
    previous_same_day = (
        {
            row_key(r.get("bank_id"), r.get("product_id"), r.get("rate_type"))
            for r in history["rows"]
            if any(entry.get("observed_on") == survey_date for entry in r.get("history", []))
        }
        if same_day_reimport
        else set()
    )
    imported_keys = set()
    if added_survey_date:
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
        # その項目だけ取り込まなかった値と、取り込んだが変えなかった名前・URL（Actions の警告に出す）
        "held": [],
        "warnings": [],
    }

    for loan in report_data.get(dataset.table_key, []):
        # 手動入力の機関（サイトから取得できずconfig/manual_rates.jsonに手書きした値）は
        # 履歴に入れない。毎週同じ値がコピーされるだけで「その日に確認した金利」ではなく、
        # 自動取得分と並べると確認済みに見えてしまうため。金利調査レポート本体でも
        # ローン表から除外しており、それに揃える。
        if loan.get("manual"):
            continue
        bank_id = validate_id(loan.get("bank_id"), "bank_id")
        product_id = validate_id(loan.get("product_id"), "product_id")
        for loan_key, rate_type in dataset.rate_key_map.items():
            value = loan.get(loan_key)
            if value is None:
                continue
            value = validate_rate(value, loan_key)
            key = row_key(bank_id, product_id, rate_type)
            row = index.get(key)
            loan_url = loan.get("url") if is_http_url(loan.get("url")) else None
            previous_entry = next(
                (
                    entry for entry in (row or {}).get("history", [])
                    if entry.get("observed_on", "") < survey_date
                ),
                None,
            )
            hold_reason = import_hold_reason(value, (previous_entry or {}).get("rate"))
            if hold_reason:
                # その項目だけ保留する（前回の値は残る。ほかの項目と種別の取り込みは止めない）
                summary["held"].append(f"{bank_id}/{product_id} {rate_type}={value:g}（{hold_reason}）")
                continue
            imported_keys.add(key)
            if row is None:
                # 新規の(機関×商品×種別)。履歴を1件で作る。名前が無い新商品は表に
                # 名前の空いた行を作ってしまうため、その回の取り込みを止めて気づかせる。
                bank_name = clean_display_name(loan.get("bank_name"))
                product_name = clean_display_name(
                    resolve_product_name(bank_id, product_id, loan.get("product_name"))
                )
                if bank_name is None or product_name is None:
                    raise ValueError(
                        f"新しい商品 {bank_id}/{product_id} に機関名・商品名がありません。"
                    )
                row = {
                    "bank_id": bank_id,
                    "bank_name": bank_name,
                    "product_id": product_id,
                    "product_name": product_name,
                    "url": loan_url,
                    "rate_type": rate_type,
                    "history": [{"rate": value, "observed_on": survey_date}],
                }
                guarantee_note = normalize_guarantee_note(loan.get("guarantee_note"))
                if guarantee_note:
                    row["guarantee_note"] = guarantee_note
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
                # 上流の名前が欠けた週は前回の名前を保つ（1件の欠けで全体を止めない）。
                # 名前が変わろうとしたときも前回の名前を保ち、警告だけ出す（再レビュー #5・本人決定
                # 2026-10-13）。本当に名前が変わったときは、警告を見て履歴 JSON を手で直す。
                for name_key, incoming in (
                    ("bank_name", clean_display_name(loan.get("bank_name"))),
                    (
                        "product_name",
                        clean_display_name(
                            resolve_product_name(bank_id, product_id, loan.get("product_name"))
                        ),
                    ),
                ):
                    if incoming is None or incoming == row.get(name_key):
                        continue
                    if row.get(name_key):
                        message = (
                            f"{bank_id}/{product_id} の {name_key} が「{log_safe(row[name_key])}」→"
                            f"「{log_safe(incoming)}」に変わろうとしたため、前回の名前のままにしました"
                        )
                        if message not in summary["warnings"]:
                            summary["warnings"].append(message)
                    else:
                        row[name_key] = incoming
                # リンク先のホストが変わろうとしたときは旧 URL を残して警告する（本人決定）。
                # 許可リストは持たない（金利調査ツールの banks.json との二重管理になるため）
                if loan_url and row.get("url") and not same_host(row["url"], loan_url):
                    message = (
                        f"{bank_id}/{product_id} のリンク先のホストが "
                        f"{urlparse(row['url']).hostname} → {urlparse(loan_url).hostname} に"
                        "変わろうとしたため、旧 URL のままにしました"
                    )
                    if message not in summary["warnings"]:
                        summary["warnings"].append(message)
                elif loan_url:
                    row["url"] = loan_url
                # 保証料の条件が消えた週は注記も消す（古い断り書きを残さない）
                guarantee_note = normalize_guarantee_note(loan.get("guarantee_note"))
                if guarantee_note:
                    row["guarantee_note"] = guarantee_note
                else:
                    row.pop("guarantee_note", None)

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

    if same_day_reimport:
        shortfall = same_day_shortfall(previous_same_day, imported_keys)
        if shortfall:
            # 置き換えず、前の版のまま残す（呼び出し側は書き込まずに赤で知らせる）
            history.clear()
            history.update(before_reimport)
            summary["kept_previous"] = shortfall
            return summary
        summary["cleared"] = clear_unconfirmed_same_day(history, survey_date, imported_keys)

    if added_survey_date and summary["held"] and not any(
        entry.get("observed_on") == survey_date
        for row in history["rows"]
        for entry in row.get("history", [])
    ):
        # 保留があり、1 項目も取り込めなかった日は、調査日を進めない（値が丸ごとおかしい回）。
        # 進めると、公開ページに値の空いた日付の列ができる。単に値が取れなかっただけの日は
        # 従来どおり調査日を残す（画面で「未確認」と出す設計）
        history["observation_dates"].remove(survey_date)
    if history["observation_dates"]:
        history["generated_at"] = history["observation_dates"][0]
    else:
        history.pop("generated_at", None)
    if survey_date == history.get("generated_at"):
        # 最新の調査日を書いた公開用データの版を残す。同じ日に新しい版が来たら取り込み直す
        # 判定（import_decision）に使う。版の無いデータで書いたときは、古い版を残さない。
        # 保留した項目があった回も版を残す（再レビュー G23・本人決定 2026-10-19）。残さないと
        # import_decision が「版の記録が無い日は手直しを守る」として同じ日の新しい版を断り、
        # 報告側の検算も「公開中の版 なし」で未照合になるため。直った新しい版は版の比較で取り込み直せる。
        version = report_data.get("feed_generated_at")
        if version:
            history["last_feed_generated_at"] = version
        else:
            history.pop("last_feed_generated_at", None)
    history["is_demo"] = False
    extend_automated_source_period(history, survey_date)
    validate_history(history)
    return summary


def same_day_shortfall(previous_keys: set, imported_keys: set):
    """同じ日の取り直しで、前の版に値があった項目のうち新しい版で取れた割合が足りなければ理由を返す。"""
    if not previous_keys:
        return None
    confirmed = len(previous_keys & imported_keys)
    if confirmed / len(previous_keys) >= SAME_DAY_MIN_CONFIRMED_RATIO:
        return None
    return (
        f"前の版で値があった {len(previous_keys)} 項目のうち、新しい版で取れたのは {confirmed} 項目だけ"
        f"（{SAME_DAY_MIN_CONFIRMED_RATIO:.0%} 未満）"
    )


def clear_unconfirmed_same_day(history: dict, survey_date: str, imported_keys: set) -> int:
    """新しい版で確かめられなかった項目の当日の値を消し、消した数を返す。その日だけの行は行ごと消す。"""
    cleared = 0
    kept_rows = []
    for row in history["rows"]:
        key = row_key(row.get("bank_id"), row.get("product_id"), row.get("rate_type"))
        entries = row.get("history", [])
        if key not in imported_keys and any(e.get("observed_on") == survey_date for e in entries):
            row["history"] = [e for e in entries if e.get("observed_on") != survey_date]
            cleared += 1
            if not row["history"]:
                continue
        kept_rows.append(row)
    history["rows"] = kept_rows
    return cleared


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


def prepare_dataset_update(
    report_data: dict,
    survey_date: str,
    dataset: Dataset,
    *,
    allow_backfill: bool = False,
) -> tuple[dict, dict]:
    """1つのローン種別の履歴をメモリ上で更新・検証し、(履歴, 変更サマリー) を返す。
    ファイルには書かない。複数種別を扱う main は全種別の準備が通ってから書き込む。"""
    history = load_history(dataset)
    summary = update_history(
        history,
        report_data,
        survey_date,
        allow_backfill=allow_backfill,
        dataset=dataset,
    )
    return history, summary


def update_dataset_file(
    report_data: dict,
    survey_date: str,
    dataset: Dataset,
    *,
    allow_backfill: bool = False,
) -> dict:
    """1つのローン種別の履歴ファイルを更新し、変更サマリーを返す。"""
    history, summary = prepare_dataset_update(
        report_data, survey_date, dataset, allow_backfill=allow_backfill
    )
    write_history_atomic(history, dataset.history_path)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="金利履歴JSONを更新する")
    parser.add_argument("report_data", help="報告自動化ツールの report_data_*.json のパス")
    parser.add_argument("--date", help="調査日(YYYY-MM-DD)。JSONに survey_date が無い場合に指定")
    parser.add_argument(
        "--allow-backfill",
        action="store_true",
        help="既存の最新日より前の調査日を追加する（過去データ投入専用）",
    )
    parser.add_argument(
        "--only-newer",
        action="store_true",
        help=(
            "既存の最新日より新しい調査日のときだけ更新する（同じ日・古い日は何もせず終える）。"
            "週次の自動取り込み用。取り込み済みの週を、あとから手で直した値ごと上書きしないため"
        ),
    )
    parser.add_argument(
        "--dataset",
        choices=sorted(DATASETS) + ["all"],
        default="all",
        help="更新するローン種別。既定はレポートに含まれるものすべて",
    )
    args = parser.parse_args()

    with open(args.report_data, encoding="utf-8") as f:
        report_data = json.load(f)

    raw_date = args.date or report_data.get("survey_date", "")
    survey_date = normalize_date(raw_date)

    empty = []
    if args.dataset == "all":
        declared = [
            dataset
            for dataset in DATASETS.values()
            if report_has_dataset(report_data, dataset)
        ]
        # 約束にあるのに表が空の種別は、その種別だけ取り込まずに最後で赤にする
        # （上流の収集が一部だけ失敗した回でも、取れた種別の更新は止めない）。
        empty = [dataset for dataset in declared if report_table_is_empty(report_data, dataset)]
        targets = [dataset for dataset in declared if dataset not in empty]
        if not targets:
            raise SystemExit(
                "入力レポートに取り込めるローン表がありません（loan_table / car_loan_table）。"
            )
    else:
        targets = [DATASETS[args.dataset]]

    reject_future_date(survey_date)
    written, kept = update_targets(args, report_data, survey_date, targets)
    if kept and not written:
        sys.exit(EXIT_KEPT_PREVIOUS)
    alert = bool(empty) or bool(kept)
    if args.only_newer:
        # 週次の自動取り込みのときだけ、公開用データの鮮度を見る（手動の取り込み直しは対象外）
        stale = stale_feed_message(survey_date, datetime.now(JST))
        if stale:
            level, text = stale
            print(f"::{level} title=公開用の金利データが古いままです::{text}")
            alert = alert or level == "error"
    if empty:
        for dataset in empty:
            print(
                f"::error title={dataset.label}を取り込めません::"
                f"公開用の金利データの {dataset.table_key} が空です。"
                f"{dataset.label}の履歴は前回のまま残し、ほかの種別だけ更新しました。"
                "金利調査ツール側の収集結果を確かめてください。"
            )
    if alert:
        sys.exit(EXIT_PARTIAL)


def import_decision(history: dict, survey_date: str, feed_version) -> tuple[bool, str]:
    """週次の自動取り込み（--only-newer）で、この種別を取り込むか。(取り込むか, 理由) を返す。

    調査日が新しければ取り込む。同じ調査日なら、公開用データの版（feed_generated_at）が
    履歴に記録した版（last_feed_generated_at）より新しく、その日に手直しの印（manual_lock）が
    無いときだけ取り込み直す（再レビュー G-2・本人決定 2026-10-13）。月初の 09:00 取り直しの
    ような同じ日の作り直しを反映しつつ、手で直した日は守る。
    履歴に版の記録が無いときも同じ日は上書きしない（版の記録を始める前に手で直した値を守るため）。
    """
    latest = latest_history_date(history)
    if not latest or survey_date > latest:
        return True, f"調査日 {survey_date} は未取り込みのため更新します。"
    if survey_date < latest:
        return False, f"調査日 {survey_date} は取り込み済み（最新 {latest}）のため更新しません。"
    lock_reason = (history.get("manual_lock") or {}).get(survey_date)
    if lock_reason:
        return False, (
            f"調査日 {survey_date} は取り込み済みで、手直しの印（manual_lock: {lock_reason}）が"
            "あるため更新しません。"
        )
    new_at = parse_feed_version(feed_version)
    old_at = parse_feed_version(history.get("last_feed_generated_at"))
    if new_at is None:
        return False, f"調査日 {survey_date} は取り込み済み（公開用データに版が無い）のため更新しません。"
    if old_at is None:
        return False, (
            f"調査日 {survey_date} は取り込み済み（履歴に版の記録が無いので、手直しを守る）"
            "のため更新しません。"
        )
    if new_at > old_at:
        return True, (
            f"調査日 {survey_date} の新しい版（{feed_version}・前回 {history['last_feed_generated_at']}）"
            "のため取り込み直します。"
        )
    return False, f"調査日 {survey_date} は取り込み済み（同じか古い版）のため更新しません。"


def expected_latest_survey_date(now: datetime):
    """今日（JST）以前で、金利調査ツールが最後に調査したはずの日（直近の月曜か、今月の最初の営業日）。"""
    today = now.astimezone(JST).date()
    monday = today - timedelta(days=today.weekday())
    first = first_business_day(today.year, today.month)
    return max(monday, first) if first <= today else monday


def stale_feed_message(survey_date: str, now: datetime):
    """公開用データが古いままなら (注記の種類 "warning"/"error", 文) を返す。新しければ None。"""
    expected = expected_latest_survey_date(now)
    if survey_date >= expected.isoformat():
        return None
    deadline = datetime(
        expected.year, expected.month, expected.day, STALE_FEED_ERROR_HOUR_JST, tzinfo=JST
    )
    level = "error" if now.astimezone(JST) >= deadline else "warning"
    text = (
        f"公開用の金利データの調査日が {survey_date} のままです"
        f"（期待される最新の調査日 {expected.isoformat()}）。"
        "金利調査ツールの週次・月初の回が失敗していないか、rate-feed ブランチが更新されたかを確かめてください。"
    )
    if level == "warning":
        text += (
            f"（{expected.isoformat()} {STALE_FEED_ERROR_HOUR_JST}:00 までは警告だけ。"
            "後の控えで新しいデータを取り込めれば消えます）"
        )
    return level, text


def update_targets(args, report_data: dict, survey_date: str, targets: list) -> None:
    """targets の種別を更新して書き込む（--only-newer なら import_decision で選ぶ）。"""
    if args.only_newer:
        newer = []
        for dataset in targets:
            should_import, reason = import_decision(
                load_history(dataset), survey_date, report_data.get("feed_generated_at")
            )
            print(f"{dataset.label}: {reason}")
            if should_import:
                newer.append(dataset)
        targets = newer
        if not targets:
            return 0, []

    # 全種別をメモリ上で更新・検証してから書き込む。マイカー側の入力が不正な回に
    # 住宅ローン側だけが新しい調査日になる部分更新を防ぐ（書き込み自体の途中失敗は対象外。
    # 本番は上流 workflow が更新成功時だけ push するため、作業環境に残るだけで公開されない）。
    prepared = [
        (
            dataset,
            *prepare_dataset_update(
                report_data,
                survey_date,
                dataset,
                allow_backfill=args.allow_backfill,
            ),
        )
        for dataset in targets
    ]
    written = 0
    kept = []
    for dataset, history, summary in prepared:
        if summary.get("kept_previous"):
            kept.append(dataset)
            print(
                f"::error title={dataset.label}は前の版のまま残しました::"
                f"同じ調査日 {survey_date} の取り直しで、{summary['kept_previous']}でした。"
                "サイト障害などで正しい値まで消えないよう、この種別は置き換えていません。"
                + ("保留: " + " / ".join(summary["held"]) if summary["held"] else "")
            )
            continue
        write_history_atomic(history, dataset.history_path)
        written += 1
        print(
            f"{dataset.label}の履歴を更新しました（調査日 {survey_date}）: "
            f"{dataset.history_path}"
        )
        if summary["replaced_demo"]:
            print("  デモ履歴を削除し、実データで初期化しました")
        print(
            f"  新規行 {summary['added_rows']} / 金利変更 {summary['changed']} / "
            f"据え置き {summary['unchanged']}"
        )
        if summary["held"]:
            print(
                f"::warning title={dataset.label}で保留した金利があります::"
                f"範囲外や急な変化のため {len(summary['held'])} 項目を取り込みませんでした"
                "（その日の値は空欄。同じ日の取り直しでは前の版の当日の値も消す。"
                "1 項目も取り込めなかった日は調査日も進めない）。"
                "公式サイトの値を確かめてください: " + " / ".join(summary["held"])
            )
        if summary.get("cleared"):
            print(
                f"  同じ日の取り直しで、新しい版で確かめられなかった {summary['cleared']} 項目の"
                "当日の値を消しました"
            )
        for message in summary["warnings"]:
            print(f"::warning title={dataset.label}の名前・リンクの変更を保留しました::{message}")
    return written, kept


if __name__ == "__main__":
    main()
