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

    def test_notes_scroll_inside_the_table_frame(self):
        frame_start = self.html.index('<div class="table-scroll" id="tableScroll">')
        notes_start = self.html.index('<div class="scrolling-notes">', frame_start)
        matrix_start = self.html.index('<div id="matrix"', notes_start)

        self.assertLess(frame_start, notes_start)
        self.assertLess(notes_start, matrix_start)
        notes = self.html[notes_start:matrix_start]
        # 凡例は表と一緒にスクロールさせる（見出しだけ残って表が動く形にしない）。
        # 保証料の凡例はマイカーローンのときだけ表示されるため既定は hidden。
        self.assertEqual(4, notes.count('class="info-line"'))
        self.assertIn("各セル", notes)
        self.assertIn("掲載範囲", notes)
        self.assertIn('id="guaranteeNote"', notes)
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



class RateHistoryColumnResizeTest(unittest.TestCase):
    """「金融機関・商品」列と「保証料」列は見出しのつまみで幅を変えられる。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (
            Path(__file__).resolve().parents[1] / "docs" / "rate-history.html"
        ).read_text(encoding="utf-8")

    def test_guarantee_header_has_its_own_resizer(self):
        self.assertIn('金融機関・商品${resizerHtml("bank")}</th>', self.html)
        self.assertIn('<th class="col-guarantee">保証料${resizerHtml("guarantee")}</th>', self.html)
        self.assertIn('data-col="${key}"', self.html)

    def test_guarantee_width_follows_a_css_variable_including_narrow_screens(self):
        self.assertRegex(self.html, r":root\s*\{[^}]*--guarantee-col-w:\s*140px;")
        self.assertRegex(
            self.html,
            r"\.col-guarantee\s*\{\s*width:\s*var\(--guarantee-col-w\);\s*"
            r"min-width:\s*var\(--guarantee-col-w\);\s*max-width:\s*var\(--guarantee-col-w\);",
        )
        self.assertRegex(self.html, r":root\s*\{\s*--guarantee-col-w:\s*112px;\s*\}")
        # 固定幅のまま残っていると、つまみを動かしても列が変わらない
        self.assertNotRegex(self.html, r"td\.col-guarantee\s*\{[^}]*width:\s*\d+px")

    def test_each_column_saves_its_width_separately_and_keeps_the_old_key(self):
        self.assertIn("finpulse.rateHistory.bankColWidth.${DATASET_KEY}", self.html)
        self.assertIn("finpulse.rateHistory.guaranteeColWidth.${DATASET_KEY}", self.html)
        # 未知の data-col（例: constructor）を設定として扱わない
        self.assertIn('Object.hasOwn(RESIZABLE_COLS, key ?? "")', self.html)


    def test_resizer_stays_inside_its_header_so_the_next_header_does_not_cover_it(self):
        self.assertRegex(self.html, r"\.col-resizer\s*\{[^}]*right:\s*0;")
        self.assertNotRegex(self.html, r"\.col-resizer\s*\{[^}]*right:\s*-")


if __name__ == "__main__":
    unittest.main()
