# 全体レビュー → 再レビュー 実装プラン（2026-09-28）

対象: リポジトリ全体（7505d90 時点）。全体レビューは Codex と Claude の両方で A 品質・B バグ・C セキュリティ/性能・D Claude API の4観点を実施（Claude の C は利用上限で一度止まり、同日夜に再実行）。再レビューも両方で照合役・評価役を実施し、プラン採点は Claude 80点／Codex 70点。低い方（70点）を採ってグリルを行い、本人回答を反映して実装した。

## 本人の決定（2026-09-28 グリル）

| 論点 | 決定 |
|---|---|
| #1 汎用抽出で記事リンク0件 | 失敗（extract_failed）扱いにする |
| #39 上流で住宅ローン商品が増減した週 | ニュース配信は止めない。商品突合は収集後の警告ステップへ移す |
| #2 同日再実行で Resend 409 | 成功（緑）扱いにし、送信済みと手動送信（send_report.py）をログで案内 |
| #11 上流の機関名・商品名の欠落 | 既存行は前回の名前を保つ。新商品だけ名前必須で取り込みを止める |
| #3 北洋の今年分XMLが404 | 1月だけ許容。2月以降の404は失敗（URL変更を見逃さない） |
| #20 共通キーワード | config.json の common_include_keywords に1か所化 |
| #10 同日再取込で金利が未確認 | 前回の値を残す（現状維持。その日に確認した実値のため） |

## 実装済み（A と B）

| # | 内容 | 変更箇所 | 確認 |
|---|---|---|---|
| 1 | 汎用抽出0件を抽出失敗に | collect_and_send.py `_scrape_programmatic_institution` | リンク無しHTMLで extract_failed のテスト |
| 3 | 北洋の今年分404を1月だけ許容 | collect_and_send.py `_is_new_year_feed_not_published` | 1月/2月/前年404のテスト（FakeResponse に status_code を追加） |
| 20 | 共通キーワードの1か所化 | config.json、`normalize_config`、`load_config` | 保存済み22レポート9,923件で通過・除外の差分0件 |
| 34 | use_claude:true を設定検証で拒否 | collect_and_send.py `validate_config` | true で失敗するテスト |
| 35 | 汎用抽出を60件で打ち切り | collect_and_send.py `MAX_PROGRAMMATIC_ITEMS` | 上限テスト（結果は従来の items[:60] と同一） |
| 25 | 冒頭説明を凍結中に合わせる | collect_and_send.py 冒頭 | — |
| 2 | 409 invalid_idempotent_request を送信済み扱い | emailer.py `_is_already_sent_with_key` | 本文をログに出さないこと・他の409は失敗のままのテスト |
| 8 | 応答JSONが dict 以外でも成功 | emailer.py | 配列応答のテスト |
| 9 | 金利履歴の部分更新を防ぐ | update_rate_history.py `prepare_dataset_update`、main | マイカー不正で住宅ファイルが書かれないテスト。書き込み自体の途中失敗は対象外（本番は上流が成功時だけ push） |
| 11・37 | 名前と URL の検証 | update_rate_history.py `clean_display_name`・`is_http_url`・`validate_history` | 新商品の名前欠落で停止、既存行は前回名を保持、javascript: を保存しないテスト |
| 12 | 金利タブの商品順を JSON 初出順に | rate-history.html `productOrder` | 両JSONの機関内商品順一致テスト、ブラウザーで釧路・北見・労金の順を確認 |
| 13 | プロトタイプ名を URL から受け付けない | rate-history.html `resolveDatasetKey`、index.html `isReportView` | Node で constructor/__proto__/toString 等を実行するテスト、ブラウザーで #constructor・?dataset=constructor を確認 |
| 15 | 未読込で検索しても例外にしない | rate-history.html・loan-features.html `render` 冒頭 | ブラウザーで state.data=null のまま検索し例外なし・案内表示維持 |
| 16 | 初期化失敗でも既定タブを開く | index.html 末尾 | catch で失敗を表示し finally で switchView |
| 17 | 未使用 bankOrder() 削除 | loan-features.html | 表示順不変 |
| 21b | 取得のキャッシュ方針を揃える | rate-history.html（履歴JSON・条件JSONとも no-store） | ブラウザーで `?v=` 付き取得を確認 |
| 24 | JA木野の URL 日付テストを有効化 | tests/test_collect_and_send.py | 日付抽出に一致する URL で補完しないこと |
| 39 | 商品突合でニュース配信を止めない | workflow（FINPULSE_DEFER_DATA_SYNC_TESTS と後段の警告ステップ）、tests/test_loan_features.py | ワークフロー構造テスト、環境変数ありで4件スキップ |

## 保留（C）

- #5 汎用抽出の祖父要素からの日付: 既決 re-review-plan.md C#8。誤補完が実データで出たら専用抽出へ移す。
- #6 記事名の改行: 22レポートで実例0件。区切りを変えると重複除去キー（title,url）がずれて過去分と二重になる。
- #7 .env の引用符: 既決 08-17 C#14。
- #14 entryAt の索引化: HANDOFF の条件（調査日が数百件か実測遅延）まで保留。Node テストが関数を正規表現で抜き出している。
- #21 3HTML の共通化（ESモジュール）・保証料3値の色分け判断の重複: Node テストの仕組みごと壊れる。safeUrl の "#" と "" は既決の仕様差。Esc・件数表示の差は次の UI/UX レビューで扱う。
- #22 専用抽出の骨組み: 次に専用抽出を足すときにまとめる。
- #23 テストの脆さ: 既決 08-17 C#13。
- #33 Claude 経路の再有効化一式: 凍結方針どおり一括で（#34 で入口は閉じた）。
- #36 1リクエストの合計時間上限: 共通タイムアウト＋15分打ち切りの設計を尊重。遅延送信サーバーは未観測。

## やらない（D）

#4（lookback_days:0 と北洋2年。本番は90日）、#10（本人決定で現状維持）、#18・#19（次にその箇所を触るとき）、#26・#27（既決 D#17）、#28・#29・#38（使い捨て・延命しない）、#30・#31・#32（実害なし。#32 は Windows で python3 がストアのスタブに当たる危険もある）。

## 意図的設計の保護リスト

- 汎用抽出に機関別の条件分岐を足さない（SCRAPERS に専用抽出を足す）。
- 北洋「両年必須」の厳格化（ba2a064）は、1月の今年分404以外では崩さない。
- safeUrl の "#"（index）と ""（金利・条件）の違い。
- Claude 経路の本体には触れない（#34 は入口の設定検証のみ）。
- 金利タブは「機関でまとめる」並べ替えを残し、商品順だけ初出順にする。
- 記事名の作り方（`get_text(strip=True)`）は変えない（重複除去キー）。
- 同日再取込で null の金利は既存値を残す。

## デグレ多発箇所

- tests/test_viewer_layout.py の Node テストは HTML から関数を正規表現で抜き出して実行する（関数宣言の形を変えない）。
- tests/test_emailer.py はエラー本文をログに出さないことを固定している。
- tests/test_collect_and_send.py は collector.fetch_page / collector.requests.get を差し替えている。
- scripts/backfill_rate_history.py が update_history / write_history_atomic を import している。
- config.json を直接読むテストは `collector.normalize_config` を通すこと（合成前の include_keywords が空の機関は全件通過になる）。
