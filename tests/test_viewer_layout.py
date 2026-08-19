import re
import unittest
from pathlib import Path


class ViewerLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "docs"
        cls.html = (root / "index.html").read_text(encoding="utf-8")
        cls.rate_html = (root / "rate-history.html").read_text(encoding="utf-8")
        cls.root_html = (root.parent / "index.html").read_text(encoding="utf-8")

    def test_site_title_lives_only_in_the_tab_row(self):
        # サイト名はタブ行の1か所だけ。検索窓の上とサイドバーには置かない。
        topbar = re.search(
            r'<header class="topbar">(.*?)</header>', self.html, re.DOTALL
        ).group(1)
        sidebar = re.search(
            r'<aside class="sidebar".*?</aside>', self.html, re.DOTALL
        ).group(0)

        self.assertNotIn("十勝金融機関News", topbar)
        self.assertNotIn("十勝金融機関News", sidebar)
        self.assertNotIn("brand-mark", self.html)
        self.assertNotIn("帯広エリア 金融機関レポート", self.html)
        self.assertEqual(1, self.html.count("十勝金融機関News</b>"))

    def test_rate_heading_moves_to_the_right_of_the_tabs(self):
        tabs = re.search(
            r'<nav class="product-tabs".*?</nav>', self.html, re.DOTALL
        ).group(0)

        self.assertLess(tabs.index('data-view="rate"'), tabs.index("金利履歴ビューアー"))
        self.assertIn("左が最新、右が過去です。", tabs)
        # 新着ニュース表示中はサイト名だけ、金利情報表示中は説明だけを出す
        self.assertRegex(
            self.html,
            r'body\[data-view="news"\]\s*\.tab-note,\s*\n\s*body\[data-view="rate"\]\s*\.tab-note\s*\{[^}]*display:\s*flex;',
        )
        self.assertRegex(
            self.html,
            r'body\[data-view="news"\]\s*\.note-heading,\s*\n\s*body\[data-view="news"\]\s*\.note-text\s*\{\s*display:\s*none;\s*\}',
        )
        self.assertRegex(
            self.html,
            r'body\[data-view="rate"\]\s*\.tab-brand,\s*\n\s*body\[data-view="rate"\]\s*\.note-heading\s*\{\s*display:\s*none;\s*\}',
        )
        # iframe 側の見出しは重複するので、埋め込み時だけ隠す
        self.assertRegex(
            self.rate_html,
            r"body\.embedded\s*\.site-header\s*\{[^}]*display:\s*none;",
        )
        self.assertIn('window.self !== window.top', self.rate_html)

    def test_site_name_leads_the_rate_tab_note(self):
        tabs = re.search(
            r'<nav class="product-tabs".*?</nav>', self.html, re.DOTALL
        ).group(0)

        # サイト名 → 金利履歴ビューアー → 説明文 の順
        self.assertLess(tabs.index("十勝金融機関News"), tabs.index("金利履歴ビューアー"))
        self.assertLess(tabs.index("金利履歴ビューアー"), tabs.index("左が最新"))
        # サイト名だけは縮めない（.tab-note b の flex を詳細度で上書きする）
        self.assertRegex(
            self.html,
            r"\.tab-note\s+\.tab-brand\s*\{[^}]*flex:\s*none;",
        )
        # 狭い画面ではサイト名と短い説明だけ残す
        self.assertRegex(
            self.html,
            r"\.tab-note\s+\.note-full,\s*\n\s*\.tab-note\s+\.note-heading\s*\{\s*display:\s*none;\s*\}",
        )

    def test_rate_tab_is_the_default_view(self):
        self.assertIn('<body data-mobile-pane="nav" data-view="rate">', self.html)
        tabs = re.search(
            r'<nav class="product-tabs".*?</nav>', self.html, re.DOTALL
        ).group(0)
        self.assertIn(
            '<button class="product-tab active" type="button" data-view="rate">金利情報</button>',
            tabs,
        )
        # URLがニュース側を指すときだけ新着ニュースへ切り替える
        self.assertIn(
            'switchView(parseHash(INITIAL_HASH)?.view === "news" ? "news" : "rate");',
            self.html,
        )

    def test_titles_use_the_japanese_site_name(self):
        # ブラウザのタブ名・ブックマーク名は画面上の見出しと同じ名前に揃える
        self.assertIn("<title>十勝金融機関News｜金利情報</title>", self.html)
        self.assertIn('const NEWS_TITLE = "十勝金融機関News｜新着ニュース";', self.html)
        self.assertIn('const RATE_TITLE = "十勝金融機関News｜金利情報";', self.html)
        self.assertIn("<title>十勝金融機関News｜金利情報</title>", self.rate_html)
        self.assertIn("<title>十勝金融機関News</title>", self.root_html)
        for html in (self.html, self.rate_html, self.root_html):
            self.assertNotIn("<title>FinPulse", html)

    def test_favicon_shows_a_bank_building(self):
        # 3ファイルとも同じ銀行アイコン（切妻＋3本柱＋台座）を使う
        for html in (self.html, self.rate_html, self.root_html):
            self.assertIn("M16 5 L28 12 H4 Z", html)
            self.assertEqual(3, html.count("width='3.4' height='9'"))

    def test_date_navigation_folds_old_reports(self):
        # 直近2か月はそのまま、それ以前は月ごと、前年度以前は会計年度ごとに畳む
        self.assertIn("const FLAT_MONTHS = 2;", self.html)
        self.assertRegex(
            self.html,
            r"function fiscalYear\(year, month\) \{\s*\n\s*return month < 4 \? year - 1 : year;",
        )
        self.assertIn("function groupReportDates(dates)", self.html)
        self.assertIn('`${fy}年度`', self.html)
        # 開閉は details なので JavaScript を足さずに動く
        self.assertIn('<details class="nav-group"', self.html)
        # 選択中の日付を含むグループは開いた状態で描く
        self.assertIn("dates.includes(selectedDate)", self.html)
        self.assertIn("allDates.includes(selectedDate)", self.html)
        # 畳んだ中の日付も同じクリック経路（data-date）を通る
        self.assertIn('data-date="${escapeHtml(date)}"', self.html)

    def test_panes_can_be_resized_and_the_width_persists(self):
        self.assertIn('id="paneResizer"', self.html)
        self.assertRegex(
            self.html,
            r"\.app-shell\s*\{[^}]*grid-template-columns:\s*var\(--sidebar-width\)",
        )
        self.assertRegex(self.html, r"\.pane-resizer\s*\{[^}]*cursor:\s*col-resize;")
        self.assertIn("localStorage.setItem(SIDEBAR_WIDTH_KEY", self.html)
        # モバイルは1画面1ペインなので調整バーを出さない
        self.assertRegex(self.html, r"\.pane-resizer\s*\{\s*display:\s*none;\s*\}")


if __name__ == "__main__":
    unittest.main()
