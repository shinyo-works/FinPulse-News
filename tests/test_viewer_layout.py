import re
import unittest
from pathlib import Path


class ViewerLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "docs"
        cls.html = (root / "index.html").read_text(encoding="utf-8")
        cls.rate_html = (root / "rate-history.html").read_text(encoding="utf-8")

    def test_site_title_sits_just_above_the_search_box(self):
        topbar = re.search(
            r'<header class="topbar">(.*?)</header>', self.html, re.DOTALL
        ).group(1)

        self.assertIn("十勝金融機関News", topbar)
        self.assertLess(topbar.index("十勝金融機関News"), topbar.index('id="searchInput"'))
        # 旧ブランド表記はサイドバーごと廃止した
        self.assertNotIn("帯広エリア 金融機関レポート", self.html)
        sidebar = re.search(
            r'<aside class="sidebar".*?</aside>', self.html, re.DOTALL
        ).group(0)
        self.assertNotIn("brand-mark", sidebar)

    def test_rate_heading_moves_to_the_right_of_the_tabs(self):
        tabs = re.search(
            r'<nav class="product-tabs".*?</nav>', self.html, re.DOTALL
        ).group(0)

        self.assertLess(tabs.index('data-view="rate"'), tabs.index("金利履歴ビューアー"))
        self.assertIn("左が最新、右が過去です。", tabs)
        self.assertRegex(
            self.html,
            r'body\[data-view="rate"\]\s*\.tab-note\s*\{[^}]*display:\s*flex;',
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
