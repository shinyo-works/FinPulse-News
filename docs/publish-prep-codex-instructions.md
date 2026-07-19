# Codex 作業指示書 — public 公開の前処理（メールSecret化 + Pages用ファイル）

リポジトリを **public** にして GitHub Pages でヴューアーを公開する前の下ごしらえ。
public にすると全ファイルが世界に見えるため、(1) 送信先メールをコードから消して Secret 化し、(2) Pages を動かす静的ファイル2枚を足す。

- 起動時の前提: `AGENTS.md` → `HANDOFF.md` → `CLAUDE.md` を先に読む。
- 表記ルール: 丸囲み数字を使わない。番号は (1)(2)(3) や (a)(b) で書く。
- 既存の収集・フィルタ・ヴューアー表示ロジックは壊さない。**変更は下記に限定**。
- 秘密情報・個人情報はチャットやコミットに直書きしない。

---

## 背景（なぜやるか）

- 監査の結果、API キー等の秘密情報はリポジトリ・履歴に無く安全。**ただし個人 Gmail の実アドレスが複数ファイルにベタ書き**されており、public 化すると自動収集され迷惑メールの温床になり得る。
- 方針: 送信先を GitHub Secret `REPORT_TO` に移し、コードは環境変数から読む。現行コード・現行ドキュメントからアドレスを消す。
- 注意: **過去の git 履歴にはアドレスが残る**（履歴書き換えはしない）。今回は「現行ファイルから消す」までが目的。

---

## 1. メール送信先を Secret 化する

### 対象と現状（ベタ書き箇所）
- `scripts/collect_and_send.py`（`send_email` 内）… `"to": ["<REPORT_TO>"]`
- `send_report.py` … `"to": "<REPORT_TO>"`
- `send_resend.py` … `"to": "<REPORT_TO>"`

### やること
3ファイルすべて、宛先を環境変数 `REPORT_TO` から読むよう修正する。

- 読み方は既存の `RESEND_API_KEY` の流儀に合わせ、**必須の環境変数**として扱う（未設定なら分かりやすく落とす）。リテラルのフォールバックは**置かない**（置くとアドレスがコードに残り目的を達しない）。
  - 例（`collect_and_send.py` の `send_email`）:
    ```python
    api_key = os.environ["RESEND_API_KEY"].strip()
    to_addr = os.environ["REPORT_TO"].strip()   # 追加
    ...
    "to": [to_addr],
    ```
  - `send_report.py` / `send_resend.py` も同様に `os.environ["REPORT_TO"]`（または既存の env 読み込み方式に合わせる。`send_resend.py` は `.env` を読み込む仕組みがあるのでそれを活用）。宛先文字列はスカラ（`"to": to_addr`）でよい。
- `RESEND_API_KEY` 同様、未設定時のエラーメッセージがある関数（`send_report.py` は「RESEND_API_KEY が設定されていません」を出している）は、`REPORT_TO` 用にも同種のチェックを足すと親切。

### ワークフローに Secret を渡す
`.github/workflows/weekly-news-report.yml` の「収集・レポート生成・送信」ステップの `env:` に1行足す:
```yaml
      - name: 収集・レポート生成・送信
        env:
          RESEND_API_KEY: ${{ secrets.RESEND_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          REPORT_TO: ${{ secrets.REPORT_TO }}   # 追加
        run: python scripts/collect_and_send.py
```

### `.env.example` に項目を追記
ローカル実行者向けに、テンプレへ追記（実アドレスは書かない）:
```
RESEND_API_KEY=ここにResendのAPIキーを貼る（re_で始まる文字列）
REPORT_TO=送信先メールアドレス（例: name@example.com）
```

### 現行ドキュメントからアドレスを消す
public で見えるドキュメントのベタ書きも placeholder に置換（履歴は残るが現行ファイルからは消す）:
- `PROGRESS.md`（個人メールアドレス宛の記述）→ 「送信先メール（`REPORT_TO` Secret）宛」等に置換。
- `docs/routine-prompt-template.md`（個人メールアドレスを含む送信先記述）→ 「Resend でメール送信（送信先は `REPORT_TO` Secret）」等に置換。

---

## 2. Pages 用の静的ファイルを2枚追加

方針は確定済み（ブランチ公開・公開ルート `/`）。ヴューアーは `docs/index.html` から `../output/data/*.json` を読むため、公開ルートはリポジトリルートで、`output/` も公開範囲に入る前提。

