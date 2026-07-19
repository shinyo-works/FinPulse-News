# Codex 作業指示書 — レポート・ヴューアー 第1弾

このファイルは **Codex CLI が単体で読んで実装するための指示書**。
実装対象・データ仕様・受け入れ基準・守る規律をここに全部書く。着手前に必ず全体を読むこと。

- 起動時の前提: `AGENTS.md`（入口・進め方）→ `HANDOFF.md`（現在地）→ `CLAUDE.md`（規律）を先に読む。
- この指示書は「何を作るか」の仕様。プロジェクト共通の規律（push 先・pre-commit・日本語統一など）は `AGENTS.md` / `HANDOFF.md` が優先。
- 表記ルール: 丸囲み数字を使わない。番号は (1)(2)(3) や (a)(b) で書く（ユーザー環境で丸囲み数字が表示されないため）。

---

## 1. 目的（何を実現するか）

`output/YYYY-MM-DD.md` として溜まっている週次レポートを、ブラウザで閲覧できる静的ヴューアーを作る。
既存の収集・フィルタ・メール送信ロジック（`scripts/collect_and_send.py` の収集部分）には**手を入れない**。追加のみ。

2つの閲覧モードを持つ:

- **日付モード**: 1日分・全機関のレポートを表示。
- **機関モード**: 1機関を選び、**全レポートを横断して**その機関の記事をまとめて表示（重複除去・日付新しい順・「収録レポート」列付き）。

サーバー・DB は使わない。データは JSON ファイル、表示はブラウザ側 JavaScript だけで完結させる。

---

## 2. スコープ（今回やること / やらないこと）

### やること

- (1) `scripts/collect_and_send.py` に **JSON 出力**を追加（既存の md 出力はそのまま残す）。
- (2) `scripts/backfill_json.py`（使い捨てスクリプト）で既存 `output/*.md` から JSON を一括生成。
- (3) `docs/index.html`（ヴューアー本体・1ファイル・依存ライブラリなし）。

### やらないこと（次弾。今回は触らない）

- 週次ワークフロー（`.github/workflows/weekly-news-report.yml`）への「data/ もコミット」反映。
- GitHub Pages の有効化（repo の公開設定はユーザーが行う）。
- 集計グラフ・トレンド分析などの作り込み。

---

## 3. 参考: 既存レポートの構造（入力データ）

`output/YYYY-MM-DD.md` は `format_report()`（`scripts/collect_and_send.py`）が生成する。構造は以下:

- 冒頭: `# 金融機関新着情報レポート — YYYY-MM-DD`
- 機関ごとの通過セクション:
  ```
  ## 帯広信用金庫　*（収集: プログラム）*

  ### ✅ 通過（5件）
  | 日付 | タイトル | URL |
  |---|---|---|
  | 2026-06-19 | タイトル… | https://... |
  ```
- 末尾に除外一覧:
  ```
  # 除外一覧

  ## 帯広信用金庫　❌ 除外（3件）
  | 日付 | タイトル | 除外キーワード |
  |---|---|---|
  | 2026-05-08 | タイトル… | 奨学金 |
  ```
- タイトル末尾に付く注記（表示フラグの手がかり）:
  - ` ⭐金利・キャンペーン` → `star: true`
  - ` ※1ヵ月超・最新` → `fallback: true`
  - ` ※当月分（日付はページに記載なし・当月初で補完）` → `date_inferred: true`
- 除外一覧は既に「配信日から3ヶ月以内」に絞られている（`format_report` 内でフィルタ済み）。JSON でもこの絞り込み後の内容に揃える。

機関名・収集方式（プログラム / XML / Claude API）は `config.json` と md の見出しから取れる。

---

## 4. データ仕様（JSON）

出力先ディレクトリ: `output/data/`

### (a) `output/data/YYYY-MM-DD.json` — 1レポート分

```json
{
  "date": "2026-06-22",
  "lookback_days": 90,
  "institutions": [
    {
      "name": "帯広信用金庫",
      "method": "プログラム",
      "passed": [
        {
          "date": "2026-06-19",
          "title": "…（注記・⭐を除いた素のタイトル）",
          "url": "https://…",
          "star": false,
          "fallback": false,
          "date_inferred": false
        }
      ],
      "excluded": [
        { "date": "2026-05-08", "title": "…", "exclude_keyword": "奨学金" }
      ]
    }
  ]
}
```

- `title` は表示注記（` ⭐…` ` ※…`）を**取り除いた素のタイトル**を入れる。注記の意味は各フラグに移す。
- `excluded` は3ヶ月フィルタ適用後（= md に載っている分と一致）。

