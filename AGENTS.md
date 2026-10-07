# AGENTS.md

このリポジトリで作業する AI エージェント（Codex / Claude Code 両方）へ。
**このファイルは「入口」と「進め方」だけを書く。規律は `CLAUDE.md`、ライブ状態は `HANDOFF.md` が正。**

## まず読むもの（起動時に必ず）

1. **`HANDOFF.md`** — 現在地・残課題の唯一の正（ライブ状態）。最初にここで現在地を把握する。
2. **`CLAUDE.md`** — プロジェクト構成・採用ツール・設計決定（守る規律）。
3. **`scripts/collect_and_send.py`** — 収集・フィルタ・送信の本体ロジック。

`CLAUDE.md` の内容はこの `AGENTS.md` でも有効。矛盾する場合は `CLAUDE.md` を優先する。

## 役割分担（同じ情報を2か所に書かない＝SSoT）

| ファイル | 役割 | 更新頻度 |
|---------|------|---------|
| `CLAUDE.md` | 規律・構成・設計決定 | まれ |
| `AGENTS.md`（本ファイル） | 入口・進め方・引き継ぎプロトコル | まれ |
| `scripts/collect_and_send.py` | 収集ロジック本体（仕組みはコード内コメント参照） | 機能変更時 |
| `HANDOFF.md` | **現在地・残課題の正（ライブ状態）** | **毎節目** |

## 引き継ぎプロトコル（Codex ⇄ Claude Code・どちらで開いても一貫させる）

現在地・残課題は **git 管理の `HANDOFF.md` 1か所だけ**を正にする。Claude Code のメモリは git 管理外で Codex から見えないため、**メモリには現在地・残課題を書かず、「`HANDOFF.md` を正とする」ポインタだけ**を持つ。

**どのツールも守る:**

- **起動時**: `HANDOFF.md` を読んで現在地・残課題を把握する。メモリと食い違えば `HANDOFF.md` が正。
- **節目（意味のあるコミット）ごと**: 進捗・残課題・完了を `HANDOFF.md` に反映し、先頭の「最終更新」を書き換えて、**コードと同じコミットに含める**。
- **離れる前**: 上記を済ませてからコミット・プッシュする。
- ライブ状態だけを簡潔に保つ。完了履歴は git log、構成は `CLAUDE.md` に委ねる。

## スキル（Claude Code の作業手順・Codex も同じ規律を踏襲する）

ワークフロー系スキルはユーザーレベル（`~/.claude/skills/`）にあり Claude Code 専用だが、運用ルールは本ファイルの引き継ぎプロトコルに集約済み。Codex はそのプロトコルに従えばよい。

| スキル | いつ使うか |
|--------|-----------|
| `前回のつづき` | セッション再開時。`HANDOFF.md` とメモリから現在地を復元する |
| `引継ぎ` | セッション終了時。`HANDOFF.md` を更新して次回へ渡す |

## 進め方（このユーザーの作業スタイル＝必ず守る）

- ユーザーはプログラミング初〜中級。**専門用語は避け、たとえ話や現象ベースで説明する**。
- 既定は最後まで進める（共通ルール `~/.codex/AGENTS.md` の「作業の進め方」に従う）。停止条件に当たる分岐だけ、**選択肢＋推奨理由を添えて、関連する質問は1回にまとめて**確認する。それ以外は止まらない。
- エラー時は原因を分かりやすく説明する。
- 節目でコミット要否を確認する。push はユーザー確認後に行う（公開サイトへの反映になるため）。
- 応答・コミットメッセージ・コードコメントはすべて**日本語**で統一。
- PowerShell での `git commit` は here-string（`@' ... '@`）を使う。

## 検証コマンド（コミット前に通す）

```bash
python -m py_compile scripts/collect_and_send.py scripts/update_rate_history.py scripts/emailer.py scripts/check_weekly_run.py send_report.py send_resend.py
python -m unittest discover -s tests -v
```

pre-commit フック（`.githooks/pre-commit`）が、ステージした Python ファイルの構文を自動検査する。フックを**スキップしない**（`--no-verify` 禁止）。クローン直後に1回だけ有効化する:

```bash
git config core.hooksPath .githooks
```

## push 先（重要）

- push は **`git push origin main`** のみ。
- 参照専用プロジェクト `../AI-trend-weather-News` は**変更禁止**（Read のみ）。

---

現在地・残課題・環境メモは **`HANDOFF.md`** を参照（このファイルには書かない＝二重管理を避ける）。
