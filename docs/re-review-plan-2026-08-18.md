# 再レビュー実装プラン（2026-08-18・採点 81/100）

対象: コミット `8055323`「feat: ヴューアー表示修正3件（見出し移設・金利説明のタブ右側化・ペイン幅調整）」の差分に対するコードレビュー指摘8件（#1〜#8）＋ユーザー追加要望（#9）。

- 検証方法: 設計意図・履歴調査エージェントと修正リスク評価エージェントを並列実行し、結果を突き合わせたうえで採点エージェントがコード実態と抜き取り照合（81/100・合格）。
- 現状: `python -m unittest discover -s tests -q` は 55件全合格。
## 実装状況（2026-08-19 更新）

| 指摘 | 状態 | コミット |
|---|---|---|
| #8 #6 #2 | 実装・動作確認済み | `6bfee76` |
| #4 #3 | 実装・動作確認済み（#3 は (a) 誤情報の除去） | `f818bb7` |
| #9 #5 | 実装・動作確認済み（狭幅はサイト名＋短縮説明を残す） | `6da61d1` |
| #7 | **未実装**。実測（iframe 初回描画のサンプリング）を2度試みたがレンダラーが応答しなくなり測定できず。目視できるちらつきは確認されていないため、方針(a)どおり現状維持 |
| #1 | 見送り確定（既存 #16 として保留・PCのみ運用の判断） |

実装時に判明した追加事実: `.tab-brand` は `<b>` 要素なので `.tab-note b`（詳細度 0,1,1）が `.tab-brand`（0,1,0）に勝ち、そのままではサイト名も縮んで切れる。`.tab-note .tab-brand` で上書きすること（テストで固定済み）。



---

## A評価（即着手・4件）

すべて今回追加した resizer 周り（保護契約の外側）に閉じる。

### #2 ドラッグ後に矢印キーが効かない
- 事実: `docs/index.html:1171` の `pointerdown` → `event.preventDefault()` が互換 mousedown を抑止し、フォーカスが当たらない。`tabindex="0"`（677行）とキーボード操作（1196行〜）が死ぬ。
- 修正: `preventDefault()` の直後に `paneResizer.focus();` を1行追加。**dblclick は preventDefault の影響を受けない**ため触らない（レビューの後半部分は過大評価）。
- 確認: ドラッグ→離す→そのまま矢印キーで幅が動く。ドラッグ中に文字選択が起きない。

### #4 ウィンドウ縮小時に幅の上限が再適用されない
- 事実: `applySidebarWidth`（1135-1142行）の上限計算は適用時のみ。`addEventListener("resize"` は両HTMLに存在しない（grep 0件）。インライン `--sidebar-width` は `@media (max-width:920px) :root{--sidebar-width:245px}`（553-554行）に必ず勝ち、`grid-template-columns` 第3トラックは `minmax(0,1fr)`（53行）なので0まで潰れうる。実害帯は 701〜920px。
- 修正: 復元した保存値を `preferredWidth` に保持し、`window` の `resize` で**インライン値があるときだけ**再クランプ。**localStorage は上書きしない**（窓を広げれば希望幅に戻る）。≤700px は `.app-shell{display:block}`（588行）・`.pane-resizer{display:none}`（591行）なので何もしない。
- 確認: 1400px で620pxへドラッグ → 780pxへ縮小で**サイドバーが460pxへ再クランプ**され記事ペインが約314px確保される（修正前は620pxのままで記事ペイン約154px）→ 1400pxへ戻すと620pxに復帰。

### #6 ドラッグ状態が residual で残る
- 事実: `endResize`（1182-1192行）は `pointerup`/`pointercancel` のみに束縛され、いずれも `hasPointerCapture` ガードあり。暗黙解放時に `body.resizing`（`user-select:none` ＋ `col-resize`・75行）がリロードまで残る。
- 修正: クラス解除だけ行う `cleanupResize()` を切り出し、`lostpointercapture` に束縛（ガードを通さない形）。幅の保存は `endResize` 側に残す。
- 確認: ドラッグ中に `#paneResizer` を DevTools で `display:none` にして離す → 本文のテキストが選択できる。

