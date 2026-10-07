# 再レビュー実装プラン（2026-08-17）

- 原典: 2026-08-17 の `/full-code-review`（4観点マルチエージェント）で挙がった24指摘を、`/re-review` でコード・git履歴・HANDOFF.md・`docs/re-review-plan.md`（2026-07-19版）と照合して作成。
- 採点: プラン監査エージェントによる 90/100（合格ライン80）。減点理由（根拠の過大表現1件・HANDOFF行番号ズレ2箇所・ゲート粒度2件など）は本書に反映済み。
- 状態: **A評価6件・B評価4件とも実装済み（2026-08-17・全52テスト合格）**。C/D評価は本書の判定どおり保留・却下。
- ユーザー決定（2026-08-17）: #24a は「維持に変える」を採用。#11 は「今回やる」を採用し、7機関の実ページで文字化けなしを検証済み。#2 は「廃止注記で残す」を採用。

## 24指摘の一覧（番号 → 一行要約）

| # | 初回評価 | 要約 | 再検証後の区分 |
|---|---|---|---|
| 1 | 高 | 日付抽出（extract_date_from_text / from_url）が実在日検証なしで不正日付を通し、ヴューアーJSON三点セット生成が丸ごと失敗 | A |
| 2 | 高 | docs/routine-prompt-template.md が旧構成の残骸（PAT平文指示・コード直埋め・受信側なしdispatch） | A |
| 3 | 中 | タイトル中の `\|` が Markdown 表と backfill 逆パースを壊す | C |
| 4 | 中 | 外部由来アイテムの入口サニタイズなし（hokuyo_xml の href スキーム未検査ほか） | B |
| 5 | 中 | publish ジョブの git push に競合リトライなし | A |
| 6 | 中 | Claude 抽出経路の max_tokens 切り捨て→正常0件化・境界防御なし | C |
| 7 | 中 | collect_and_send.py 約1,080行の責務集中 | C |
| 8 | 中 | 同じ知識の分散（注記文字列・方式ラベル・マーカーパス・スクレイパー登録・日付パース） | B |
| 9 | 中 | backfill_json.py の出力が validate を通らない・schema_version なし | C |
| 10 | 中 | ヴューアー2画面の複製・毎キーストローク全再構築・DATA_ROOT 構成依存 | C |
| 11 | 中 | fetch_page が requests 私有属性 _content / _content_consumed に依存 | B |
| 12 | 中 | テンプレ「過去7日」vs config lookback_days:90 の齟齬 | D（#2で消滅） |
| 13 | 中 | test_rate_history_layout.py の CSS ピクセル値・JS 文字列への密結合 | C |
| 14 | 低 | emailer.load_dotenv が引用符・BOM 未処理 | C |
| 15 | 低 | send_report.py の glob `????-??-??.md` が緩い | A |
| 16 | 低 | scripts/test_ip_access.py の utcnow 非推奨ほか | D |
| 17 | 低 | モデルID・UA 文字列のマジックリテラル | D |
| 18 | 低 | Claude 応答の `\[.*\]` 貪欲マッチ | C（#6と一体） |
| 19 | 低 | backfill_rate_history.py の sorted(reports) が dict 比較に到達しうる | A |
| 20 | 低 | update_rate_history.normalize_date が桁数のみで判定 | D（事実誤認） |
| 21 | 低 | rate-history.html の手動入力3機関名ハードコード | C |
| 22 | 低 | defusedxml 未導入時のローカル縮退 | D |
| 23 | 低 | 送信元が onboarding@resend.dev のまま | C |
| 24 | 低 | UX: (a) 日付切替で機関絞り込みリセット / (b) 空状態文言の実態不一致 | (a)=B / (b)=A |

## 再検証で確認した前提（設計原則）

