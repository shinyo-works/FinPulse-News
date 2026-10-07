import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class PreCommitHookTest(unittest.TestCase):
    def setUp(self):
        self.checker = (
            Path(__file__).resolve().parents[1]
            / ".githooks"
            / "check_staged_python.py"
        )

    def git(self, directory, *args):
        return subprocess.run(
            ["git", *args],
            cwd=directory,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def run_checker(self, directory):
        return subprocess.run(
            [sys.executable, str(self.checker)],
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # 親と子のUTF-8設定が異なっても、日本語の診断を同じ文字コードで読む。
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            encoding="utf-8",
        )

    def test_checks_staged_blob_instead_of_working_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            self.git(directory, "init", "--quiet")
            path = Path(directory) / "日本語 ファイル.py"

            path.write_text("def broken(:\n    pass\n", encoding="utf-8")
            self.git(directory, "add", "--", path.name)
            path.write_text("def fixed():\n    pass\n", encoding="utf-8")

            failed = self.run_checker(directory)
            self.assertEqual(1, failed.returncode)
            self.assertIn(path.name, failed.stderr)

            self.git(directory, "add", "--", path.name)
            path.write_text("def broken_again(:\n    pass\n", encoding="utf-8")

            passed = self.run_checker(directory)
            self.assertEqual(0, passed.returncode, passed.stderr)


class SecretHookTest(unittest.TestCase):
    """公開リポジトリなので、鍵の形をした文字列はコミット前に止める（2026-10-07 監査）。"""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".githooks"))
        import check_staged_secrets

        self.module = check_staged_secrets

    def test_detects_key_like_strings_without_returning_values(self):
        samples = [
            "AIza" + "B" * 35,
            "sk-ant-" + "api03-" + "x" * 30,
            "github_pat_" + "1" * 30,
            "ghp_" + "a" * 36,
            "re_" + "a" * 8 + "_" + "b" * 24,
            "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
        ]
        for sample in samples:
            with self.subTest(sample=sample[:8]):
                found = self.module.find_secrets(f"value = '{sample}'")
                self.assertTrue(found)
                self.assertNotIn(sample, " ".join(found))

    def test_plain_setting_names_are_not_flagged(self):
        self.assertEqual(self.module.find_secrets("RESEND_API_KEY=re_xxxxxxxxx"), [])
        self.assertEqual(self.module.find_secrets("ssh-key: ${{ secrets.RATE_FEED_READ_KEY }}"), [])


if __name__ == "__main__":
    unittest.main()