### #8 ハイコントラストでフォーカスが見えない
- 事実: `docs/index.html:70-72` が `:hover` / `:focus-visible` / `.dragging` を同一宣言に束ね `outline: none`。`forced-colors` 指定は両HTMLに0件。キーボード操作が唯一の非マウス手段なので影響が大きい。
- 修正: `:focus-visible` を独立させ `outline: 2px solid var(--teal-600); outline-offset: -2px;` ＋ `@media (forced-colors: active)` で `Highlight`。hover/dragging の見た目は不変。**余白・文字サイズは増やさない**（高密度方針）。
- 確認: Tab でリングが見える／forced-colors エミュレーションでも見える。

**コミット粒度**: #8 → #6 → #2 を sonnet で1コミット、#4 を opus で別コミット。各コミット前に 55テストを回す。

---

## B評価（方針を決めてから・4件）

### #9【ユーザー追加要望】金利履歴タブの右側にサイト名を出す
- 内容: タブのすぐ右（先頭）に `● 十勝金融機関News` を置き、その右に既存の「金利履歴ビューアー ＋ 説明文」を並べる（ユーザー選択済み）。
- 実装案: `docs/index.html:654` の `p.tab-note` 先頭に `<b class="tab-brand"><span class="pulse-dot"></span>十勝金融機関News</b>` を追加し、rate ビューのみ表示する既存の仕組み（`body[data-view="rate"] .tab-note`・531行）に乗せる。`.tab-note` は `align-items: baseline; gap: 10px`（506-518行）なので、`.pulse-dot`（101-107行）を再利用するなら `align-items` の扱いだけ調整する。ニュース側はトップバー（682行）に既にサイト名があるため重複表示しない。
- `docs/rate-history.html` は触らない（埋め込み時は `html`/`body.embedded` でヘッダーごと非表示・284行）。
- 決めるべき点: 狭幅（≤700px）で3つのうち何を残すか（#5と連動）。
- 確認: 金利タブでタブのすぐ右にサイト名→金利履歴ビューアー→説明文の順。ニュースタブでは出ない。

### #5 タブ行の狭幅対策
- レビューの「金利履歴タブ右端が切れる」は**両エージェントとも再現しないと判定**（`.product-tab{flex:none}`・486行は今回の意図的防御なので外さない）。
- 実在する問題: `.tab-note b`（520-524行）に `overflow`/`text-overflow` が無く、かつ `.tab-note` 自身はフレックスコンテナのため自前の `text-overflow`（517行）が不活性。結果「金利履歴ビューアー」が省略記号なしにハードカットされる（`.tab-note span`（526-528行）は自前の overflow 指定があるので省略が効く）。
- 修正案: ≤700px で `.tab-note b { display: none }`、または `.tab-note b { flex:0 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis }`。#9 で要素が増えるため優先度が上がる。
- 任意: `.product-tabs { overflow: hidden }`（470-483行）をページズーム150%以上の保険として足すか。副作用として、はみ出しに気づけなくなる。

### #7 金利タブ初回表示のヘッダーちらつき
- 事実: `docs/rate-history.html:335` のクラス付与が `<header class="site-header">`（297行）より後。ただし**「開くたび」は誤り**で、`rateLoaded` ガード（index.html:1217-1221）により iframe 読込は初回のみ＝セッション中最大1回。`#rateFrame` の固定高さにより親のレイアウトジャンプは起きない。
- 直す場合: `<head>` のスクリプトで `documentElement` にクラスを付け、CSS を `html.embedded .site-header` へ。**`tests/test_viewer_layout.py:39-43` が `body\.embedded` セレクタと `window.self !== window.top` をピン留めしているため、同一コミットでテスト更新が必須**。`tests/test_rate_history_layout.py` がピン留めする `.site-header` のベース規則は絶対に触らない。
- 決めるべき点: (a) まず実測して目に見えたときだけ直す (b) 予防的に直す。

