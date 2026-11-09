"""Draft -> TrendQuery assembly rules. These run without any LLM."""

import importlib
import unittest
from datetime import date

from _query_support import HAS_PYDANTIC, SKIP_REASON

TODAY = date(2026, 11, 9)


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class AssembleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assemble_module = importlib.import_module("vogue_orchestrator.query.assemble")
        cls.draft_module = importlib.import_module("vogue_orchestrator.query.draft")

    def assemble(self, draft=None, query="합성 질의", today=TODAY):
        draft = self.draft_module.QueryDraft.model_validate(draft or {})
        return self.assemble_module.assemble_trend_query(
            draft, request_id="rid-1", original_query=query, today=today,
        )

    def test_request_id_and_query_are_copied_and_missing_fields_stay_empty(self):
        result = self.assemble({"gender": {"raw": "합성", "value": "여"}}, query="합성 질의")
        self.assertEqual(result.trend_query.request_id, "rid-1")
        self.assertEqual(result.trend_query.original_query, "합성 질의")
        self.assertEqual(result.trend_query.missing_fields, [])  # assumption A-2

    def test_season_defaults_to_the_next_fashion_season_by_code(self):
        for today, expected in [(date(2026, 5, 1), (2026, "FW")), (date(2026, 11, 9), (2027, "SS")),
                                (date(2027, 1, 15), (2027, "SS"))]:
            with self.subTest(today=today):
                season = self.assemble({}, today=today).trend_query.trend_terms.season
                self.assertEqual((season.year, season.season, season.origin, season.raw),
                                 (*expected, "default_next", None))

    def test_named_season_with_year_is_kept_as_user_requested(self):
        draft = {"season": {"year": 2028, "season": "FW", "raw": "2028 가을"}}
        result = self.assemble(draft, query="2028 가을")
        season = result.trend_query.trend_terms.season
        self.assertEqual((season.year, season.season, season.origin, season.raw),
                         (2028, "FW", "user_requested", "2028 가을"))
        self.assertNotIn("season_year_inferred", result.warnings)

    def test_named_season_without_year_is_resolved_by_code_and_reported(self):
        result = self.assemble({"season": {"year": None, "season": "SS", "raw": "봄"}}, query="봄")
        season = result.trend_query.trend_terms.season
        self.assertEqual((season.year, season.origin), (2027, "user_requested"))
        self.assertIn("season_year_inferred", result.warnings)

    def test_minor_category_overrides_a_conflicting_major_and_warns(self):
        draft = {"include_categories": [{"raw": "코트", "major": "상의", "minor": "코트"}]}
        result = self.assemble(draft, query="코트")
        ref = result.trend_query.requested.categories.include[0]
        self.assertEqual((ref.major, ref.minor), ("아우터", "코트"))
        self.assertIn("category_major_overridden", result.warnings)

    def test_major_only_category_keeps_minor_null_and_tops_stay_separate_from_outerwear(self):
        result = self.assemble({"include_categories": [{"raw": "상의", "major": "상의"}]}, query="상의")
        refs = result.trend_query.requested.categories.include
        self.assertEqual([(r.major, r.minor) for r in refs], [("상의", None)])

    def test_non_standard_categories_go_to_unsupported_and_are_never_widened(self):
        draft = {"include_categories": [
            {"raw": "가방", "major": None, "minor": None},
            {"raw": "블레이저", "major": "아우터", "minor": "블레이저"},  # outside the 21 minors
            {"raw": "신발", "major": "신발", "minor": None},               # outside the 4 majors
        ]}
        result = self.assemble(draft, query="가방 블레이저 신발")
        query = result.trend_query
        self.assertEqual(query.requested.categories.include, [])
        self.assertEqual([(u.type, u.raw) for u in query.unsupported],
                         [("category", "가방"), ("category", "블레이저"), ("category", "신발")])
        self.assertIn("category_not_standard", result.warnings)

    def test_attribute_value_outside_the_vocabulary_becomes_null_not_a_filter(self):
        draft = {"attributes": [{"type": "color", "raw": "핑크색", "value": "핑크색"}]}
        result = self.assemble(draft, query="핑크색")
        attribute = result.trend_query.requested.attributes[0]
        self.assertEqual((attribute.raw, attribute.value), ("핑크색", None))
        self.assertIn("attribute_value_not_in_vocabulary", result.warnings)

    def test_unknown_attribute_type_is_recorded_in_unsupported(self):
        draft = {"attributes": [{"type": "length", "raw": "롱", "value": "롱"}]}
        result = self.assemble(draft, query="롱")
        self.assertEqual(result.trend_query.requested.attributes, [])
        self.assertEqual(result.trend_query.unsupported[0].type, "attribute")
        self.assertIn("attribute_type_unsupported", result.warnings)

    def test_gender_value_outside_the_vocabulary_becomes_null(self):
        result = self.assemble({"gender": {"raw": "여성", "value": "여성"}}, query="여성")
        gender = result.trend_query.requested.gender
        self.assertEqual((gender.raw, gender.value), ("여성", None))
        self.assertIn("gender_value_not_in_vocabulary", result.warnings)

    def test_unsupported_reasons_are_fixed_texts_and_duplicates_collapse(self):
        draft = {"unsupported": [
            {"type": "brand", "raw": "H&M"}, {"type": "brand", "raw": "H&M"},
            {"type": "price", "raw": "3만원 이하"},
        ]}
        result = self.assemble(draft, query="H&M 3만원 이하")
        unsupported = result.trend_query.unsupported
        self.assertEqual([u.type for u in unsupported], ["brand", "price"])
        self.assertEqual(unsupported[0].reason, "Product 데이터는 Musinsa뿐")
        self.assertEqual(unsupported[1].reason, "현재 검색에서 지원하지 않는 조건")

    def test_paraphrased_raw_expression_is_reported(self):
        draft = {"style_names": [{"raw": "발레 코어"}]}
        result = self.assemble(draft, query="발레코어")
        self.assertIn("raw_not_in_query", result.warnings)

    def test_empty_draft_is_valid_but_flagged(self):
        result = self.assemble({}, query="합성 질의")
        self.assertIn("no_conditions_extracted", result.warnings)
        self.assertEqual(result.trend_query.requested.categories.include, [])


if __name__ == "__main__":
    unittest.main()
