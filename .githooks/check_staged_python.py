"""Gitのステージ領域にあるPythonファイルだけを構文検査する。"""

import os
import subprocess
import sys


def staged_python_paths() -> list[str]:
    """追加・変更・リネームされたPythonパスをNUL区切りで安全に取得する。"""
    result = subprocess.run(
        [
            "git",
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--diff-filter=ACMR",
            "--",
            "*.py",
        ],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def main() -> int:
    for path in staged_python_paths():
        result = subprocess.run(
            ["git", "show", f":{path}"],
            check=True,
            stdout=subprocess.PIPE,
        )
        try:
            compile(result.stdout, path, "exec")
        except SyntaxError as exc:
            print(f"{path}:{exc.lineno}: {exc.msg}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
