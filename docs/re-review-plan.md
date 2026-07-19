# 再レビュー実装プラン（2026-07-19）

`/full-code-review`（4観点）→ `/re-review`（設計整合の再検証）で確定した、優先度付き実装プラン。
**このファイルはプラン記録。A・B評価項目は2026-07-19に実装・コミット済み。** 現在地の正は `HANDOFF.md`。

- 対象: `scripts/collect_and_send.py`, `send_report.py`, `send_resend.py`, `scripts/backfill_json.py`, `.github/workflows/weekly-news-report.yml`, `config.json`, `docs/index.html`, `scripts/test_ip_access.py`
- 判定方針: fail-open・出口防御・使い捨てスクリプトという一貫した設計思想を尊重し、思想と無関係の実害バグを最優先にする。

## ユーザー決定事項（2026-07-19）
- 手動送信スクリプト（#1/#3）: **残して修正**（共通化＋エスケープ、手動送信手段は維持）。
- ハードコード設定（#6/#17）: **star_keywords のみ config 化**。`[:60]` 等の定数化は追わない。
- Claude 経路（#11）: **温存**（使う直前にまとめて堅牢化）。
- 用途不明フラグ（#18）: **削除して確認**。

## A評価（即着手・安全）
- **#2＋#5 レポート消失の防止（最優先）**: `weekly-news-report.yml` のコミット工程に `if: always()` を追加。`collect_and_send.py:544-547` の `os.environ["RESEND_API_KEY"]/["REPORT_TO"]` を `.get` にし未設定なら送信スキップ。→ 送信失敗でも生成済み `output/*.md`・JSON が push される。確認: workflow_dispatch で成功時コミット。
- **#16 `import calendar` を先頭へ**: `date_n_months_ago` 内のローカル import をファイル冒頭へ。確認: `py_compile`。
- **#18 `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24` 削除**: workflow から削除し workflow_dispatch が緑になるか確認。

## B評価（方針決定済み・計画的に）
- **#3＋#1 手動送信の共通化＋エスケープ**: `_load_dotenv` と Resend 送信を共通モジュール（例 `scripts/emailer.py`）へ集約。`send_report.py`/`send_resend.py`/本番 `send_email` から利用。HTML 送信部は `html.escape(body)` を通す。手動2本は維持。確認: 各エントリで送信成功・本文が崩れない。
- **#6 star_keywords を config 化**: `config.json` に `"star_keywords": ["金利","キャンペーン"]` を追加。`apply_filters` は未指定時は現行2語をデフォルト。確認: config 変更で⭐付与が変わる。
- **#4 除外3ヶ月・日付パースの共通化**: `parse_ymd()` と `is_recent_excluded` を単一ヘルパに抽出し、`format_report`（md）と `build_report_data`（JSON）が同一ヘルパを通る形に。挙動不変。確認: `backfill_json.py` 再実行で `output/data/*.json` の diff ゼロ。

## C評価（今は触らない＝意図的設計・使い捨て・デグレ高）
次回セッションで「バグ」と誤認して直さないための記録。
- **#7・#12** backfill_json.py の注記→フラグ変換・除外列位置: 「使い捨て・将来 collect が直接 JSON を出すため不要」と冒頭明記。将来 md 廃止で不要化。
- **#8** `scrape_news_programmatic` の `a.parent.parent` 日付探索: 汎用スクレイパの割り切り。階層を絞ると7機関横断で回帰リスク大。本番で誤補完なしと検証済み（HANDOFF）。
- **#9・#15** URL スキーム検証・`safeUrl`: ヴューアー `docs/index.html` の `safeUrl` が http/https 以外を `#` に落とす出口防御。データは `urljoin` で絶対URL化済みで実害なし。
- **#10** defusedxml フォールバック: 本番は workflow で `pip install defusedxml` 保証。ローカル利便のための意図的縮退（改善余地は警告print程度）。
- **#11** Claude 経路（`extract_news_with_claude`）: 現 config に `use_claude:true` が無くデッドコード。温存し使う直前に temperature=0・max_tokens・JSON抽出強化・prompt injection 分離・リトライをまとめて。
- **#13** `test_ip_access.py`: Cursor Automation 疎通確認の使い捨て。`utcnow()`・urllib・TARGETS 二重管理は承知の割り切り。
- **#14** `fetch_page` の `apparent_encoding`: 北洋 cp932 誤設定を消した経緯（文字化け回避優先）の意図的既定。
- **#19** 送信元 `onboarding@resend.dev`: 独自ドメイン DNS 検証を要する別タスク。宛先は本人1件で運用成立。

## 着手順
1. #2＋#5（データ損失防止・独立・最優先）
2. #16・#18（軽微・安全）
3. #3＋#1（送信の共通化＋エスケープを一体で）
4. #6（star を config へ）
5. #4（最後・挙動不変・diff0で検証）

## デグレ注意（実装時）
- #4: md と JSON が必ず同一ヘルパを通す（片方だけ変えると除外件数がズレる）。
- #2/#5: `if: always()` 追加時、収集クラッシュで空/壊れ output を commit しないよう成功判定を意識。
- #6: star 用の独立キーにする（include_keywords と混同すると通過条件が変わる）。
- #8（もし将来触るなら）: 7機関横断の回帰確認が必須（fixture 整備前提）。
