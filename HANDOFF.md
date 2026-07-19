# HANDOFF.md — 現在地・残課題の正（SSoT）

このファイルは **Codex と Claude Code の両方が読み書きする、ライブな引き継ぎ状態の唯一の正**。
「いま何がどこまで進んでいて、次に何が残っているか」はここだけに書く（メモリや他ファイルに二重に書かない）。

- **規律・構成は `CLAUDE.md`**。**進め方とプロトコルは `AGENTS.md`**。**収集ロジック本体は `scripts/collect_and_send.py`**。
- 節目（意味のあるコミット）ごとに本ファイルを更新し、**コードと同じコミットに含める**。
- 更新時は下の「最終更新」を必ず書き換える（古さを検知できるように）。

**最終更新**: 2026-07-19 / ヴューアー左サイドバーのUI微修正を追加（更新者: Codex）

---

## 現在地

- 帯広エリア近隣7金融機関の新着情報を週次収集してメール送信する自動化ツール。Phase 3（週次自動化）まで稼働中。
- GitHub Actions が毎週月曜 05:00 JST に収集→フィルタ→Resend送信→`output/YYYY-MM-DD.md` を自動コミット。直近（06-22〜07-13）も7機関すべて取得成功で正常稼働。
- 相対日付補完（`infer_date_from_relative_text`）を本番検証済み: `output/2026-06-22.md` で十勝信組「今月のローン金利」が 2026-06-01＋補完注記付きで正しく通過。補完注記が付いたのはこの1件のみで他機関への誤補完なし。コード変更不要。
- 除外一覧の3ヶ月フィルタを追加（2026-07-19）: `format_report` で、配信日（today）から3ヶ月以上経過した除外項目は列挙しない（日付不明は fail-open で残す）。件数表示と「合計 除外」も表示ベースに修正。月末クランプ付き `date_n_months_ago()` を新設。通過セクションのロジックは不変。
- 静的ヴューアー第1弾を実装:
  - `collect_and_send.py` に日付別JSON・日付一覧・機関別集約JSONの追加出力を実装。JSON失敗時も従来のメール送信は継続する。
  - `scripts/backfill_json.py` で既存Markdownレポート12件を `output/data/` へ変換。初期の旧形式レポートにも対応。
  - `docs/index.html` に日付モード・機関モード・検索・機関絞り込み・除外表示・スマホ表示を実装（外部ライブラリなし）。
- 機関別集約に24ヶ月の保持窓を追加: `build_institution_index` が直近24ヶ月だけを集約し、日付別JSON・index.json・ヴューアーの形式は不変。
- public公開前の準備を実装・コミット済み: 送信先を `REPORT_TO` Secretへ移し、リポジトリ直下にPages用リダイレクト(`index.html`)と`.nojekyll`を追加。
- **git履歴を書き換えてGmailを完全除去（2026-07-19・git-filter-repo）**: (a) 全履歴のファイル内容の個人Gmail実値を `redacted@example.com` に置換、(b) コミット作者/コミッターのGmailを noreply(`maintainer@finpulse-news.invalid`) に置換。**全コミットのSHAが変わったため、過去のHANDOFF記載やコミットメッセージ中の旧ハッシュ（例 `ad3133b` 等）は無効**。旧履歴を持つstaleブランチ `claude/bold-planck-rxVHX` はリモートから削除済み。旧履歴のバックアップは `../FinPulse-News-backup-pre-rewrite.bundle`。
- **リポジトリを public 化済み（2026-07-19）**。`REPORT_TO` Secret 登録済み。現行ファイル・全リモート履歴ともに個人Gmailの実値は0件。
- ヴューアー左サイドバーを微修正: 機関リストを日付リストより上に移し、日付リストだけを高密度化。JavaScript・ID・データ形式は不変。
- 未コミットの変更: なし。
- 検証: `python -m py_compile`（対象4スクリプト構文OK）、`REPORT_TO` 未設定時の送信停止、個人メール実値の `git grep` 0件、バックフィル12件成功、JavaScript構文OK。ローカルブラウザで日付・機関切替、検索、絞り込み、除外、スマホ表示を確認済み。lint/型/test 基盤は未導入。

## 残課題（優先度順）

1. **GitHub Pages 有効化と表示確認**: `REPORT_TO` 登録・public化は完了済み。残るは Settings → Pages で Source: Deploy from a branch / Branch `main` / Folder `/(root)` を有効化し、`https://旧個人アカウント側の github.io/FinPulse-News/`（root `index.html` がヴューアーへリダイレクト）と `…/output/data/index.json` の表示を確認する。公開ルートは必ず `/(root)`（`/docs` 不可＝ヴューアーが `../output/data` を読むため）。
2. **ヴューアー自動公開の確認**: Pages公開後、週次ワークフローで更新された `output/data/` が公開画面にも反映されることを確認する。
3. **北洋の通過率が低い**（優先度低）: 投稿が企業向け中心で 金利/キャンペーン/お知らせ に当たりにくい。取得自体は正常。必要なら include キーワード調整（別タスク・先に「北洋で何を拾いたいか」の方針決めが必要）。
4. **将来の年次アーカイブ**: `output/` が数百本規模になったら、古い年のレポートを `output/archive/YYYY/` に移す。将来は `list_report_dates` とindex.json生成をarchive配下も走査するよう拡張する（現時点では未実装）。

### 完了済み
- ~~相対日付補完の本番確認~~: 2026-07-19 検証完了。`output/2026-06-22.md` で意図どおり動作、誤補完なし。

## 環境メモ

- 秘密情報は `.env`（Git管理外）と GitHub Secrets に置く。チャットに貼らない。`RESEND_API_KEY`（送信用）、`ANTHROPIC_API_KEY`（Claude遅延生成・現状未消費）。
- 本番実行は GitHub Actions（`.github/workflows/weekly-news-report.yml`）。依存は workflow 内で `pip install anthropic requests beautifulsoup4 defusedxml`。手動実行は workflow_dispatch ボタン。
- **リポジトリは public**。秘密情報・個人情報をコミットに含めない。送信先メールは `REPORT_TO` Secret（コード・ドキュメントに実値を書かない）。
- コミット用の `user.email` はこのリポジトリで noreply(`maintainer@finpulse-news.invalid`)に設定済み。Gmail等の個人メールで再コミットしないこと。別環境で作業する場合も同様に設定する。
- **履歴は書き換え済み（2026-07-19）。旧clone・旧ブランチを復活させない**。別の場所にclone があれば作り直す（`git reset --hard origin/main` かクローンし直し）。
- push 先は **origin/main** のみ。
- pre-commit フックを**スキップしない**（`--no-verify` 禁止）。クローン直後に1回 `git config core.hooksPath .githooks` で有効化する。
- 参照専用プロジェクト `../AI-trend-weather-News` は**変更禁止**（Read のみ）。
