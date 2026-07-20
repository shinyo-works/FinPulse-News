# 金融機関新着情報収集・報告 自動化プロジェクト

## プロジェクト概要

近隣金融機関のWebサイトから新着情報を収集し、キーワードフィルタ後にGmailで報告する自動化ツール。

---

## 採用ツール・構成

| 役割 | ツール | 状態 |
|---|---|---|
| Webサイト巡回・取得 | Claude in Chrome MCP（接続済み） | ✅ Phase 1で使用 |
| メール送信 | Resend API（`scripts/collect_and_send.py`） | ✅ Phase 2で使用 |
| スクレイピング自動化 | Claude ルーティン（月曜 05:00 JST） | ✅ Phase 3で使用 |
| メール自動送信 | GitHub Actions（push トリガー） | ✅ Phase 3で使用 |
| LINE通知 | LINE Notify REST API（要設定） | Phase 3候補 |
| データ保存 | Markdown ファイル | Phase 1から使用 |

## ファイル構成

```
FinPulse-News/
├── .env                   # APIキー保存（Git管理外）
├── .env.example           # キーのテンプレ
├── config.json            # 対象URL・キーワード設定
├── send_resend.py         # Resend経由メール送信スクリプト（手動用）
├── send_report.py         # 最新レポート送信スクリプト（手動用）
├── scripts/
│   └── collect_and_send.py  # GitHub Actions用 収集・送信スクリプト
├── .github/
│   └── workflows/
│       └── weekly-news-report.yml  # 週次自動実行ワークフロー
├── output/                # 収集結果の保存先
│   └── YYYY-MM-DD.md      # 日付別レポート
├── docs/
│   ├── setup-connectors.md
│   ├── routine-prompt-template.md
│   └── research-line-notify.md
├── CLAUDE.md              # このファイル
└── PROGRESS.md            # 作業ログ
```

## 環境変数（.env / GitHub Secrets）

| キー | 用途 | 設定場所 |
|---|---|---|
| `RESEND_API_KEY` | Resend メール送信 | .env + GitHub Secrets |

## 設計決定事項

- メール送信: Resend API（AI-trend-weather-News と同じ実績ある仕組み）
- 金融機関の収集・JSON・表示順は `config.json` の `institutions` 配列を唯一の正とする。生成処理とバックフィルで順序を正規化し、ヴューアー側では並べ替えない。
- LINEは Phase 3 候補（REST API 必要）
- Slack は未採用（ユーザー環境にコネクタなし）
- スクレイピング: Chrome MCP（puppeteer不要・インストールゼロ）

## Phase 進捗

- [x] Phase 1: 複数サイト手動スクレイピング → ファイル保存（7機関）
- [x] Phase 2: Resend でメール送信
- [x] Phase 3: 週次自動化（GitHub Actions）