### (a) リポジトリ直下 `index.html`（リダイレクト専用）
ベースURL（`https://<user>.github.io/FinPulse-News/`）を開いたら自動でヴューアーへ飛ばす:
```html
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="0; url=./docs/index.html">
  <link rel="canonical" href="./docs/index.html">
  <title>金融機関 新着情報ヴューアー</title>
</head>
<body>
  <p>ヴューアーへ移動します。自動で切り替わらない場合は
     <a href="./docs/index.html">こちら</a>。</p>
</body>
</html>
```

### (b) リポジトリ直下 `.nojekyll`（空ファイル）
GitHub Pages の Jekyll ビルドを無効化し、置いたファイルをそのまま静的配信させる（`_` 始まりの無視やビルド事故を防ぐ）。中身は空でよい。

---

## 3. ユーザーが別途おこなう手動手順（Codex は実施しない・依存として認識）

以下は GitHub の Web 設定操作。Codex のコミットとは別に、ユーザーが行う:
- (a) リポジトリ Settings → Secrets and variables → Actions → **New repository secret** で `REPORT_TO` = 送信先アドレスを登録。
- (b) リポジトリを public に変更（Settings → General → Danger Zone）。
- (c) Settings → Pages → Source: Deploy from a branch / Branch: `main` / Folder: `/ (root)` → Save。

**順序の注意**: `REPORT_TO` Secret 登録（a）は、次回のワークフロー実行より前に済ませること（未登録だと送信ステップが `REPORT_TO` 無しで失敗する）。

---

## 4. 検証（コミット前に必ず）

```bash
python -m py_compile scripts/collect_and_send.py send_report.py send_resend.py scripts/backfill_json.py
```
- ソース全文検索で、現行の追跡ファイルに実アドレスが残っていないこと:
  ```bash
  git grep -n '<送信先メールアドレス>'
  ```
  → **ヒット0件**であること（履歴は対象外。現行ファイルのみ確認）。
- ローカルで送信を試す場合は `.env` に `REPORT_TO` を設定してから。未設定なら分かりやすいエラーで落ちることを確認。
- `index.html`（リダイレクト）と `.nojekyll` がリポジトリ直下にあること。

---

## 5. コミット・引き継ぎ

- pre-commit を有効化済みであること（`git config core.hooksPath .githooks`）。`--no-verify` 禁止。
- `HANDOFF.md` を更新（先頭「最終更新」書き換え、現在地に「送信先メールを Secret 化」「Pages 用ファイル追加」、残課題からヴューアー第2弾/Pages 関連の進捗を反映）。コードと同じコミットに含める。
- PowerShell の `git commit` は here-string（`@' ... '@`）。メッセージは日本語。
- push は **`git push origin main`** のみ。**コミット・push はユーザーに確認してから**行う。

---

## 6. 受け入れ基準（Done）

- [ ] `collect_and_send.py` / `send_report.py` / `send_resend.py` が宛先を `REPORT_TO` から読み、リテラルのアドレスを持たない。
- [ ] ワークフローの送信ステップに `REPORT_TO: ${{ secrets.REPORT_TO }}` を追加。
- [ ] `.env.example` に `REPORT_TO` を追記。
- [ ] `PROGRESS.md` / `docs/routine-prompt-template.md` の実アドレスを placeholder に置換。
- [ ] 実際の送信先メールアドレスを指定した `git grep` が現行ファイルで0件。
- [ ] リポジトリ直下に `index.html`（`docs/index.html` へリダイレクト）と空の `.nojekyll` を追加。
- [ ] `python -m py_compile` 全対象OK。pre-commit を通してコミットできる。
- [ ] `HANDOFF.md` 更新済み。

---

## 7. 注意・落とし穴

- リテラルのフォールバック宛先を置かない（目的が無効化する）。
- `from` アドレス `onboarding@resend.dev` は変更しない（`CLAUDE.md`/実績どおり）。
- 過去の git 履歴にアドレスは残る。今回は現行ファイルからの除去のみ（履歴書き換えはしない）。
- 公開ルートは `/`（`/docs` にしない）。日付別JSON・index.json・by-institution.json の場所は変えない。
- 参照専用プロジェクト `../AI-trend-weather-News` は変更禁止（Read のみ）。