### (b) `output/data/index.json` — 日付マニフェスト

```json
{ "reports": ["2026-07-13", "2026-07-06", "2026-06-29", "..."] }
```

- **新しい順**（降順）。ヴューアーの日付リストにそのまま使う。

### (c) `output/data/by-institution.json` — 全期間横断の機関別集約

```json
{
  "institutions": [
    {
      "name": "北洋銀行",
      "items": [
        {
          "date": "2026-04-22",
          "title": "…",
          "url": "https://…",
          "star": false,
          "reports": ["2026-06-22", "2026-06-29"]
        }
      ]
    }
  ]
}
```

- 全 `output/data/YYYY-MM-DD.json` を走査し、機関ごとに**通過記事**を集約する（除外は機関モードでは既定で扱わない。トグル ON 時のみ別途集約してよいが、第1弾は通過のみで可）。
- 重複除去キーは `(title, url)`。同じ記事が複数レポートに出たら1件にまとめ、`reports` に出現した全レポート日付を新しい順で入れる。
- `items` は `date` の**新しい順**。`date` 空欄の記事は末尾にまとめる。
- 生成方法: **`output/data/*.json` を全読みして毎回作り直す**（差分マージしない）。これで常に完全・最新になる。共有関数にして (1) と (2) の両方から呼ぶ。

---

## 5. 実装ステップ

### (1) `scripts/collect_and_send.py` に JSON 出力を追加

- 収集・フィルタ・`format_report`・`send_email` などの既存関数は**変更しない**。追加のみ。
- 追加する関数（名前は目安・日本語コメント必須）:
  - `build_report_data(results, today, lookback_days) -> dict`
    - `results`（`(name, passed, excluded, method)` のリスト）から 4-(a) の dict を組む。
    - `title` から注記・⭐を除く整形をここで行い、フラグは item のフラグ（`star` / `fallback` / `date_inferred`）をそのまま反映（item に既に入っている）。
  - `build_institution_index(data_dir) -> dict`
    - `data_dir`（`output/data`）内の `YYYY-MM-DD.json` を全読みし、4-(c) の dict を組む。
- `__main__` の、md を書き出している箇所の直後に追記:
  - `output/data/` を作成（無ければ）。
  - `build_report_data(...)` を `output/data/{today}.json` に書き出す（UTF-8・`ensure_ascii=False`・`indent=2`）。
  - `output/data/index.json` を更新（`output/data/*.json` のファイル名から日付一覧を作り、降順で書き出す）。
  - `build_institution_index('output/data')` を `output/data/by-institution.json` に書き出す。
- 例外時もメール送信を妨げないよう、JSON 出力は既存処理の**後**に置く。

### (2) `scripts/backfill_json.py`（使い捨て・過去分生成）

- `output/*.md`（`data` ディレクトリ配下は除く）を全部読み、各ファイルから 4-(a) の per-date JSON を復元して `output/data/` に書き出す。
- md パースの要点:
  - 見出し `## 機関名　*（収集: XXX）*` から機関名・method。
  - `### ✅ 通過（N件）` 以下のテーブル行を passed に。タイトル末尾の ` ⭐…` ` ※1ヵ月超…` ` ※当月分…` を検出してフラグ化し、素のタイトルに戻す。
  - `# 除外一覧` 以降の `## 機関名　❌ 除外（N件）` テーブルを excluded に。
  - `lookback_days` は末尾 `*…対象期間: 過去90日*` から拾う（取れなければ 90）。
- 全 per-date JSON を書き終えたら、`build_institution_index` と index.json 生成を呼んで `by-institution.json` と `index.json` も作る（(1) の関数を import して再利用する。重複実装しない）。
- 冒頭コメントに「使い捨て。将来は collect_and_send.py が直接 JSON を出すため不要」と明記。

### (3) `docs/index.html`（ヴューアー本体）

- 素の HTML + CSS + JavaScript の**1ファイル**。外部ライブラリ・ビルド不要。日本語 UI。
- データ取得: `fetch('../output/data/index.json')` 等の相対パス。`docs/index.html` から見て `../output/data/` を参照する。
- 画面構成（PC）:
  - 左サイドバー上段「📅 日付で見る」= index.json の日付リスト（新しい順）。
  - 左サイドバー下段「🏦 機関で見る」= by-institution.json の機関リスト。
  - 右メイン = 選択に応じた本文。
  - 上部バー（メイン内）= フリーワード検索ボックス / 機関チェックボックス（日付モード時のみ）/「除外も表示」トグル（既定 OFF = 通過のみ）。
