"""StructuredQuery -> TrendQuery (query/to_trend.py, decision FW-1): only the first item and the first season
go to trend; everything else is reported (IT-4, SQ-2, X4). Uses the contract examples (contracts/
structured-query.md §6), so no evaluation query is involved (W-10). No model call."""

import copy
import importlib
import unittest

from _query_support import HAS_PYDANTIC, SKIP_REASON


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class ToTrendQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = importlib.import_module("vogue_orchestrator.query.models")
        cls.convert = importlib.import_module("vogue_orchestrator.query.to_trend")
        cls.trend = importlib.import_module("vogue_orchestrator.query.trend_query")
        cls.examples = importlib.import_module("test_query_structured_models")

    def run_example(self, document):
        sq = self.models.StructuredQuery.model_validate(document)
        result = self.convert.to_trend_query(sq)
        if result.trend_query is not None:  # always a valid TrendQuery (trend's contract snapshot model)
            self.trend.TrendQuery.model_validate(result.to_dict())
        return result

    def test_only_the_first_item_is_forwarded(self):
        # example 5: i1 = 숏패딩 (크롭), i2 = 반팔 니트 드레스; 성별 제외는 두 품목 모두
        result = self.run_example(self.examples.EXAMPLE_5)
        tq = result.to_dict()
        self.assertEqual([c["minor"] for c in tq["requested"]["categories"]["include"]], ["패딩"])
        self.assertEqual(tq["requested"]["attributes"], [])  # 니트(i2) is not carried over to the padding
        self.assertIsNone(tq["requested"]["gender"])  # an excluded gender has no TrendQuery field
        self.assertEqual(sorted((u["type"], u["raw"]) for u in tq["unsupported"]),
                         [("body_length", "숏패딩"), ("gender", "남성")])
        self.assertEqual(result.warnings, ("items_not_forwarded", "conditions_not_forwarded"))

    def test_only_the_first_season_is_forwarded(self):
        document = copy.deepcopy(self.examples.EXAMPLE_3)
        document["trend_terms"]["seasons"] = [
            {"year": 2027, "season": "SS", "raw": "내년 봄", "origin": "user_requested"},
            {"year": 2027, "season": "FW", "raw": "가을", "origin": "user_requested"}]
        result = self.run_example(document)
        self.assertEqual(result.to_dict()["trend_terms"]["season"],
                         {"year": 2027, "season": "SS", "raw": "내년 봄", "origin": "user_requested"})
        self.assertIn("seasons_not_forwarded", result.warnings)

    def test_one_item_one_season_is_forwarded_whole(self):
        # example 3: 후드티, 옐로우·오버사이즈 포함, 블랙 제외, 꾸안꾸(abstract)
        result = self.run_example(self.examples.EXAMPLE_3)
        tq = result.to_dict()
        self.assertEqual([(a["type"], a["value"]) for a in tq["requested"]["attributes"]],
                         [("color", "옐로우"), ("fit", "오버사이즈")])
        self.assertEqual([(u["type"], u["raw"]) for u in tq["unsupported"]], [("color", "블랙"), ("abstract", "꾸안꾸 느낌")])
        self.assertTrue(tq["unsupported"][0]["reason"].endswith("(제외 요청)"))
        self.assertEqual(result.warnings, ("conditions_not_forwarded",))  # the excluded color has no field

    def test_no_category_means_all_and_exclusions_are_kept(self):
        # example 7 (IT-6): category null -> include [] (= all four majors) minus the excluded 아우터
        tq = self.run_example(self.examples.EXAMPLE_7).to_dict()
        self.assertEqual(tq["requested"]["categories"]["include"], [])
        self.assertEqual([c["major"] for c in tq["requested"]["categories"]["exclude"]], ["아우터"])

    def test_an_excluded_unsupported_condition_says_so(self):
        tq = self.run_example(self.examples.EXAMPLE_4).to_dict()
        brand = next(u for u in tq["unsupported"] if u["type"] == "brand")
        self.assertTrue(brand["reason"].endswith("(제외 요청)"))

    def test_out_of_scope_items_are_reported_to_trend_as_unsupported_categories(self):
        # IT-7′ (2026-11-12): searched over every category, the out-of-scope item is reported as not applied
        document = copy.deepcopy(self.examples.EXAMPLE_6)
        document["out_of_scope_items"] = [{"raw": "부츠", "reason": "표준 카테고리(상분류 4·하분류 21)에 없음",
                                           "conditions_raw": ["블랙"]}]
        result = self.run_example(document)
        tq = result.to_dict()
        boots = [u for u in tq["unsupported"] if u["raw"] == "부츠"]
        self.assertEqual(boots, [{"type": "category", "raw": "부츠", "reason": "표준 카테고리(상분류 4·하분류 21)에 없음"}])
        self.assertNotIn("블랙", [u["raw"] for u in tq["unsupported"]])  # its own conditions are not sent
        self.assertIn("conditions_not_forwarded", result.warnings)

    def test_no_item_means_nothing_to_forward(self):
        document = copy.deepcopy(self.examples.EXAMPLE_6)
        document["items"] = []
        result = self.run_example(document)
        self.assertIsNone(result.trend_query)
        self.assertEqual(result.warnings, ("no_item_to_forward",))


if __name__ == "__main__":
    unittest.main()
