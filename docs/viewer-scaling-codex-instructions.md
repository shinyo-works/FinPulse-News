# Codex 作業指示書 — ヴューアーのデータ量“軽い保険”

第1弾ヴューアー（`docs/viewer-codex-instructions.md`）は実装済み。本書はその**追加改修**の指示書。
データ量が時間とともに増えても困らないよう、**実装コストほぼゼロの保険だけ**を入れる。過剰な作り込みはしない。

- 起動時の前提: `AGENTS.md` → `HANDOFF.md` → `CLAUDE.md` を先に読む（規律・現在地はそちらが正）。
- 表記ルール: 丸囲み数字を使わない。番号は (1)(2)(3) や (a)(b) で書く。
- 既存の収集・フィルタ・メール送信・ヴューアー表示ロジックは壊さない。**追加・小修正のみ**。

---

## 1. 背景（なぜやるか / なぜ大げさにしないか）

- 週1回・7機関の小さなデータ。年52本、10年でも約520本・repo 数十MB程度で、Git もブラウザも余裕で捌ける。**今すぐの本格対策は不要**。
- ただし1か所だけ“無限に増える”ものがある: `output/data/by-institution.json`（全期間の通過記事を1ファイルに集約）。放置すると年々じわ増える。
- そこで、後付けだと面倒な**軽い保険2つ**だけを今のうちに入れておく。

---

## 2. スコープ

### やること
- (a) `by-institution.json` の集約に**直近24ヶ月の窓**を入れ、サイズを頭打ちにする。
- (b) 将来の**年次アーカイブ方針**をドキュメントに残す（実装はしない。方針の明文化のみ）。

### やらないこと
- 年次アーカイブの実装、ページング、DB化、履歴の書き換えなど（今回は不要）。
- 日付別JSON（`YYYY-MM-DD.json`）・index.json の変更（**触らない**。過去分は「日付モード」で従来どおり全て閲覧可能のまま）。

---

## 3. (a) 機関別集約に24ヶ月の窓を入れる

対象関数: `scripts/collect_and_send.py` の `build_institution_index(data_dir)`。

### 仕様
- モジュール定数を追加: `INSTITUTION_WINDOW_MONTHS = 24`。
- 関数シグネチャを `build_institution_index(data_dir, window_months=INSTITUTION_WINDOW_MONTHS, today=None)` に拡張（後方互換: 既存の `build_institution_index(data_dir)` 呼び出しはそのまま動く）。
- 集約の走査で、**基準日から `window_months` ヶ月より古いレポート日付はスキップ**する。
  - 基準日 `base` = `today`（未指定なら `now_jst()` の当日）。
  - しきい値 `cutoff` = 既存の `date_n_months_ago(window_months, base_date)` を `"%Y-%m-%d"` にしたもの。
  - レポート日付（`list_report_dates` が返す `"YYYY-MM-DD"`）は文字列のまま `report_date < cutoff` で比較してよい（ISO 形式なので辞書順＝日付順）。
- `window_months` が 0/None のときは窓なし（全期間）＝従来動作。

### 実装イメージ（関数冒頭に追記し、ループ先頭でスキップ）
```python
INSTITUTION_WINDOW_MONTHS = 24


def build_institution_index(data_dir, window_months=INSTITUTION_WINDOW_MONTHS, today=None):
    """全日付のJSONを読み、通過記事を機関別に重複なくまとめる。
    window_months を指定すると、その月数より古いレポートは集約から除外する（サイズ抑制）。"""
    data_path = Path(data_dir)
    institutions = {}

    cutoff = ""
    if window_months:
        base = today or now_jst().strftime("%Y-%m-%d")
        try:
            base_date = datetime.strptime(base, "%Y-%m-%d").date()
        except ValueError:
            base_date = now_jst().date()
        cutoff = date_n_months_ago(window_months, base_date).strftime("%Y-%m-%d")

    for report_date in list_report_dates(data_path):
        if cutoff and report_date < cutoff:
            continue
        # ...（以降の集約処理は既存のまま）
```