- 挙動:
  - 日付クリック → その日の `YYYY-MM-DD.json` を fetch し、機関ごとに通過テーブルを描画（日付モード）。
  - 機関クリック → by-institution.json の該当機関の `items` を1つのテーブルで描画（機関モード。「収録レポート」列に `reports` を表示）。
  - 検索: タイトル部分一致でフィルタ＋一致語をハイライト。
  - 機関チェック（日付モード）: チェックした機関だけ表示。
  - 「除外も表示」ON: 日付モードでは各機関の下に除外テーブルを追加表示。機関モードは第1弾では通過のみでよい（除外対応は任意）。
  - ⭐記事は視覚的に強調。`fallback` / `date_inferred` はバッジや注記で示す。URL は新規タブで開く。
- レスポンシブ: 画面が狭いとき（スマホ）は2ペインをタブ切替にし、テーブルはカード型に積む（横スクロールさせない）。
- 空データ・fetch 失敗時は「データがありません」等のメッセージを出して落ちない。

---

## 6. 動作確認（ローカル・コミット前に必ず）

- 構文チェック（`AGENTS.md` の検証コマンド）:
  ```bash
  python -m py_compile scripts/collect_and_send.py send_report.py send_resend.py scripts/backfill_json.py
  ```
- バックフィル実行:
  ```bash
  python scripts/backfill_json.py
  ```
  → `output/data/` に per-date JSON（既存レポート数と同数）＋ `index.json` ＋ `by-institution.json` ができることを確認。
- ヴューアーをローカル配信して目視確認（`file://` 直開きは fetch が CORS で失敗するため必ず HTTP 経由）:
  ```bash
  python -m http.server 8000
  ```
  ブラウザで `http://localhost:8000/docs/index.html` を開き、以下を確認:
  - 日付モード: 日付を切り替えると全機関の通過が正しく出る。
  - 機関モード: 機関を選ぶと全期間の記事がまとまって出る。重複が除かれ、日付新しい順。「収録レポート」が出る。
  - 検索・機関フィルタ・「除外も表示」トグルが効く。⭐・注記バッジが出る。リンクが新規タブで開く。
- 気になる挙動があれば直してから次へ。**ユーザーに確認せず大きな方針変更をしない**（`AGENTS.md`）。

---

## 7. コミット・引き継ぎ（節目でやること）

- pre-commit フックを有効化してあること（クローン直後に1回）:
  ```bash
  git config core.hooksPath .githooks
  ```
  フックは**スキップしない**（`--no-verify` 禁止）。
- `HANDOFF.md` を更新する（`AGENTS.md` の引き継ぎプロトコル）:
  - 先頭「最終更新」を書き換える。
  - 「現在地」にヴューアー追加を反映。「残課題」に**次弾（ワークフロー反映・Pages 有効化）**を追記。
  - コードと**同じコミット**に含める。
- PowerShell での `git commit` は here-string（`@' ... '@`）を使う。コミットメッセージは日本語。
- push は **`git push origin main`** のみ。**コミット・push はユーザーに確認してから**行う。

---

## 8. 受け入れ基準（Done の定義）

- [ ] `scripts/collect_and_send.py` が、既存 md に加えて per-date JSON・index.json・by-institution.json を出力する（収集・送信ロジックは不変）。
- [ ] `scripts/backfill_json.py` で既存 `output/*.md` 全てから JSON が生成される。
- [ ] `output/data/by-institution.json` が全期間横断・重複除去・日付降順・`reports` 付きで正しい。
- [ ] `docs/index.html` が日付モード / 機関モードの両方で動作し、検索・機関フィルタ・除外トグル・ハイライト・リンクが機能する。
- [ ] スマホ幅でタブ切替＋カード型になり、横スクロールしない。
- [ ] `python -m py_compile` が全対象で通る。pre-commit を通してコミットできる。
- [ ] `HANDOFF.md` を更新し、次弾（ワークフロー反映・Pages 有効化）を残課題に記載した。

---

## 9. 注意・落とし穴

- 既存の収集・フィルタ・`format_report`・`send_email` を壊さない。JSON 出力は**追加**であり、失敗してもメール送信を妨げないよう既存処理の後に置く。
- JSON は UTF-8・`ensure_ascii=False`（日本語をそのまま）。
- 日付基準は JST（`now_jst()`）。新たに日付を扱う場合も JST に揃える。
- `docs/` には既存の手順書 md がある。`docs/index.html` を足しても共存する（消さない・上書きしない）。
- 参照専用プロジェクト `../AI-trend-weather-News` は変更禁止（Read のみ）。
