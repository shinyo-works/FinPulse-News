import re
import unittest
from pathlib import Path


class RateHistoryLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (
            Path(__file__).resolve().parents[1] / "docs" / "rate-history.html"
        ).read_text(encoding="utf-8")

    def test_subtitle_is_on_the_right_at_original_font_size(self):
        header = re.search(
            r'<header class="site-header">(.*?)</header>', self.html, re.DOTALL
        ).group(1)

        self.assertLess(header.index("金利履歴ビューアー"), header.index('id="subtitle"'))
        self.assertRegex(
            self.html,
            r"\.site-header\s*\{[^}]*display:\s*flex;",
        )
        self.assertRegex(
            self.html,
            r"\.site-header p\s*\{[^}]*font-size:\s*0\.8rem;",
        )

    def test_search_control_and_table_rows_are_compact(self):
        self.assertRegex(
            self.html,
            r"#searchInput\s*\{[^}]*height:\s*30px;",
        )
        self.assertRegex(
            self.html,
            r"tbody td\s*\{[^}]*padding:\s*6px 8px;",
        )

    def test_three_notes_scroll_inside_the_table_frame(self):
        frame_start = self.html.index('<div class="table-scroll" id="tableScroll">')
        notes_start = self.html.index('<div class="scrolling-notes">', frame_start)
        matrix_start = self.html.index('<div id="matrix"', notes_start)

        self.assertLess(frame_start, notes_start)
        self.assertLess(notes_start, matrix_start)
        notes = self.html[notes_start:matrix_start]
        self.assertEqual(3, notes.count('class="info-line"'))
        self.assertIn("各セル", notes)
        self.assertIn("掲載範囲", notes)
        self.assertIn('id="sourceNote"', notes)
        self.assertIn("overflow: auto;", self.html)
        self.assertNotIn(
            'el.matrix.innerHTML = `<div class="table-scroll">', self.html
        )

    def test_table_header_remains_sticky_after_notes_scroll_away(self):
        self.assertRegex(
            self.html,
            r"thead th\s*\{[^}]*position:\s*sticky;\s*top:\s*0;",
        )

    def test_past_minimum_uses_font_only_without_label(self):
        self.assertIn('isCurrent ? "best" : "past-best"', self.html)
        self.assertIn("const badge = isCurrent && isBest", self.html)
        self.assertIn(': `<span class="rate-val">', self.html)
        self.assertRegex(
            self.html,
            r"\.rate-cell\.past-best \.rate-val\s*\{[^}]*font-weight:\s*900;"
            r"[^}]*text-decoration:\s*underline;",
        )
        self.assertIn("function minRateByDateAndType(rows, dates)", self.html)


if __name__ == "__main__":
    unittest.main()