- 収集パイプラインは **fail-open**（一部失敗でも成果物を保存し、Actions の終了コードだけで通知）＋「入口は緩く・出口で厳格検証」が設計原則（HANDOFF.md 2026-08-01 の記録・CLAUDE.md 設計決定事項）。
- **Claude 抽出経路は凍結中**（HANDOFF C評価#8:「再有効化時に一括対応・部分修正は安全になった誤認を生むため行わない」）。config に use_claude 機関ゼロ、workflow に ANTHROPIC_API_KEY 未注入。
- `backfill_json.py` / `test_ip_access.py` は**使い捨て**（docstring・HANDOFF D#14 に明記）。
- ヴューアー2画面の複製は **iframe 分離の意図的帰結**（HANDOFF 113行 #17・126〜127行: 同名シンボル衝突回避のため1ファイルに統合しない）。safeUrl の "#" vs "" は仕様差（ニュース側は常に a 要素を出す・金利側は URL なしならリンク自体を出さない）。
- レイアウトテストの厳密一致は**承認済みレイアウトのピン留め**（HANDOFF 58行: 30px検索欄・セル密度・日付見出し固定の4テスト）。
- 初回レビューの補正: #1 の「Actions 失敗」は正しいが **Markdown 保存とメール送信は継続する**（fail-open は機能している）。#2 の PAT はプレースホルダで実値漏洩なし。#20 は strptime による実在日検証が既にあり事実誤認に近い。#21 の「data_sources から生成」案は現スキーマにその情報が無く実装不能。

## A評価（即着手・6件）

### #1 日付抽出の実在日検証（最優先）
- 対象: `scripts/collect_and_send.py` の `extract_date_from_text` / `extract_date_from_url`。return 前に既存 `parse_ymd`（227行付近）で検証し、不正なら次候補または `""` へ（text 側は re.finditer で全候補走査）。
- 方針: 不正日付は「**空欄化して残す**」＝ fail-open 維持（既存テスト `test_lookback_keeps_cutoff_and_unknown_date`「日付不明は残す」と整合）。除外へ落とす案は不採用。捨てた日付文字列とタイトルは print でログに残す（黙殺防止）。
- ゲート: `tests/test_collect_and_send.py` に電話番号「0155-24-1234」・13月・2月30日の回帰テストを追加＋既存40テスト。
- モデル: sonnet

### #2 routine-prompt-template.md の廃止明記
- 対象: `docs/routine-prompt-template.md` 冒頭に「廃止（2026-05-26 の `05750ec` で Actions 週次実行へ移行済み・使用禁止）」を明記して残す（履歴参照用・削除しない）。同時に CLAUDE.md の採用ツール表「Claude ルーティン ✅」行を「廃止（Actions へ移行）」へ更新（放置すると虚偽記載が孤立する）。
- 効果: #12（7日 vs 90日の齟齬）はこれで消滅。
- ゲート: ドキュメントのみ。テスト不要。
- モデル: sonnet

### #15 send_report.py の glob 絞り込み
- 対象: `send_report.py` 38行。glob 後に `re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem)` でフィルタ（collect 側 `list_report_dates` と同じ流儀）。
- ゲート: `tests/test_send_report.py` に偽名ファイル（abcd-ef-gh.md）混入テスト1本。
- モデル: sonnet

### #19 backfill_rate_history の sorted に key 明示
- 対象: `scripts/backfill_rate_history.py` 79行。`sorted(reports, key=lambda r: (r[0], r[1]))`。
- ゲート: 同一調査日の2レポートを入力する tie ケースの回帰テストを1本追加（既存テストは tie を踏まないため）。
- モデル: sonnet

### #24b 空状態文言の分岐修正
- 対象: `docs/index.html` 828行付近。「除外情報のみ表示しています。」を状態に応じた文言分岐へ。期待文言の4状態マトリクス:
  - showExcluded OFF × 除外あり →「対象期間内の通過記事はありません。除外された記事は「除外を表示」で確認できます。」
  - showExcluded OFF × 除外0件 →「対象期間内の記事はありません。」
  - showExcluded ON × 除外あり →「通過記事はありません。除外情報のみ表示しています。」
  - showExcluded ON × 除外0件 →「対象期間内の記事はありません。」
- ゲート: 自動テストなし。公開URLまたはローカルで上記4状態を目視確認。
- モデル: sonnet

