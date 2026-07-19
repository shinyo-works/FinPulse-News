# Codex 作業指示書 — ヴューアーUI微修正（日付リスト高密度化＋ペイン並び替え）

対象は **`docs/index.html` の1ファイルのみ**。左サイドバーの見た目を2点だけ直す。データ構造・JavaScript のロジック・ID・fetch は変えない。

- 起動時の前提: `AGENTS.md` → `HANDOFF.md` → `CLAUDE.md` を先に読む。
- 表記ルール: 丸囲み数字を使わない。番号は (1)(2)(3) や (a)(b) で書く。
- 収集・フィルタ・送信ロジック、`output/data/*.json` の形式には触らない。

---

## 目的（ユーザー要望そのまま）

- (1) **左ペインの日付リストをもっと詰めて、一度に多くの行が見えるようにする。**
- (2) **「機関で見る」の内容を「日付で見る」より上に配置する**（機関ペインが日付ペインの上）。

---

## 現状（該当箇所）

`docs/index.html` の左サイドバー内、`<aside class="sidebar">` の中に2つの `<section class="nav-section">` がこの順で並ぶ:

```html
<section class="nav-section">
  <h2 class="nav-heading">📅 日付で見る</h2>
  <ul class="nav-list" id="dateList">…</ul>
</section>

<section class="nav-section">
  <h2 class="nav-heading">🏦 機関で見る</h2>
  <ul class="nav-list" id="institutionList">…</ul>
</section>
```

- 日付リスト（`#dateList`）は `formatDate()` で「2026年7月13日」形式のボタンを縦に並べる。件数は今後も増える（週次でレポートが増える）。
- 共通スタイル: `.nav-list { gap: 3px }`、`.nav-button { padding: 9px 12px; border-radius: 9px }`。
- 機関リスト（`#institutionList`）は7件程度と少なく、詰める必要はない。

---

## 変更内容

### (1) ペインの並び替え（機関を上、日付を下）
サイドバー内の2つの `<section class="nav-section">` ブロックの**順序を入れ替える**だけ。
- 上に「🏦 機関で見る」（`#institutionList`）、下に「📅 日付で見る」（`#dateList`）。
- **HTML の並びを入れ替えるのみ。ID・クラス・JavaScript は一切変更しない**（`renderNavigation()` などは ID 参照なので順序に依存せず動く）。
- 初期表示は従来どおり日付モードのまま（`initialize()` が最新レポートを開く挙動は変えない）。

### (2) 日付リストの高密度化
日付リストの行だけを詰めて、一度に表示される行数を増やす。機関リストの見た目は据え置き（7件しかないため）。

- **日付リストのボタンだけに効く compact スタイル**を追加する。`#dateList` を起点にセレクタを絞ると、機関側に影響しない。
- 目安の値（この範囲で調整可）:
  - 縦 padding を詰める: 上下 `4〜5px`（左右は現状の `12px` 前後を維持）
  - フォント: `0.8rem` 程度に小さく、`font-variant-numeric: tabular-nums`（数字の桁を揃えて見やすく）
  - 行間の隙間: `#dateList.nav-list { gap: 2px }` 程度
  - `border-radius` は `7px` 程度に軽く
  - 1行に収めたい: `white-space: nowrap`（日付は1行で十分収まる）
- 実装例（`<style>` 内に追記。既存の `.nav-button` は共通のまま、日付側だけ上書き）:
  ```css
  #dateList { gap: 2px; }
  #dateList .nav-button {
    padding: 4px 12px;
    border-radius: 7px;
    font-size: 0.8rem;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  ```
- スマホ表示（`@media (max-width: 700px)`）でも破綻しないこと（既存のタブ切替・サイドバー全幅表示を壊さない）。

---

## やらないこと
- 日付の表示文言（「2026年7月13日」形式）の変更はしない（詰めるのは余白・文字サイズのみ）。必要と感じても勝手に短縮形へ変えない。
- 機関リスト・トップバー・本文テーブル・カード表示のスタイル変更はしない。
- JavaScript・データ・fetch パス・ID の変更はしない。

---

## 検証（コミット前に必ず）
- ローカル配信して目視（`file://` 直開きは fetch が失敗するため不可）:
  ```bash
  python -m http.server 8000
  ```
  `http://localhost:8000/docs/index.html` を開き、以下を確認:
  - 左サイドバーで **「🏦 機関で見る」が上、「📅 日付で見る」が下**になっている。
  - 日付リストが以前より詰まり、一度に多くの日付行が見える。日付は1行で崩れていない。
  - 日付クリックで日付モード、機関クリックで機関モードが従来どおり動く（active 表示・検索・絞り込み・除外トグルも不変）。
- スマホ幅（横700px以下）に狭めても、タブ切替とサイドバー表示が崩れない。

---

## コミット・引き継ぎ
- pre-commit を有効化済みであること（`git config core.hooksPath .githooks`）。`--no-verify` 禁止。
- `HANDOFF.md` は軽微なUI調整のため、必要なら「現在地」に1行反映する程度でよい（大きな残課題の増減はなし）。
- コミットメッセージは日本語。PowerShell の `git commit` は here-string（`@' ... '@`）。
- push は **`git push origin main`** のみ。**コミット・push はユーザーに確認してから**行う。

---

## 受け入れ基準（Done）
- [ ] サイドバーが「機関で見る（上）／日付で見る（下）」の順になっている。
- [ ] 日付リストが高密度化し、同じ画面高さでより多くの日付行が見える。日付は1行表示。
- [ ] 機関リスト・本文・検索・フィルタ・除外トグル・スマホ表示は従来どおり動く（デグレなし）。
- [ ] 変更は `docs/index.html` のみ。JavaScript・ID・データは不変。

---

## 注意
- 変更は HTML の並び替えと CSS 追加のみに限定する。ロジックへ波及させない。
- 参照専用プロジェクト `../AI-trend-weather-News` は変更禁止（Read のみ）。
