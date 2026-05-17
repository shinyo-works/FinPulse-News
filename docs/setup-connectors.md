# コネクタ接続状況（2026-05-17 確認）

## 接続済み（追加設定ゼロで使えるもの）

| コネクタ | 用途 | 備考 |
|---|---|---|
| Claude in Chrome | Webサイト閲覧・テキスト抽出 | ブラウザ自動操作 |
| Gmail | メール下書き・送信 | Phase 2 から利用 |
| Google Calendar | スケジュール管理 | 今回は未使用予定 |
| Notion | ドキュメント管理 | 今回は未使用予定 |

## 未接続（追加が必要なもの）

| ツール | 用途 | 対応方法 |
|---|---|---|
| LINE Notify | LINE通知 | REST API（無料）。Phase 3 候補 |
| Slack | Slack通知 | 今回は対象外（Gmail優先） |

## 今回使わないが存在するもの

標準コネクタは 2026年時点で 375+ 存在。Stripe / DocuSign / Spotify 等は今回不要。

## LINE Notify について

- 無料で使える LINE 公式の通知 API
- `https://notify-api.line.me/api/notify` に POST するだけ
- 詳細は Phase 3 着手時に `docs/setup-line-notify.md` を作成予定
