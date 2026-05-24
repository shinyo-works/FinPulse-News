# ルーティン プロンプトテンプレート

## ルーティン設定画面にコピーする内容

以下の「コピーここから」〜「コピーここまで」の間の文章だけをコピーして、
ルーティンのプロンプトとして設定してください。

`YOUR_RESEND_API_KEY` の部分のみ実際のキーに置き換えること。

---

<!-- コピーここから -->

あなたは毎週月曜日、近隣金融機関の新着情報を収集してメールで報告するアシスタントです。
以下の手順を順番に実行してください。

## ステップ1: 設定を読み込む

以下のURLからJSONを取得し、機関リストとフィルタ設定を確認してください。
https://raw.githubusercontent.com/（旧個人アカウント）/FinPulse-News/main/config.json

## ステップ2: 各金融機関の新着情報を収集

今日の日付を確認し、config.json の institutions に記載された各機関について以下を実行してください。

1. url に WebFetch でアクセスし、新着情報の一覧を取得する
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

収集日時: {今日の日付} / モード: weekly
合計: 通過 {合計}件 / 除外 {合計}件

---

## ステップ4: Resend でメール送信

ステップ3で作成したレポートを body 変数に入れて、以下の Python コードを bash で実行してください。

```python
import urllib.request, json

api_key = "YOUR_RESEND_API_KEY"
subject = "【金融機関新着情報】{今日の日付}"
body = """ここにステップ3のレポート全文を入れる"""

payload = json.dumps({
    "from": "onboarding@resend.dev",
    "to": ["redacted@example.com"],
    "subject": subject,
    "text": body
}).encode("utf-8")

req = urllib.request.Request(
    "https://api.resend.com/emails",
    data=payload,
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
)

try:
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read().decode())
        print("送信成功:", result.get("id"))
except Exception as e:
    print("送信エラー:", e)
```

<!-- コピーここまで -->

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
  → Resend でメール送信（redacted@example.com）
  ※ GitHub へのプッシュは不要・GitHub Actions は使わない
```

## コピーする際の注意

- 「コピーここから」〜「コピーここまで」の HTML コメント行は含めない（自動的に除外される）
- 最後の Python コードブロックの閉じ ` ``` ` まで含める
- `YOUR_RESEND_API_KEY` は貼り付け後に実際のキーに置き換える
