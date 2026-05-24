# 作業ログ

## 2026-05-17 — セッション1開始

- プロジェクト方針決定・ロードマップ策定
- CLAUDE.md / PROGRESS.md 作成

## 2026-05-24 — Phase 2 準備完了

- AI-trend-weather-News の実績ある Resend 送信方式を採用 ✅
- `send_resend.py` 作成（Resend SDK経由・redacted@example.com 宛） ✅
- `docs/routine-prompt-template.md` 作成（Phase 3 ルーティン用） ✅
- `.env.example` 作成（RESEND_API_KEY テンプレ） ✅
- 次の一手: `.env` に RESEND_API_KEY を設定 → Phase 2 実行テスト

## 2026-05-24 — Phase 2 完了

- `send_report.py` 作成（output/最新ファイルを自動検出してResend送信） ✅
- テスト送信成功 ✅（件名: 【FinPulse-News】テスト送信）
- レポート送信成功 ✅（件名: 【金融機関新着情報】2026-05-17、7機関37件）
- 次の一手: Phase 3 — GitHub Actions で週次自動化

## 2026-05-24 — Phase 3 完了

- AI-trend-weather-Newsと同じ GitHub Actions 構成を採用 ✅
- `.github/workflows/weekly-news-report.yml` 作成（毎週火曜08:00 JSTに自動実行） ✅
- `scripts/collect_and_send.py` 作成（HTTP取得→Claude API抽出→フィルタ→Resend送信） ✅
- 次の一手: Claude ルーティン × GitHub Actions 連携構成に移行

## 2026-05-24 — Phase 3 構成変更（ルーティン連携）

- 帯広信用金庫・JAめむろが GitHub Actions の IP からブロックされることが判明 ✅
- スクレイピングを Claude ルーティン（月曜05:00 JST）に移管 ✅
- GitHub Actions は output/*.md の push をトリガーにメール送信のみに変更 ✅
- scripts/collect_and_send.py をメール送信専用に簡略化 ✅
- ANTHROPIC_API_KEY 不要に（GitHub Secrets から削除可） ✅
- 次の一手: claude.ai でルーティンを新規作成し routine-prompt-template.md のプロンプトを設定

## 2026-05-17 — Phase 1 / Step 2 完了

- 対象URL: https://www.shinkin.co.jp/obishin/news/（帯広信用金庫）
- Chrome MCP でページ取得成功（30件）
- フィルタ適用: 通過 15件 / 除外 4件（電子公告等キーワード不一致含む）
- 収集結果保存: output/2026-05-17.md
- 次の一手: Step 2（設定ファイル化 + 個別記事URL取得）またはGmail送信
