# ルーティン プロンプトテンプレート

## ルーティン設定画面にコピーする内容

以下の「コピーここから」〜「コピーここまで」の間の文章だけをコピーして、
ルーティンのプロンプトとして設定してください。

`YOUR_GITHUB_PAT` の部分のみ実際のトークンに置き換えること。
（GitHub PAT の取得方法は下部参照）

---

<!-- コピーここから -->

あなたは毎週月曜日、近隣金融機関の新着情報を収集してメールで報告するアシスタントです。
以下の手順を順番に実行してください。

**重要：ステップ2でエラーが発生した機関は「取得失敗」と記録してスキップし、次の機関に進んでください。すべての機関の処理が終わったら（失敗があっても）必ずステップ4を実行してください。**

## ステップ1: 設定を読み込む

以下のURLからJSONを取得し、機関リストとフィルタ設定を確認してください。
https://raw.githubusercontent.com/（旧個人アカウント）/FinPulse-News/main/config.json

## ステップ2: 各金融機関の新着情報を収集

今日の日付を確認し、config.json の institutions に記載された各機関について以下を実行してください。
**1機関ずつ順番に処理し、エラーが出ても止まらず次の機関に進むこと。**

1. url に WebFetch でアクセスし、新着情報の一覧を取得する
   - アクセス失敗・タイムアウトの場合は「取得失敗」と記録して次の機関へ
2. 各記事の「日付・タイトル・URL」を抽出する（過去7日以内が対象）
3. 以下のフィルタルールを適用する：

   【通過条件】
   include_keywords のいずれかをタイトルに含む記事を通過させる

   【除外条件】
   exclude_rules の keyword をタイトルに含む記事は除外する
   ただし unless が設定されている場合、unless リストのいずれかも含む記事は除外しない

   【フォールバック】
   過去7日以内の通過記事が0件の場合は、全期間から最新の通過記事を1件表示する
   （「直近7日以内の対象記事なし」と注記する）

## ステップ3: レポート本文を生成

以下のフォーマットでレポートを作成してください。

---
# 金融機関新着情報レポート — {今日の日付}

## 通過

### {機関名}
| 日付 | タイトル | URL |
|---|---|---|
| YYYY-MM-DD | タイトル（金利・キャンペーン関連は末尾に [金利・キャンペーン] を付ける） | URL |

（全機関分）

---

## 除外

### {機関名}
| 日付 | タイトル | 除外キーワード |
|---|---|---|

（全機関分）

---

## 取得失敗

取得できなかった機関: {機関名（カンマ区切り）、なければ「なし」}

---

収集日時: {今日の日付} / モード: weekly
合計: 通過 {合計}件 / 除外 {合計}件 / 取得失敗 {合計}機関

---

## ステップ4: GitHub Actions でメール送信をトリガー

ステップ3で作成したレポートを body 変数に入れて、以下の Python コードを bash で実行してください。

```python
import urllib.request, json

github_token = "YOUR_GITHUB_PAT"
subject = "【金融機関新着情報】{今日の日付}"
body = """ここにステップ3のレポート全文を入れる"""

payload = json.dumps({
    "event_type": "send-report",
    "client_payload": {
        "subject": subject,
        "body": body
    }
}).encode("utf-8")

req = urllib.request.Request(
    "https://api.github.com/repos/（旧個人アカウント）/FinPulse-News/dispatches",
    data=payload,
    headers={
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }
)

try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"GitHub Actions トリガー成功（ステータス: {r.status}）")
except Exception as e:
    print(f"エラー: {e}")
```

<!-- コピーここまで -->

---

## GitHub PAT（Personal Access Token）の取得方法

1. https://github.com/settings/tokens/new を開く
2. Note（名前）: `FinPulse-News routine`
3. Expiration: 任意（1年推奨）
4. Select scopes: **repo** にチェック（`public_repo` だけでも可）
5. 「Generate token」→ 表示されたトークン（`ghp_...`）をコピー
6. ルーティンプロンプトの `YOUR_GITHUB_PAT` を置き換える

---

## ルーティンのスケジュール設定

| 項目 | 設定値 |
|---|---|
| 実行タイミング | 毎週月曜 05:00 JST（= 日曜 20:00 UTC） |
| モデル | claude-sonnet-4-5 以上 |

## 処理の流れ

```
Claude ルーティン（月曜 05:00 JST）
  → config.json を GitHub raw URL から読み込み
  → 全7機関を WebFetch でスクレイピング（IP ブロックなし）
  → キーワードフィルタ適用
  → GitHub dispatch API を呼び出す（Python）

GitHub Actions（dispatch イベント受信）
  → GITHUB_EVENT_PATH からレポートを読み込み
  → Resend でメール送信（redacted@example.com）
```