### #5 publish ジョブの push リトライ
- 対象: `.github/workflows/weekly-news-report.yml` 81〜95行。commit 後に「push 失敗 → git pull --rebase → 再push」最大3回のループ。
- 根拠: 2026-08-03 は本ワークフローと別リポジトリの金利調査ツール（当時は Deploy Key 経由）の push が同日に発生（20:53 UTC / 23:12 UTC、約2時間20分の間隔）。**衝突は未発生**だが、実行時間の変動や手動 dispatch で窓が重なれば push 失敗が起きる構図（concurrency は自リポジトリ内のみ有効）。
- 注意: commit → push → 失敗時 rebase → 再push の順序（`rm -f output/viewer-json-ready.txt` とステージ済み変更が rebase で巻き戻らないこと）。両者の変更は output/ と docs/data/ で非交差のため rebase 自体は安全。3回失敗時は fail-open 原則どおりジョブを失敗表示（成果物は artifact に保存済み）。
- ゲート: ローカル検証不可。次回 workflow_dispatch 手動実行で確認。`tests/test_workflow_security.py` へのリトライ存在アサートは任意。
- モデル: opus（CI のみで検証・順序設計にリスク）

## B評価（方針を決めてから・4件）

### #4 入口サニタイズ（範囲は XML / programmatic のみ）
- `scrape_hokuyo_xml`（482行付近）の href スキーム検査が programmatic 経路（420行付近）と非対称。共通ヘルパー化して XML / programmatic に適用。**Claude 経路は凍結決定（C#8）に従い触らず**、再有効化時に同ヘルパーへ合流させる。ヘルパーは #8 のディスパッチ辞書化を見越した配置（モジュールレベル関数・注記定数の隣）にする。
- 不正項目は「その項目だけ除外」＋除外件数と理由の print 必須（黙殺防止）。
- ゲート: 不正URL・不正型が項目単位で除外されるテストを新設。
- モデル: opus

### #8 同じ知識の分散の解消（本体内のみ）
- 対象: (a) 注記文字列の参照一元化（**値は1文字も変えない** — by-institution.json の重複除去キーが過去分とズレるため）、(c) viewer-json-ready.txt のパス導出一本化、(d) スクレイパーのディスパッチ辞書化＋ VALID_SCRAPERS の導出、(e) `filter_by_lookback` / `get_fallback_item` を `parse_ymd` へ寄せる（**ValueError → 残す挙動の維持が絶対条件**）。backfill 側の重複は使い捨てのため触らない。
- ゲート: 既存40テスト＋注記往復テスト1本。
- モデル: opus

### #11 requests 私有属性依存の解消
- 対象: `scripts/collect_and_send.py` 189〜191行。`fetch_page` を bytes 自前デコード（encoding指定 → HTTPヘッダ charset → charset_normalizer → utf-8）へ。
- リスク: 北洋 cp932 の経緯（re-review-plan C#14）があり、判定挙動の変化で文字化け→抽出全滅の恐れ。Shift_JIS 固定HTMLのデコードテスト新設＋7機関の実ページでローカル確認が必須。
- 選択肢: (a) 今回の opus 塊で実施 / (b) requests のバージョン更新タスクと抱き合わせに延期（requirements.lock 固定運用下では緊急性低）。
- モデル: opus

### #24a 日付切替時の機関絞り込み維持
- ユーザー判断待ち: (a) 「維持する」へ変更（既存選択と新レポート機関の積集合・空なら全選択へフォールバック） / (b) 「毎回リセット」が意図仕様として現状維持。
- モデル: opus（状態管理・自動テストなし・目視検証）

## C評価（保留・9件）