### #3 リサイザーの ARIA
- 事実は正しい（`aria-valuemin`/`max` 無し・初期状態では `aria-valuenow` も無い・`resetSidebarWidth`（1154行）が削除・px値が暗黙0-100レンジで読まれる）。
- ただし `docs/re-review-plan-2026-08-17-uiux.md:100` で ARIA 系は「所有者専用サイトのため公開範囲拡大時に一括対応」＝D評価と既決。
- 決めるべき点: (a) 誤情報だけ消す（`aria-valuenow` の設定・削除をやめる） (b) `aria-valuemin="180" aria-valuemax="620"` を677行に足して正しくする（D評価ポリシーに1件だけ例外を作る）。
- **依存**: どちらの案も `applySidebarWidth`（1140行）/`resetSidebarWidth`（1154行）＝#4と同一関数を触るため、必ず #4 の後に実施する。

---

## C評価（今は触らない・1件）

### #1 モバイルの絞り込みパネル位置（`.filter-popover { inset: 162px … }`・613行）
- バグは実在し、今回トップバーに見出しを足したことで約26px ズレが悪化する方向。ただし162px は `c80152a` 由来の**既存**マジック値で、`docs/re-review-plan-2026-08-17-uiux.md:95` に「#16 ポップオーバー位置＝C評価・実機確認待ち」、HANDOFF.md 残課題2 に「PCのみで使うなら見送りで確定してよい」と既決。
- `.filter-popover` は2ファイル同型修正契約の代表例。`docs/index.html` だけ直すと非対称になる。
- 判断材料: スマホで公開URLを使うか。使うなら #16 と束ねて `getBoundingClientRect` 基準へ根治（両ファイル同型）、使わないなら見送り確定。**162px を別のマジック値へ差し替える対症療法は禁止**。

---

## 保護対象（触らない）

- `.product-tab { flex: none }`（486行）＝今回の意図的防御。外すと逆デグレ。
- `tests/test_rate_history_layout.py` がピン留めする `.site-header` のベース規則（`display:flex`・`p` の `0.8rem`）。
- iframe分離と2ファイル複製（両ファイル同時・同型修正が契約）。
- 高密度レイアウト（余白・文字サイズを増やす方向の修正は方針違反）。
- `tests/test_viewer_layout.py:54` の `\.pane-resizer\s*\{\s*display:\s*none;\s*\}` 厳密マッチ。モバイル用ルールにプロパティやコメントを足すと落ちる。

## デグレ源（機械置換の禁止）

- `docs/index.html:654` の「金利履歴ビューアー」「帯広エリア…」「左が最新、右が過去です。」は `docs/rate-history.html:298-299` の**完全な複製**。片方だけ変えても両テストが通る。文言変更時は必ず両方を直す。
- #9 実施後は「十勝金融機関News」が `docs/index.html` 内の2箇所（トップバー682行・タブ行）に存在する。**一括置換禁止**。変更時はトップバーとタブ行の両方＋`tests/test_viewer_layout.py:18-19` を確認する。

## 実装モデルと着手順（切り替え2回）

| 順 | 作業（指摘番号） | モデル | 理由 |
|---|---|---|---|
| 1 | #8 → #6 → #2（1コミット） | sonnet | 単独CSSブロックと数行のJS。ゲート（テスト＋目視）で回帰を検出できる |
| 2 | #4（別コミット） | opus | 状態保持とメディアクエリ境界の設計判断あり |
| 3 | #9 ＋ #5 | opus | レイアウト判断＋文言の二重管理契約に触れる |
| 4 | #7（判断次第） | opus | ピン留めテストと同時変更 |
| 5 | #3（判断次第） | sonnet | 属性1行 or valuenow 削除。#4 の後に実施 |

## 未回答のユーザー確認事項

1. #7: (a) 実測してから直す / (b) 予防的に直す（テスト更新が確定）
2. #3: (a) `aria-valuenow` をやめて誤情報だけ消す / (b) min/max を足して正しくする
3. #9・#5: 狭幅（≤700px）で残すのは「サイト名のみ」か「サイト名＋短縮説明」か
4. #1: スマホで公開URLを使うか（使わないなら #16 ごと見送り確定）
5. #5 の `.product-tabs { overflow: hidden }` をズーム時の保険として入れるか
