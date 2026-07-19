# HANDOFF.md — 現在地・残課題の正（SSoT）

このファイルは **Codex と Claude Code の両方が読み書きする、ライブな引き継ぎ状態の唯一の正**。
「いま何がどこまで進んでいて、次に何が残っているか」はここだけに書く（メモリや他ファイルに二重に書かない）。

- **規律・構成は `CLAUDE.md`**。**進め方とプロトコルは `AGENTS.md`**。**収集ロジック本体は `scripts/collect_and_send.py`**。
- 節目（意味のあるコミット）ごとに本ファイルを更新し、**コードと同じコミットに含める**。
- 更新時は下の「最終更新」を必ず書き換える（古さを検知できるように）。

**最終更新**: 2026-07-19 / 除外一覧を配信日から3ヶ月以内に限定＋相対日付補完を本番検証（更新者: Claude Code）

---

## 現在地

- 帯広エリア近隣7金融機関の新着情報を週次収集してメール送信する自動化ツール。Phase 3（週次自動化）まで稼働中。
- GitHub Actions が毎週月曜 05:00 JST に収集→フィルタ→Resend送信→`output/YYYY-MM-DD.md` を自動コミット。直近（06-22〜07-13）も7機関すべて取得成功で正常稼働。
- 相対日付補完（`infer_date_from_relative_text`）を本番検証済み: `output/2026-06-22.md` で十勝信組「今月のローン金利」が 2026-06-01＋補完注記付きで正しく通過。補完注記が付いたのはこの1件のみで他機関への誤補完なし。コード変更不要。
- 除外一覧の3ヶ月フィルタを追加（2026-07-19）: `format_report` で、配信日（today）から3ヶ月以上経過した除外項目は列挙しない（日付不明は fail-open で残す）。件数表示と「合計 除外」も表示ベースに修正。月末クランプ付き `date_n_months_ago()` を新設。通過セクションのロジックは不変。
- 未コミットの変更: なし（上記2点はコミット済み）。
- 検証: `python -m py_compile`（全 .py 構文OK）。lint/型/test 基盤は未導入。

## 残課題（優先度順）

1. **北洋の通過率が低い**（優先度低）: 投稿が企業向け中心で 金利/キャンペーン/お知らせ に当たりにくい。取得自体は正常。必要なら include キーワード調整（別タスク・先に「北洋で何を拾いたいか」の方針決めが必要）。

### 完了済み
- ~~相対日付補完の本番確認~~: 2026-07-19 検証完了。`output/2026-06-22.md` で意図どおり動作、誤補完なし。

## 環境メモ

- 秘密情報は `.env`（Git管理外）と GitHub Secrets に置く。チャットに貼らない。`RESEND_API_KEY`（送信用）、`ANTHROPIC_API_KEY`（Claude遅延生成・現状未消費）。
- 本番実行は GitHub Actions（`.github/workflows/weekly-news-report.yml`）。依存は workflow 内で `pip install anthropic requests beautifulsoup4 defusedxml`。手動実行は workflow_dispatch ボタン。
- push 先は **origin/main** のみ。
- pre-commit フックを**スキップしない**（`--no-verify` 禁止）。クローン直後に1回 `git config core.hooksPath .githooks` で有効化する。
- 参照専用プロジェクト `../AI-trend-weather-News` は**変更禁止**（Read のみ）。
