"""週次ワークフローを今回動かすかを判定する（GitHub Actions の guard ジョブから呼ぶ）。

本命の起動は外部スケジューラ（cron-job.org）から毎週月曜 08:05 JST と毎月1〜7日 09:20 JST の
workflow_dispatch、控えは GitHub の schedule（月曜 09:00・1〜7日 10:30 JST）。GitHub の schedule は
1〜3時間遅れるうえ発火しない回もあるため、定刻は外部に任せ、schedule は外部が止まった回の保険にする。
月初の回は「月の最初の銀行営業日」だけ動かす（1日が土日祝なら翌営業日。本人依頼 2026-10-08）。
cron-job.org の月初ジョブは本文で monthly=true を渡し、1〜7日のうち最初の営業日以外はここで止める。
判定は「今日（JST）」単位なので、月初の営業日が月曜に重なった日も1回だけ動く。

判定（上から順に最初に当てはまったもの）:
  1. check_only=true   → 動かさない（cron-job.org からの接続テスト。収集・送信・公開をしない）
  2. force=true        → 動かす（手動の再実行）
  3. 月初の回で、今日が月の最初の営業日でない → 動かさない
  4. 今日（JST）すでに「収集と公開までやり遂げた」実行がある → 動かさない（本命が済んだ週の控えを止める）
  5. それ以外          → 動かす
「やり遂げた」は実行全体の成否ではなく、収集ジョブと公開ジョブの両方が success かで見る。
接続テストや省略した実行も、ジョブが skipped のまま実行全体は success で終わるため、
全体の成否で見ると同じ日の本命まで止めてしまう。
一覧やジョブの取得に失敗したら「動かす」に倒す。控えが止まって配信が抜けるより、
同日に取り直すほうが害が小さい（メールは同日の冪等キーで2通目が止まる）。

標準ライブラリだけで書く（guard ジョブでは依存をインストールしない）。
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
API_ROOT = "https://api.github.com"
WORKFLOW_FILE = "weekly-news-report.yml"
# 「やり遂げた」と数えるのに success が要るジョブ（workflow の jobs のキー名）
REQUIRED_JOBS = ("collect-and-report", "publish-results")
REQUEST_TIMEOUT_SECONDS = 20
# 月初の回の控え（workflow の schedule と同じ文字列。github.event.schedule で届く）
MONTHLY_CRON = "30 1 1-7 * *"
# 銀行の休業日になる祝日のうち、月の1〜7日に来うるもの（元日・憲法記念日・みどりの日・こどもの日・文化の日）
EARLY_MONTH_HOLIDAYS = ((1, 1), (5, 3), (5, 4), (5, 5), (11, 3))
# 年末年始の銀行休業日
NEW_YEAR_CLOSED_DAYS = ((12, 31), (1, 1), (1, 2), (1, 3))


def jst_day_start_utc(now):
    """now（tz-aware）が属する JST の日の 00:00 を UTC で返す。"""
    jst_now = now.astimezone(JST)
    start = jst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def early_month_holidays(year):
    """その年の EARLY_MONTH_HOLIDAYS と、それが日曜に重なった時の振替休日を返す。"""
    holidays = {date(year, month, day) for month, day in EARLY_MONTH_HOLIDAYS}
    for holiday in sorted(holidays):
        if holiday.weekday() == 6:
            substitute = holiday + timedelta(days=1)
            while substitute in holidays:
                substitute += timedelta(days=1)
            holidays.add(substitute)
    return holidays


def is_bank_holiday(day):
    """銀行の休業日か（土日・祝日・年末年始 12/31〜1/3）。

    祝日は月の最初の営業日を決めるのに効くものだけを持つ。成人の日（1月第2月曜）のように
    8日以降にしか来ない祝日は、1〜7日のどこかに必ず営業日があるので判定に影響しない。
    即位の礼のような臨時の祝日は持たない（来たらその月だけ1日早く動く）。
    """
    if day.weekday() >= 5:
        return True
    if (day.month, day.day) in NEW_YEAR_CLOSED_DAYS:
        return True
    return day in early_month_holidays(day.year)


def first_business_day(year, month):
    """その月の最初の銀行営業日を返す（最も遅くて7日）。"""
    day = date(year, month, 1)
    while is_bank_holiday(day):
        day += timedelta(days=1)
    return day


def is_true(value):
    """workflow の inputs は文字列 "true"/"false" か空で渡るので、明示の true だけを真とする。"""
    return str(value).strip().lower() == "true"


def successful_runs_today(runs, current_run_id, day_start_utc):
    """今日（JST）作られ、全体が成功で終わった「今回以外」の実行を返す（新しい順のまま）。"""
    found = []
    for run in runs:
        if str(run.get("id")) == str(current_run_id):
            continue
        if run.get("conclusion") != "success":
            continue
        created_at = run.get("created_at")
        if not isinstance(created_at, str):
            continue
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created >= day_start_utc:
            found.append(run)
    return found


def did_real_work(jobs):
    """収集ジョブと公開ジョブがどちらも success なら True（skipped・失敗・欠けは False）。"""
    conclusions = {}
    for job in jobs:
        if isinstance(job, dict) and isinstance(job.get("name"), str):
            conclusions[job["name"]] = job.get("conclusion")
    return all(conclusions.get(name) == "success" for name in REQUIRED_JOBS)


def is_monthly_trigger(monthly_input, event_schedule):
    """月初の回の起動か（cron-job.org の monthly=true か、月初の控えの schedule）。"""
    return is_true(monthly_input) or str(event_schedule).strip() == MONTHLY_CRON


def decide(*, check_only, force, runs, current_run_id, now, fetch_jobs, monthly=False):
    """(動かすか, 理由) を返す。

    runs が None なら一覧の取得に失敗したことを表す。fetch_jobs(run_id) はその実行の
    ジョブ一覧を返し、取得に失敗したら None を返す。monthly は月初の回の起動かどうか。
    """
    if is_true(check_only):
        return False, "接続確認のみ（check_only）。収集・送信・公開は行いません。"
    if is_true(force):
        return True, "手動の強制実行（force）です。"
    if monthly:
        today = now.astimezone(JST).date()
        first = first_business_day(today.year, today.month)
        if today != first:
            return False, (
                f"月初の回は月の最初の営業日（{first.month}/{first.day}）だけ動きます。"
                "今日は対象外のため省略します。"
            )
    if runs is None:
        return True, "実行一覧を取得できなかったため、配信を抜かさないよう実行します。"
    day_start = jst_day_start_utc(now)
    for run in successful_runs_today(runs, current_run_id, day_start):
        jobs = fetch_jobs(run.get("id"))
        if jobs is None:
            # 確かめられない実行は数えない（止めるより動かすほうが害が小さい）
            continue
        if did_real_work(jobs):
            return False, (
                f"今日（JST）すでに収集と公開まで成功した実行があるため省略します"
                f"（run {run.get('id')}・{run.get('event')}・{run.get('created_at')}）。"
                "やり直す場合は手動実行で force にチェックを入れてください。"
            )
    return True, "今日（JST）まだ収集と公開まで成功した実行がないため実行します。"


def _get_json(url, token, opener):
    """GitHub API に GET して JSON を返す。通信・応答の失敗は None。"""
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "finpulse-weekly-guard",
        },
    )
    try:
        with opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"::warning::GitHub API の取得に失敗しました（{type(exc).__name__}）。")
        return None


def fetch_runs_since(repository, token, since_utc, *, opener=urllib.request.urlopen):
    """このワークフローの since_utc 以降に作られた実行一覧を返す。失敗時は None。"""
    since_text = since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    query = urllib.parse.urlencode({"created": f">={since_text}", "per_page": "100"})
    url = f"{API_ROOT}/repos/{repository}/actions/workflows/{WORKFLOW_FILE}/runs?{query}"
    payload = _get_json(url, token, opener)
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        print("::warning::実行一覧の応答形式が想定と違います。")
        return None
    return runs


def fetch_jobs(repository, token, run_id, *, opener=urllib.request.urlopen):
    """実行1件のジョブ一覧を返す。失敗時は None。"""
    try:
        run_number = int(run_id)
    except (TypeError, ValueError):
        return None
    url = f"{API_ROOT}/repos/{repository}/actions/runs/{run_number}/jobs?per_page=100"
    payload = _get_json(url, token, opener)
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        print(f"::warning::run {run_id} のジョブ一覧を確認できませんでした。")
        return None
    return jobs


def write_outputs(should_run, reason):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"should_run={'true' if should_run else 'false'}\n")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            verdict = "実行する" if should_run else "実行しない"
            summary.write(f"### 週次レポートの実行判定: {verdict}\n\n{reason}\n")


def main():
    now = datetime.now(timezone.utc)
    check_only = os.environ.get("INPUT_CHECK_ONLY", "")
    force = os.environ.get("INPUT_FORCE", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    runs = None
    if not is_true(check_only) and not is_true(force):
        if repository and token:
            runs = fetch_runs_since(repository, token, jst_day_start_utc(now))
        else:
            print("::warning::GITHUB_REPOSITORY または GITHUB_TOKEN がありません。")
    should_run, reason = decide(
        check_only=check_only,
        force=force,
        runs=runs,
        current_run_id=os.environ.get("GITHUB_RUN_ID", ""),
        now=now,
        fetch_jobs=lambda run_id: fetch_jobs(repository, token, run_id),
        monthly=is_monthly_trigger(
            os.environ.get("INPUT_MONTHLY", ""), os.environ.get("EVENT_SCHEDULE", "")
        ),
    )
    print(("::notice::" if not should_run else "") + reason)
    write_outputs(should_run, reason)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 判定の不具合で控えまで止めない
        print(f"::warning::判定処理で想定外のエラー（{type(exc).__name__}）。実行する側に倒します。")
        write_outputs(True, "判定処理のエラーにより実行します。")
        sys.exit(0)
