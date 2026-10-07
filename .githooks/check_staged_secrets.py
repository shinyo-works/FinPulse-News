"""Gitのステージ領域にある文字ファイルから、鍵の形をした文字列を探す。

このリポジトリは公開されている。鍵を一度コミットして push すると、消しても履歴やコピーに残る。
GitHub の push protection も効いているが、push の前（コミットの時点）で止めるための手元の関門
（2026-10-07 監査の衛生項目）。見つけても値そのものは表示しない。
"""

import os
import re
import subprocess
import sys

# 鍵の形（実物と同じ書式だけを狙う。説明文の「RESEND_API_KEY=」などには当たらない）
SECRET_PATTERNS = (
    ("Google API キー", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("Anthropic API キー", re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,}")),
    ("OpenAI API キー", re.compile(r"sk-(?:proj-)?[0-9A-Za-z]{32,}")),
    ("GitHub トークン", re.compile(r"github_pat_[0-9A-Za-z_]{20,}|gh[pousr]_[0-9A-Za-z]{30,}")),
    ("Resend API キー", re.compile(r"\bre_[0-9A-Za-z]{8,}_[0-9A-Za-z]{16,}")),
    ("秘密鍵", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


def find_secrets_in_bytes(blob: bytes) -> list[str]:
    """文字コードに関係なく鍵を探す（鍵の形は ASCII なので、生のバイトをそのまま照合する）。

    UTF-8 以外の文書（CP932 の日本語メモなど）も素通りさせない。UTF-16 は 0 バイトを
    取り除いた形でも照合する。
    """
    texts = [blob.decode("latin-1")]
    if b"\x00" in blob:
        texts.append(blob.replace(b"\x00", b"").decode("latin-1"))
    found = []
    for text in texts:
        for label in find_secrets(text):
            if label not in found:
                found.append(label)
    return found


def find_secrets(text: str) -> list[str]:
    """鍵の形をした文字列の種類を返す（値そのものは返さない）。"""
    return [label for label, pattern in SECRET_PATTERNS if pattern.search(text)]


def staged_paths() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def main() -> int:
    found_any = False
    for path in staged_paths():
        blob = subprocess.run(
            ["git", "show", f":{path}"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        found = find_secrets_in_bytes(blob)
        if found:
            found_any = True
            print(
                f"{path}: {'・'.join(found)} の形をした文字列があります（値は表示しません）。"
                "本物なら取り除き、その鍵は失効させてください。",
                file=sys.stderr,
            )
    return 1 if found_any else 0


if __name__ == "__main__":
    sys.exit(main())
