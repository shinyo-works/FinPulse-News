# 外部スケジューラ（cron-job.org）で週次を定刻に動かす — 手順書

**秘密の値（PAT）はこのファイルに書かない。** 置き場所と手順だけを書く。
現在地・残課題は `HANDOFF.md`、設計の決まりごとは `CLAUDE.md` が正。

---

## しくみ（1枚で）

```
月曜 08:05 JST  cron-job.org ──「今すぐ動かして」（workflow_dispatch）──┐
                                                                       ▼
月曜 09:00 JST  GitHub の schedule（控え。実際は 1〜3 時間遅れて起動）──▶ guard（判定）
                                                                       │
                     ┌─────────────────────────────────────────────────┤
                     ▼                                                 ▼
     今日（JST）収集と公開までやり遂げた実行がある            まだ無い（失敗・未実行・接続テストだけ）
     → 何もしないで終わる（メールも送らない）                 → 収集 → メール → 公開
```

- **毎月1日も同じ流れで動く**（2026-10-08 追加）。本命は cron-job.org の `FinPulse 月初（1日 8:05）`、
  控えは GitHub の schedule（1日 09:00 JST）。金利の取り込み（import-rate-history）も 1日 08:05 の中で動き、
  控えは 1日 11:00 JST。1日が月曜に重なった日は、判定が「今日すでに成功済み」で2回目を省略するので1回だけ。
  上流の金利調査ツールも毎月1日 07:00 に動く（向こうの `docs/external-scheduler.md`）。
- **本命は cron-job.org**。GitHub の `schedule` は順番待ちで 1〜3 時間遅れ、発火しない回もあるため、
  定刻は外部に任せる（kobetukabu・FX-prudential と同じ方式）。
- **控えは GitHub の `schedule`（月曜 09:00 JST）**。cron-job.org が止まった週・PAT が切れた週・
  本命が途中で失敗した週だけ、代わりに全部やる。
- 本命と控えは同時に走らない（`concurrency` で1本ずつ並ぶ）。後から来たほうは、前の実行が公開まで
  終わってから判定するので取りこぼさない。
- 同じ日のメールは2通目が止まる（配信日入りの冪等キー）。判定をすり抜けても2通は届かない。

### 判定の中身（`scripts/check_weekly_run.py`）

| 状況 | 動くか | 理由 |
| --- | --- | --- |
| `check_only` にチェック（接続テスト） | 動かない | cron-job.org の接続確認だけ。収集・送信・公開はしない |
| `force` にチェック（手動のやり直し） | 動く | 成功済みの日でも取り直したいとき |
| 今日（JST）収集と公開までやり遂げた実行がある | 動かない | 本命が済んだ週に控えを止める |
| 今日の実行が無い／失敗しかない／接続テストだけ | 動く | cron-job.org が止まった、メール送信だけ失敗した、一部機関が落ちた等 |
| 判定そのものが失敗（GitHub API の障害など） | 動く | 控えまで止めないため。最悪でも同日の取り直しが1回増えるだけ |

「やり遂げた」は実行全体の緑・赤ではなく、**収集ジョブと公開ジョブの両方が成功したか**で見る。
接続テストや省略した実行も全体は緑で終わるため、全体の色で見ると同じ日の本命まで止めてしまうから。
**接続テストは月曜の朝でも安心してできる**（本命・控えを止めない）。

一部機関の収集だけ失敗した週も、控えがもう一度収集する。メールは2通目が止まるので、
**メールは本命の内容、公開ページ（ヴューアー）は控えの取り直し後の内容**になる（許容と決定）。

---

## 本人（管理者）がやること（3ステップ・合計15分ほど）

### ステップ1: PAT（GitHub の合鍵）を1本作る — 約5分

**なぜ本人でないとだめか**: 合鍵の発行は GitHub アカウントの持ち主にしかできないため。

1. このリンクを開く → <https://github.com/settings/personal-access-tokens/new>
2. 次のとおり入れる

   | 項目 | 入れる値 |
   | --- | --- |
   | Token name | `finpulse-cron-dispatch` |
   | **Resource owner** | **`shinyo-works`**（個人名のほうではない。ここを間違えると 404 になる） |
   | Expiration | **Custom** を選び、**今日から1年後の日付**（選べる最長）。一覧に「No expiration」が出ても選ばない |
   | Repository access | **Only select repositories** → `FinPulse-News` **だけ** |
   | Repository permissions → **Actions** | **Read and write**（行の右端の `Access: No access ▼` を押して選ぶ） |
   | それ以外 | すべて **No access のまま**（Metadata の Read-only は自動で付く） |

3. 下の **Generate token** を押す
4. 出てきた `github_pat_...` を**コピーしてステップ2へ**（この画面を離れると二度と見られない）

> **「承認待ち（Pending）」と出たとき**: 組織が合鍵に管理者の承認を求める設定になっている。
> 管理者は本人なので、<https://github.com/organizations/shinyo-works/settings/personal-access-token-requests>
> を開いて、`finpulse-cron-dispatch` を **Approve** する。
>
> **Resource owner に `shinyo-works` が出てこないとき**: 組織が合鍵を受け付けない設定になっている。
> <https://github.com/organizations/shinyo-works/settings/personal-access-tokens> で
> 「Allow access via fine-grained personal access tokens」を選んで保存してから、1 に戻る。

### ステップ2: cron-job.org にジョブを1本登録する — 約7分