- **#3 `|` エスケープ**: HANDOFF C#9 に「Markdown を機械入力に使う方針へ変わる場合だけ出力エスケープと parser を一体設計」と記録済みの意図的割り切り。単独修正は過去 Markdown との表記差で backfill 再実行時の重複リスク。
- **#6 / #18 Claude 経路**: C#8「再有効化時に一括対応・部分修正禁止」のユーザー決定に従う。
- **#7 モノリス分割**: テスト40件の mock.patch パスと backfill の import が構造前提。次の大きな機能追加（Claude 経路再有効化等）と抱き合わせ。
- **#9 backfill の検証ゲート**: 使い捨て宣言済み。再利用の必要が生じた時に validate＋schema_version＋寛容化を一体で。
- **#10 ヴューアー複製・性能・DATA_ROOT**: 複製は iframe 分離の意図的帰結。safeUrl の差は仕様差（"#" に統一すると金利側の無リンク分岐が壊れる）。性能は現規模（11調査日×48行）で問題なし。DATA_ROOT は「Pages = main /(root)」の確定契約内。
- **#13 レイアウトテスト**: 承認済みレイアウトのピン留め（意図的）。次に正当なスタイル変更を邪魔した時点で、その変更と同じコミットで構造レベルへ緩和。
- **#14 load_dotenv**: .env.example は引用符なし形式で自家製パーサの契約内。BOM 問題の実害が出たら utf-8-sig 化。
- **#21 手動機関名ハードコード**: data_sources に情報が無く、動的化はスキーマ拡張（金利調査ツール側のreport-tool との契約変更）が先。機関の顔ぶれが変わった時にセットで。
- **#23 送信元ドメイン**: 独自ドメイン取得というインフラ判断が前提（re-review-plan C#19 で別タスク化済み）。

## D評価（やらない・5件）

- **#12**: #2 の廃止で消滅。
- **#16 test_ip_access.py**: HANDOFF D#14「使い捨て診断・延命しない」決定済み。
- **#17 定数化**: re-review-plan「ハードコード設定は star_keywords のみ config 化。定数化は追わない」決定済み。
- **#20 normalize_date**: strptime 実在日検証が既にあり事実誤認に近い。寛容さは複数形式受理の明示的仕様。
- **#22 defusedxml 縮退**: 本番は requirements.lock ハッシュ固定＋ --require-hashes で保証済みの意図的縮退。

## 実装モデルと着手順（切り替え1回）

| 順 | 作業 | モデル |
|---|---|---|
| 1 | #1 日付検証＋回帰テスト | sonnet |
| 2 | #2 テンプレ廃止明記＋CLAUDE.md 更新 | sonnet |
| 3 | #15 glob 絞り込み | sonnet |
| 4 | #19 sorted key 明示＋tie テスト | sonnet |
| 5 | #24b 空状態文言（4状態） | sonnet |
| — | **区切り: ここでコミットし全テスト合格を確認してから opus 塊へ** | |
| 6 | #5 push リトライ | opus |
| 7 | #4 入口サニタイズ（XML/programmatic 限定） | opus |
| 8 | #8 知識の一元化（値不変） | opus |
| 9 | #11 デコード自前化（実施時期はユーザー選択） | opus |
| 10 | #24a 絞り込み維持（ユーザー判断後） | opus |

## リスク保護リスト（実装時に守る制約）

- 注記文字列の「値」は不変（by-institution.json の重複除去キーが過去分とズレる）。
- `filter_by_lookback` の「ValueError → 残す」は仕様（テストが固定）。
- Claude 経路（collect_and_send.py 497〜531行）は凍結。#4 のヘルパー設計時も適用しない。
- safeUrl を "#" に統一しない（rate-history の無リンク分岐が壊れる）。
- workflow のリトライは commit → push → 失敗時 rebase → 再push の順序。
- `backfill_json.py` / `test_ip_access.py` は磨かない（使い捨て決定）。
- **#1 は将来の週次実行分のみに作用させる。過去 JSON の再生成・backfill 再実行は行わない**（不正日付→空欄化で `(title, date)` 系の重複除去キーが過去分と変わり得るため）。

## ユーザー確認事項（回答済み・2026-08-17）

1. #24a: **「維持に変える」を採用**（前回選択と新レポート機関の積集合・空なら全選択へフォールバック）。
2. #11: **「今回やる」を採用**。Shift_JIS デコードテスト新設＋7機関の実ページ取得で文字化けなしを確認済み。
3. #2: **「廃止注記で残す」を採用**。
