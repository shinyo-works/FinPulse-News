"""住宅ローンの条件比較データ（docs/data/loan-features.json）とその表示の検証。

この表は週次の自動収集ではなく人手で書いた資料なので、金利履歴と食い違ったまま
公開されないよう、商品の同一性と表示側の配線をテストで固定する。
"""

import json
import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"

# 機関名セルは幅142ページピクセル（狭い画面は112）しかないため、
# 長い注記を入れると行が伸びて金利の比較が読みにくくなる。
MAX_GUARANTEE_NOTE_CHARS = 20
MAX_FEE_NOTE_CHARS = 30


class LoanFeaturesDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.features = json.loads(
            (DOCS / "data" / "loan-features.json").read_text(encoding="utf-8")
        )
        cls.history = json.loads(
            (DOCS / "data" / "rate-history.json").read_text(encoding="utf-8")
        )

    def test_every_product_has_a_value_for_every_column(self):
        keys = [column["key"] for column in self.features["columns"]]
        self.assertTrue(keys)
        for product in self.features["products"]:
            values = product["values"]
            missing = [key for key in keys if not str(values.get(key, "")).strip()]
            self.assertEqual(
                [], missing, f"{product['product_id']} の未記入項目: {missing}"
            )
            extra = [key for key in values if key not in keys]
            self.assertEqual([], extra, f"{product['product_id']} の余分な項目: {extra}")

    def test_columns_belong_to_a_declared_group(self):
        group_keys = {group["key"] for group in self.features["groups"]}
        for column in self.features["columns"]:
            self.assertIn(column["group"], group_keys)

    def test_products_match_the_rate_history_exactly(self):
        """金利表に無い商品を載せない。金利表にある商品を落とさない。"""
        feature_ids = [product["product_id"] for product in self.features["products"]]
        history_ids = []
        for row in self.history["rows"]:
            if row["product_id"] not in history_ids:
                history_ids.append(row["product_id"])
        self.assertEqual(sorted(history_ids), sorted(feature_ids))
        self.assertEqual(len(feature_ids), len(set(feature_ids)))

    def test_bank_order_follows_the_rate_history(self):
        """表示順は config.json の機関順が正。ビューアーで並べ替えないので配列順を合わせる。"""
        def bank_order(ids):
            order = []
            for bank_id in ids:
                if bank_id not in order:
                    order.append(bank_id)
            return order

        self.assertEqual(
            bank_order([row["bank_id"] for row in self.history["rows"]]),
            bank_order([p["bank_id"] for p in self.features["products"]]),
        )

    def test_bank_and_product_names_match_the_rate_history(self):
        names = {
            row["product_id"]: (row["bank_name"], row["product_name"])
            for row in self.history["rows"]
        }
        for product in self.features["products"]:
            self.assertEqual(
                names[product["product_id"]],
                (product["bank_name"], product["product_name"]),
            )

    def test_notes_are_short_enough_for_the_bank_cell(self):
        for product in self.features["products"]:
            for key, limit in (
                ("guarantee_note", MAX_GUARANTEE_NOTE_CHARS),
                ("fee_note", MAX_FEE_NOTE_CHARS),
            ):
                note = product[key]
                self.assertIsInstance(note, str)
                self.assertTrue(note.strip(), f"{product['product_id']} の {key} が空")
                self.assertLessEqual(
                    len(note), limit, f"{product['product_id']} の {key} が長すぎる: {note}"
                )

    def test_guarantee_warn_marks_only_products_paying_the_fee_separately(self):
        for product in self.features["products"]:
            warn = product["guarantee_warn"]
            self.assertIsInstance(warn, bool)
            guarantee = product["values"]["guarantee"]
            if warn:
                # 赤で立てるのは「金利に含まれない」商品だけ
                self.assertNotIn("金利に含む", guarantee)
            else:
                self.assertNotIn("別途", guarantee[:6])

    def test_urls_are_http_or_https(self):
        for product in self.features["products"]:
            self.assertRegex(product["url"], r"^https?://")

    def test_verified_date_is_recorded(self):
        self.assertRegex(self.features["verified_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(1, self.features["schema_version"])


class LoanFeaturesViewerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index_html = (DOCS / "index.html").read_text(encoding="utf-8")
        cls.rate_html = (DOCS / "rate-history.html").read_text(encoding="utf-8")
        cls.features_html = (DOCS / "loan-features.html").read_text(encoding="utf-8")

    def test_comparison_tab_is_added_after_the_rate_tabs(self):
        tabs = re.search(
            r'<nav class="product-tabs".*?</nav>', self.index_html, re.DOTALL
        ).group(0)
        self.assertIn('data-view="features"', tabs)
        self.assertLess(tabs.index('data-view="car"'), tabs.index('data-view="features"'))
        self.assertIn('src: "./loan-features.html"', self.index_html)
        self.assertIn('<iframe id="featuresFrame" class="report-frame"', self.index_html)

    def test_tab_row_scrolls_instead_of_clipping_the_last_tab(self):
        tabs_css = re.search(
            r"\.product-tabs \{.*?\n    \}", self.index_html, re.DOTALL
        ).group(0)
        self.assertIn("overflow-x: auto;", tabs_css)
        self.assertNotIn("overflow: hidden;", tabs_css)

    def test_rate_table_shows_the_two_notes_under_the_bank_name(self):
        self.assertIn('featuresUrl: "./data/loan-features.json"', self.rate_html)
        self.assertIn("function featureNotesHtml(row)", self.rate_html)
        self.assertIn("${featureNotesHtml(row)}", self.rate_html)
        self.assertIn(".feature-note {", self.rate_html)
        self.assertIn(".feature-note.warn {", self.rate_html)
        # 注記は必ずエスケープしてから差し込む
        self.assertIn("escapeHtml(feature.guarantee_note)", self.rate_html)
        self.assertIn("escapeHtml(feature.fee_note)", self.rate_html)

    def test_feature_notes_are_housing_only(self):
        datasets = re.search(
            r"const DATASETS = \{.*?\n    \};", self.rate_html, re.DOTALL
        ).group(0)
        housing = datasets[datasets.index("housing: {"):datasets.index("car: {")]
        car = datasets[datasets.index("car: {"):]
        self.assertIn("featuresUrl:", housing)
        self.assertNotIn("featuresUrl:", car)

    def test_missing_feature_file_does_not_break_the_rate_table(self):
        loader = re.search(
            r"async function loadFeatures\(\) \{.*?\n    \}", self.rate_html, re.DOTALL
        ).group(0)
        self.assertIn("catch", loader)
        self.assertIn("state.features.clear();", loader)

    def test_comparison_viewer_escapes_values_and_keeps_the_json_order(self):
        self.assertIn("escapeHtml(text)", self.features_html)
        self.assertIn("safeUrl(product.url)", self.features_html)
        # 表示順はJSONの並び順が正。ビューアーでは並べ替えない。
        self.assertNotIn(".sort(", self.features_html)

    def test_comparison_viewer_points_back_to_the_rate_tab(self):
        self.assertIn("住宅ローン金利情報", self.features_html)
        self.assertIn("./data/loan-features.json", self.features_html)
        # 週次更新ではないことを画面上で断る
        self.assertIn("確認日", self.features_html)


if __name__ == "__main__":
    unittest.main()
