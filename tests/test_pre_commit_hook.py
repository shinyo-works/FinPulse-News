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
            text=True,
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


if __name__ == "__main__":
    unittest.main()
