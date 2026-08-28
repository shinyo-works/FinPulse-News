"""住宅ローンの条件比較データ（docs/data/loan-features.json）とその表示の検証。

この表は週次の自動収集ではなく人手で書いた資料なので、金利履歴と食い違ったまま
公開されないよう、商品の同一性と表示側の配線をテストで固定する。
"""

import json
import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"

# 機関名セルの既定幅は142ページピクセル（狭い画面は112）。利用者がドラッグで広げられる
# ようになったが、既定のまま見る人のために、長い注記で行を伸ばさない上限は残す。
MAX_GUARANTEE_NOTE_CHARS = 20
MAX_FEE_NOTE_CHARS = 30

# 保証料の扱い。included=金利に含む／separate=必ず別途／conditional=条件次第・記載がなく不明
GUARANTEE_STATUSES = ("included", "separate", "conditional")


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

    def test_guarantee_status_matches_the_wording_of_each_product(self):
        """保証料の色分けは3値。included=灰・separate=赤・conditional=橙。"""
        for product in self.features["products"]:
            status = product["guarantee_status"]
            self.assertIn(status, GUARANTEE_STATUSES, product["product_id"])
            # 旧2値のキーは残さない（どちらを見るべきか迷わせないため）
            self.assertNotIn("guarantee_warn", product)
            guarantee = product["values"]["guarantee"]
            if status == "separate":
                # 赤で立てるのは「必ず別途かかる」商品だけ
                self.assertNotIn("金利に含む", guarantee)
            elif status == "included":
                self.assertNotIn("別途", guarantee[:6])
                # 「会員だけ無料」「審査によっては前払い」のような条件付きを灰色に戻さない
                for word in ("会員は", "場合あり", "選択制"):
                    self.assertNotIn(
                        word, guarantee, f"{product['product_id']} は conditional では"
                    )
            else:
                # 橙にするのは、条件次第・審査次第・記載がなく不明のいずれかが読み取れるもの
                self.assertTrue(
                    any(
                        word in guarantee
                        for word in ("会員", "場合あり", "選択制", "記載がな")
                    ),
                    f"{product['product_id']} の guarantee に条件付きの根拠がない",
                )

    def test_values_do_not_rank_institutions_against_each_other(self):
        """比較の断定は書かない。機関を足すたびに全セルの再検証が要るのを避ける。"""
        # 「最高3,000万円」「合計3点以上」のような正当な語とは衝突しない範囲で広く禁じる
        banned = ("最も", "唯一", "他行より", "10機関", "最安値")
        for product in self.features["products"]:
            for key, value in product["values"].items():
                for word in banned:
                    self.assertNotIn(
                        word, value, f"{product['product_id']} の {key}: {value}"
                    )

    def test_urls_are_http_or_https(self):
        for product in self.features["products"]:
            self.assertRegex(product["url"], r"^https?://")

    def test_verified_date_is_recorded(self):
        self.assertRegex(self.features["verified_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(2, self.features["schema_version"])


class LoanFeaturesViewerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index_html = (DOCS / "index.html").read_text(encoding="utf-8")
        cls.rate_html = (DOCS / "rate-history.html").read_text(encoding="utf-8")
        cls.features_html = (DOCS / "loan-features.html").read_text(encoding="utf-8")

    def test_comparison_tab_sits_between_the_news_and_rate_tabs(self):
        """条件比較は新着ニュースのすぐ右。金利2タブはその後ろに並べる。"""
        tabs = re.search(
            r'<nav class="product-tabs".*?</nav>', self.index_html, re.DOTALL
        ).group(0)
        self.assertIn('data-view="features"', tabs)
        self.assertLess(tabs.index('data-view="news"'), tabs.index('data-view="features"'))
        self.assertLess(tabs.index('data-view="features"'), tabs.index('data-view="rate"'))
        self.assertLess(tabs.index('data-view="rate"'), tabs.index('data-view="car"'))
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
        self.assertIn(".feature-note.caution {", self.rate_html)
        # 色分けは3値の guarantee_status を見る（旧2値のキーは参照しない）
        self.assertIn("feature.guarantee_status", self.rate_html)
        self.assertNotIn("guarantee_warn", self.rate_html)
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
        # 保証料の色分けは3値。赤と橙の両方を凡例で説明する。
        self.assertIn('product.guarantee_status === "separate"', self.features_html)
        # 値が欠けていても既定色（警告なし）に落とさない
        self.assertIn('product.guarantee_status !== "included"', self.features_html)
        self.assertNotIn("guarantee_warn", self.features_html)
        self.assertIn("td.caution {", self.features_html)
        self.assertIn("橙の欄", self.features_html)

    def test_comparison_viewer_points_back_to_the_rate_tab(self):
        self.assertIn("住宅ローン金利情報", self.features_html)
        self.assertIn("./data/loan-features.json", self.features_html)
        # 週次更新ではないことを画面上で断る
        self.assertIn("確認日", self.features_html)


if __name__ == "__main__":
    unittest.main()