**なぜ本人でないとだめか**: 本人の cron-job.org アカウントの設定で、合鍵を貼る作業があるため。
（アカウントは kobetukabu・FX で使っているものと同じ）

1. このリンクを開く → <https://console.cron-job.org/jobs/create>
2. **COMMON（基本）**

   | 項目 | 入れる値 |
   | --- | --- |
   | Title | `FinPulse 週次（月曜 8:05）` |
   | URL | `https://api.github.com/repos/shinyo-works/FinPulse-News/actions/workflows/weekly-news-report.yml/dispatches` |
   | Enable job | **オン** |
   | Execution schedule | **Custom**（分・時・日・月・曜日を選ぶ表）→ 分 `5`、時 `8`、日 すべて、月 すべて、**曜日 月曜だけ** |
   | Timezone | **`Asia/Tokyo`**（画面の版によって、この欄はスケジュールの近くか ADVANCED 側にある） |

3. **ADVANCED（詳細）**

   | 項目 | 入れる値 |
   | --- | --- |
   | Request method | **POST**（既定の GET のままだと 404 になる） |
   | Headers（3行追加） | `Accept` = `application/vnd.github+json` |
   |  | `Authorization` = `Bearer github_pat_...`（**`Bearer` と半角スペースを先頭に付ける**） |
   |  | `X-GitHub-Api-Version` = `2022-11-28` |
   | Request body | **まずテスト用**: `{"ref":"main","inputs":{"check_only":"true"}}` |

4. **NOTIFICATIONS（通知）** … **実行失敗時のメール通知をオン**（起動が落ちたことに気づける唯一の経路）
5. **Create（保存）** を押す

### ステップ3: 接続テスト → 本番の中身に戻す — 約3分

1. ジョブ一覧 <https://console.cron-job.org/jobs> で今のジョブを開き、**Test run（テスト実行）** を押す
   （月曜 8:00〜8:15 は避ける。本命と重なると、本命が順番待ちから外れて控えの 9:00 以降に遅れることがある）
2. 結果が **204** なら成功（本文が空なのも正常）。204 以外なら下の表で原因を見る
3. GitHub 側でも確認する → <https://github.com/shinyo-works/FinPulse-News/actions/workflows/weekly-news-report.yml>
   一番上に新しい実行があり、**guard だけが緑、ほかの2つは灰色（スキップ）** なら成功。
   実行を開くと「週次レポートの実行判定: 実行しない／接続確認のみ（check_only）」と出ている。
   **メールは届かない**（届いたら設定がおかしいので教えてください）
4. **大事**: ジョブを開き直し、ADVANCED の Request body を **本番用に書き換えて保存**する

   ```
   {"ref":"main"}
   ```

   ここを戻し忘れると、毎週月曜が接続テストだけで終わる（その場合でも 09:00 の控えが動くので
   メールは届くが、1〜3 時間遅れる）。

| cron-job.org の結果 | 原因 |
| --- | --- |
| 204 | 正常（起動を受け付けた） |
| 401 | 合鍵が違う・期限切れ・`Bearer ` の付け忘れ |
| 403 | Actions の権限が Read のまま／組織の承認待ち（ステップ1の「承認待ち」） |
| 404 | URL の打ち間違い、Resource owner が `shinyo-works` でない、対象リポジトリの選び忘れ |
| 422 | Request body の JSON が壊れている |

**204 は「起動を受け付けた」だけ。** メールや公開の成否は GitHub の実行結果（緑／赤）で見る。

---

## 毎月1日のジョブ（2026-10-08 追加）

週次のジョブ `FinPulse 週次（月曜 8:05）` を複製し、次の2か所だけ変えたもの。URL・ヘッダー（合鍵）・
Request body（`{"ref":"main"}`）は週次と同じ。

| 項目 | 入れる値 |
| --- | --- |
| Title | `FinPulse 月初（1日 8:05）` |
| Execution schedule | 分 `5`、時 `8`、**日 `1` だけ**、月 すべて、**曜日 すべて** |

合鍵（PAT）を作り直したときは、週次と月初の**両方のジョブ**の `Authorization` を差し替える。

---

## 運用

- **合鍵の期限は1年**。切れると月曜 08:05 に cron-job.org から失敗メールが届く（その週は控えが
  09:00 以降に動くので配信は止まらない）。新しい合鍵をステップ1で作り、ジョブの `Authorization` を差し替える。
- **手でやり直したいとき**: GitHub の Actions 画面 → 「週次金融機関新着情報レポート」→ **Run workflow**。
  その日すでに成功していたら何もせず終わるので、取り直したいときは **force** にチェックを入れる
  （メールは同日2通目が止まる。送り直すなら `send_report.py`）。
- **メールが届かなかった週**: 本命・控えとも、同じ日の2通目を止める仕組みで「送信済み」と判断することがある。
  GitHub の実行が赤で終わっていてメールが無い場合は、`send_report.py` で手動送信する。
- **時刻を変えるとき**: 本命は cron-job.org の Execution schedule（週次・月初の2本）、控えは
  `.github/workflows/weekly-news-report.yml` の `cron`（UTC で書く。JST − 9時間）。
  控えは本命より後ろに置くこと（前に置くと控えが先に全部やってしまう）。
- **cron-job.org をやめるとき**: ジョブを無効にするだけでよい。控えの schedule が毎週 09:00 以降に動き続ける。
- 上流（報告自動化ツール）も同じ朝に FinPulse へ金利履歴を push する。どちらの push も競合したら
  取り込み直して最大3回やり直す（触るファイルが別なので衝突しない）。