### 呼び出し側
- `write_json_viewer_data`（`collect_and_send.py`）と `scripts/backfill_json.py` の `build_institution_index(data_dir)` 呼び出しは**変更不要**。デフォルト引数で24ヶ月窓が効く。
- 生成後、`output/data/by-institution.json` を作り直しておく（下の検証でバックフィルを流せば更新される）。

### 期待挙動（確認済みの想定値）
- 現時点（2026-07・データは2026-05〜07の12本）は全レポートが24ヶ月以内 → **集約結果は不変**（総item数は変わらない。現状90件）。
- 将来、24ヶ月より古くなったレポートは集約から自然に外れ、`by-institution.json` が一定サイズで頭打ちになる。
- 日付モード（`YYYY-MM-DD.json` 直読み）は**影響なし**。古いレポートも従来どおり全て開ける。

---

## 4. (b) 年次アーカイブ方針をドキュメント化（実装しない）

将来 `output/` のファイル本数が増えて扱いにくくなったときの方針を、**言葉だけ**残す。以下いずれかの形で明文化すればよい（コード変更なし）。

- `HANDOFF.md` の残課題に「将来対応」として1行、または `CLAUDE.md`/この指示書に注記:
  - 「`output/` が数百本規模になったら、古い年のレポートを `output/archive/YYYY/` に移す。ヴューアーの index.json 生成と `list_report_dates` は将来 archive 配下も走査するよう拡張する（今は未実装）。」
- 実装・ディレクトリ作成は今はしない。あくまで“いつ・何をするか”の備忘。

---

## 5. 検証（コミット前に必ず）

```bash
python -m py_compile scripts/collect_and_send.py scripts/backfill_json.py send_report.py send_resend.py
python scripts/backfill_json.py
```
- バックフィル後、`output/data/by-institution.json` が生成され、現時点では**従来と同じ内容（総item数が変わらない）**であることを確認。
- 窓の効きを確認する簡易テスト（任意）: `build_institution_index('output/data', today='2028-08-01')` が全レポートを窓外にして空集約になること、`today='2026-07-19'` では不変であることを確かめる。
- ヴューアー（`docs/index.html`）は `by-institution.json` の形（`institutions[].items[]`）が不変なので**改修不要**。念のため `python -m http.server` 経由で機関モードが従来どおり出るか目視。

---

## 6. コミット・引き継ぎ

- pre-commit を有効化済みであること（`git config core.hooksPath .githooks`）。フックはスキップしない（`--no-verify` 禁止）。
- `HANDOFF.md` を更新（先頭「最終更新」を書き換え、現在地に「機関別集約に24ヶ月窓を追加」、(b) の将来方針を残課題に1行）。コードと同じコミットに含める。
- PowerShell の `git commit` は here-string（`@' ... '@`）。メッセージは日本語。
- push は **`git push origin main`** のみ。**コミット・push はユーザーに確認してから**行う。

---

## 7. 受け入れ基準（Done）

- [ ] `build_institution_index` に `window_months`（既定24）と `today` 引数が入り、古いレポートを集約から除外する。既存の無引数呼び出しは後方互換で動く。
- [ ] 現時点のバックフィル結果で `by-institution.json` の内容が従来と同一（窓の副作用で減っていない）。
- [ ] 日付別JSON・index.json・ヴューアー表示は不変。
- [ ] (b) の年次アーカイブ方針が `HANDOFF.md`（または本書/`CLAUDE.md`）に明文化されている。
- [ ] `python -m py_compile` 全対象OK。pre-commit を通してコミットできる。
- [ ] `HANDOFF.md` 更新済み。

---

## 8. 注意

- 日付比較は ISO 文字列（`YYYY-MM-DD`）の辞書順でよいが、`date_n_months_ago` は既存実装を使う（月末クランプ済み）。
- 集約から外すのは**あくまで by-institution.json だけ**。元の日付別JSONは消さない・移さない。
- JSON は UTF-8・`ensure_ascii=False`。日付基準は JST（`now_jst()`）。
- 参照専用プロジェクト `../AI-trend-weather-News` は変更禁止（Read のみ）。
