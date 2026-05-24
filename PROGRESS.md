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

## 2026-05-17 — Phase 1 / Step 2 完了

- 対象URL: https://www.shinkin.co.jp/obishin/news/（帯広信用金庫）
- Chrome MCP でページ取得成功（30件）
- フィルタ適用: 通過 15件 / 除外 4件（電子公告等キーワード不一致含む）
- 収集結果保存: output/2026-05-17.md
- 次の一手: Step 2（設定ファイル化 + 個別記事URL取得）またはGmail送信
