# 金融機関新着情報収集・報告 自動化プロジェクト

## プロジェクト概要

近隣金融機関のWebサイトから新着情報を収集し、キーワードフィルタ後にメールで報告する自動化ツール。

---

## 採用ツール・構成

| 役割 | ツール | 状態 |
|---|---|---|
| Webサイト巡回・取得 | requests + BeautifulSoup／機関別の専用DOM・XML解析 | ✅ 本番稼働中 |
| メール送信 | Resend API（`scripts/collect_and_send.py`） | ✅ Phase 2で使用 |
| スクレイピング自動化 | GitHub Actions（月曜 05:00 JST・収集も実行） | ✅ Phase 3で使用 |
| メール自動送信 | GitHub Actions（週次・手動実行） | ✅ Phase 3で使用 |
| Claude抽出経路 | コードのみ温存・利用機関0・Secret未連携 | 凍結中 |
| LINE通知 | LINE Notify REST API（要設定） | Phase 3候補 |
| データ保存 | 人間向けMarkdown + 検証済みJSON三点セット | ✅ 本番稼働中 |

## ファイル構成

```
FinPulse-News/
├── .env                   # APIキー保存（Git管理外）
├── .env.example           # キーのテンプレ
├── config.json            # 対象URL・キーワード設定
├── requirements.in        # 直接利用するPython依存
├── requirements.lock      # バージョン・ハッシュ固定済みの全依存
├── send_resend.py         # Resend経由メール送信スクリプト（手動用）
├── send_report.py         # 最新レポート送信スクリプト（手動用）
├── scripts/
│   ├── collect_and_send.py  # 収集・フィルタ・保存・送信の本体
│   ├── emailer.py           # Resend送信の共通処理
│   └── update_rate_history.py # 金利履歴の検証・更新
├── .github/
│   └── workflows/
│       └── weekly-news-report.yml  # 週次自動実行ワークフロー
├── output/                # 収集結果の保存先
│   ├── YYYY-MM-DD.md      # 人間向け日付別レポート
│   └── data/              # 日付別・一覧・機関別の公開JSON
├── docs/
│   ├── index.html         # 新着ニュースヴューアー
│   ├── rate-history.html  # 金利履歴ヴューアー
│   ├── data/rate-history.json # 金利履歴の正本
│   ├── setup-connectors.md
│   ├── routine-prompt-template.md
│   └── research-line-notify.md
├── tests/                 # 標準unittestによる固定データ・モックテスト
├── CLAUDE.md              # このファイル
└── HANDOFF.md             # 現在地・残課題の唯一の正
```

## 環境変数（.env / GitHub Secrets）

| キー | 用途 | 設定場所 |
|---|---|---|
| `RESEND_API_KEY` | Resend メール送信 | .env + GitHub Secrets |
| `REPORT_TO` | レポート送信先 | .env + GitHub Secrets |

## 設計決定事項

- メール送信: Resend API（AI-trend-weather-News と同じ実績ある仕組み）
- 自動メールは配信日を含む冪等キーを使い、同日の自動再実行による重複を抑止する。手動送信は明示的な再送として扱う。
- 一部機関の収集に失敗してもMarkdownと検証済みJSONは保存・公開し、GitHub Actions自体は失敗表示にする。
- 収集結果は `InstitutionResult.status/error` で正常0件と失敗を区別する。汎用抽出を全機関向けに条件分岐させず、サイト固有構造は `SCRAPERS` 登録簿へ専用抽出を追加する。
- HTML/XMLはサイズ・Content-Type・HTTP状態を検証し、接続待ちと読取無通信に共通タイムアウトを使う。収集ステップは15分で打ち切るが、ジョブ全体は打ち切らず、失敗時も成果物保存を続ける。
- GitHub Actionsは収集ジョブを読み取り権限、公開ジョブだけを書き込み権限とする。外部ActionはコミットSHA、Python依存は `requirements.lock` のバージョンとハッシュで固定する。
- `requirements.in` 更新後は Python 3.11 で `pip-compile --generate-hashes --output-file=requirements.lock requirements.in` を実行し、ロックを再生成する。
- 金融機関の収集・JSON・表示順は `config.json` の `institutions` 配列を唯一の正とする。生成処理とバックフィルで順序を正規化し、ヴューアー側では並べ替えない。
- 公開JSONは、日付別・日付一覧・機関別集約の三点を一時領域で全検証し、入口の `index.json` を最後に置換する。成功マーカーがない実行では既存JSONを公開し直さない。
- Markdownは人間向け保存物で、通常の公開JSON生成では再解析しない。使い捨てbackfillを再び本番入力にする場合だけ、表セルのエスケープとparserを一体で設計する。
- 記事URLはHTTP(S)だけを許可する。十勝信用組合の `shinyo.jp` など正規の外部リンクがあるため、同一ホスト制限はしない。必要になった場合は機関別許可ホストを実リンク監査と一緒に導入する。
- 機関別ニュース集約は24か月窓に制限する。`output/` が数百本規模になった時に年次アーカイブとArtifact差分化を一体で設計し、それまでは原子的な全JSON検証を優先する。
- 金利履歴は確認できた有限の数値だけを保存し、「据え置き」と「未確認」を区別する。`rate_type_order` と `rate_type_labels` が表示順・表示名の正で、入力レポートの `rate_contract` と手動値の型を更新前に検証する。
- ニュースと金利はJavaScript名・IDの衝突を避けるためiframeで分離する。手動送信の2入口は用途別に維持し、送信本体だけを共通化する。
- Claude抽出経路は削除せず凍結する。再有効化する場合は、非信頼HTMLとの指示境界、構造化出力、型・日付・URL・期間の再検証、出力切断、timeout/retry、例外分類、依存分離を一括で実装する。部分修正はしない。
- LINEは Phase 3 候補（REST API 必要）
- Slack は未採用（ユーザー環境にコネクタなし）

## Phase 進捗

- [x] Phase 1: 複数サイト手動スクレイピング → ファイル保存（8機関）
- [x] Phase 2: Resend でメール送信
- [x] Phase 3: 週次自動化（GitHub Actions）
