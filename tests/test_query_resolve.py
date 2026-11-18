"""Value resolver (query/resolve.py, decision Q3 = gamma, G-1, G-2). Stdlib only.

Expected values were checked against the real alias dictionary (alias_data.py snapshot) by running
the code; where this file says "dictionary", the result comes from `lexicon.py`, not from the LLM.
"""

import unittest

from vogue_orchestrator.query import resolve as r
from vogue_orchestrator.query.resolve import Condition, ResolvedValue, include_exclude_conflicts


def one(raw, kind, polarity="include", suggestion=None, **kwargs):
    return r.resolve_condition(Condition(raw, kind, polarity, suggestion), **kwargs)


def codes(result):
    return [w.code for w in result.warnings]


class SpecExample3Tests(unittest.TestCase):
    """contracts/agents/orchestrator/output/structured-query.md §4.1, example 3."""

    def test_example_3_table(self):
        result = r.resolve_conditions([
            Condition("후드티", "category", "include", "후드티"),
            Condition("블랙", "color", "exclude", "블랙"),
            Condition("버터옐로우", "color", "include", "옐로우"),
            Condition("오버핏", "fit", "include", "오버사이즈"),
        ])
        got = [(v.raw, v.kind, v.polarity, v.value, v.match) for v in result.values]
        self.assertEqual(got, [
            ("후드티", "category", "include", "후드티", "exact"),
            ("블랙", "color", "exclude", "블랙", "exact"),
            ("버터옐로우", "color", "include", "옐로우", "approx"),
            ("오버핏", "fit", "include", "오버사이즈", "llm"),
        ])
        self.assertEqual(result.values[0].major, "상의")
        self.assertEqual(result.unsupported, ())
        self.assertEqual(result.warnings, ())

    def test_overfit_without_suggestion_is_none(self):
        result = one("오버핏", "fit")
        self.assertEqual((result.values[0].value, result.values[0].match), (None, "none"))


class DictionaryFirstTests(unittest.TestCase):
    def test_alias(self):
        value = one("슬랙스", "category", "exclude", "팬츠").values[0]
        self.assertEqual((value.major, value.minor, value.match, value.polarity), ("하의", "팬츠", "alias", "exclude"))

    def test_unknown_color_without_usable_suggestion_is_none(self):
        result = one("연두", "color")
        self.assertEqual([(v.value, v.match) for v in result.values], [(None, "none")])
        self.assertEqual(result.warnings, ())

    def test_suggestion_outside_the_standard_list_is_dropped_with_warning(self):
        result = one("연두", "color", suggestion="연두")
        self.assertEqual([(v.value, v.match) for v in result.values], [(None, "none")])
        self.assertEqual(codes(result), [r.WARN_SUGGESTION_NOT_IN_VOCABULARY])

    def test_suggestion_of_another_kind_is_not_accepted(self):
        result = one("연두", "color", suggestion="니트")  # material value offered for a color
        self.assertEqual(result.values[0].match, "none")
        self.assertEqual(codes(result), [r.WARN_SUGGESTION_NOT_IN_VOCABULARY])

    def test_dictionary_overrides_a_different_suggestion(self):
        result = one("슬림", "fit", suggestion="스키니")  # dictionary: 슬림 -> 타이트 (approx)
        self.assertEqual([(v.value, v.match) for v in result.values], [("타이트", "approx")])
        self.assertEqual(result.warnings, (r.ResolutionWarning(r.WARN_LLM_SUGGESTION_OVERRIDDEN, "슬림"),))

    def test_same_suggestion_gives_no_warning(self):
        self.assertEqual(one("슬랙스", "category", suggestion="팬츠").warnings, ())

    def test_whole_query_words_are_never_scanned(self):
        # "겨울" contains "울" but only exact lookups of the span/its tokens are made
        result = one("겨울", "material")
        self.assertEqual([(v.value, v.match) for v in result.values], [(None, "none")])

    def test_hints_are_never_applied(self):
        # dictionary: material "다운" -> hint category_minor 패딩 only (H5)
        result = one("다운", "material")
        self.assertEqual([(v.kind, v.value, v.match) for v in result.values], [("material", None, "none")])

    def test_dictionary_null_value_blocks_the_suggestion(self):
        # dictionary: neckline/collar "카라" is a null value (no standard value on purpose)
        result = one("카라", "detail", suggestion="셔츠칼라")
        self.assertEqual([(v.value, v.match) for v in result.values], [(None, "none")])
        self.assertEqual(codes(result), [r.WARN_LLM_SUGGESTION_OVERRIDDEN])


class TokenFallbackTests(unittest.TestCase):
    def test_material_token(self):
        value = one("니트 소재", "material", suggestion="니트").values[0]
        self.assertEqual((value.raw, value.value, value.match, value.matched_text), ("니트 소재", "니트", "exact", "니트"))

    def test_whole_span_is_tried_first(self):
        value = one("울 블렌드", "material").values[0]  # whole-span alias, not the token "울"
        self.assertEqual((value.value, value.match, value.matched_text), ("울/캐시미어", "alias", "울 블렌드"))

    def test_several_values_in_one_span_give_one_output_each_with_the_same_raw(self):
        result = one("청바지 스커트", "category")
        self.assertEqual([(v.raw, v.minor, v.match) for v in result.values],
                         [("청바지 스커트", "청바지", "exact"), ("청바지 스커트", "스커트", "exact")])
        self.assertIn(r.WARN_MULTIPLE_VALUES_IN_SPAN, codes(result))

    def test_color_pair_in_one_span_follows_the_dictionary(self):
        # normalize_color does not split on spaces: "블랙 화이트" -> base color at the end (approx).
        # The prompt asks the LLM for one condition per value; this documents the dictionary behaviour.
        result = one("블랙 화이트", "color")
        self.assertEqual([(v.value, v.match) for v in result.values], [("화이트", "approx")])


class DetailAndRouteTests(unittest.TestCase):
    def test_detail_looks_up_neckline_and_collar(self):
        cases = {"브이넥": ("브이넥", "exact"), "셔츠칼라": ("셔츠칼라", "exact"), "폴로": ("폴로칼라", "alias"),
                 "터틀넥": ("터틀넥", "exact"), "퀼팅": ("퀼팅", "exact")}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                result = one(raw, "detail")
                self.assertEqual([(v.kind, v.value, v.match) for v in result.values], [("detail", *expected)])
                self.assertEqual(result.warnings, ())  # routes inside detail are not reported

    def test_route_to_another_contract_kind(self):
        result = one("데님", "pattern")  # dictionary: pattern 데님 -> material 데님
        self.assertEqual([(v.kind, v.value, v.match) for v in result.values], [("material", "데님", "exact")])
        self.assertEqual(codes(result), [r.WARN_ATTRIBUTE_ROUTED])

    def test_route_out_of_the_contract_is_unsupported_attribute(self):
        for raw, kind, target in (("플레어", "fit", "shape"), ("일자", "fit", "shape")):
            with self.subTest(raw=raw):
                result = one(raw, kind, "exclude", suggestion="와이드")
                self.assertEqual(result.values, ())
                self.assertEqual(result.unsupported, (r.UnsupportedItem(
                    "attribute", raw, "exclude", r.REASON_ROUTE_NOT_IN_CONTRACT, target),))
                self.assertEqual(codes(result), [r.WARN_ATTRIBUTE_ROUTED, r.WARN_LLM_SUGGESTION_OVERRIDDEN])

    def test_route_into_a_length_kind_is_inside_the_contract(self):
        # dictionary: detail 민소매 -> sleeve_length (decision OQ-9': lengths are contract kinds now)
        result = one("민소매", "detail", "exclude")
        self.assertEqual([(v.kind, v.polarity, v.value, v.match) for v in result.values],
                         [("sleeve_length", "exclude", "민소매", "exact")])
        self.assertEqual(result.unsupported, ())
        self.assertEqual(codes(result), [r.WARN_ATTRIBUTE_ROUTED])

    def test_route_without_value_falls_back_to_a_suggestion_of_the_target_kind(self):
        # dictionary: detail "프린트" -> pattern, no value
        result = one("프린트", "detail", suggestion="그래픽")
        self.assertEqual([(v.kind, v.value, v.match) for v in result.values], [("pattern", "그래픽", "llm")])
        result = one("프린트", "detail")
        self.assertEqual([(v.kind, v.value, v.match) for v in result.values], [("pattern", None, "none")])


class CategoryG2Tests(unittest.TestCase):
    """contracts/agents/orchestrator/output/structured-query.md §4.3 G-2."""

    def check(self, raw, suggestion, expected, polarity="include"):
        result = one(raw, "category", polarity, suggestion)
        self.assertEqual([(v.major, v.minor, v.match) for v in result.values], expected)
        return result

    def test_named_minor_is_kept_and_never_widened(self):
        result = self.check("스커트", "하의", [("하의", "스커트", "exact")])
        self.assertEqual(codes(result), [r.WARN_LLM_SUGGESTION_OVERRIDDEN])

    def test_minor_found_by_token(self):
        self.check("데님 스커트", None, [("하의", "스커트", "exact")])

    def test_subtypes_take_a_minor_suggestion(self):
        self.check("미니스커트", "스커트", [("하의", "스커트", "llm")])
        self.check("블레이저", "재킷", [("아우터", "재킷", "llm")])

    def test_major_names_are_exact(self):
        for major in ("상의", "하의", "아우터"):
            with self.subTest(major=major):
                result = self.check(major, major, [(major, None, "exact")])
                self.assertEqual(result.values[0].value, major)

    def test_onepiece_is_the_dress_alias(self):
        # decision OQ-5': the dictionary maps 원피스 -> 드레스 (alias) and the dictionary goes first
        result = self.check("원피스", "드레스", [("원피스", "드레스", "alias")])
        self.assertEqual(result.warnings, ())
        result = self.check("원피스", "원피스", [("원피스", "드레스", "alias")])
        self.assertEqual(codes(result), [r.WARN_LLM_SUGGESTION_OVERRIDDEN])

    def test_spaced_category_words_are_each_a_category(self):
        # decision OQ-10': "셔츠 원피스" = 셔츠 and 원피스(드레스); joined "셔츠원피스" is one dress (llm)
        self.check("셔츠 원피스", None, [("상의", "셔츠", "exact"), ("원피스", "드레스", "alias")], polarity="exclude")
        self.check("셔츠원피스", "드레스", [("원피스", "드레스", "llm")], polarity="exclude")
        self.check("니트 원피스", None, [("원피스", "드레스", "alias")])  # 니트 is not a category word

    def test_major_suggestion_for_a_subtype_is_rejected(self):
        result = self.check("롱스커트", "하의", [])
        self.assertEqual(result.unsupported, (r.UnsupportedItem("category", "롱스커트", "include", r.REASON_NOT_IN_DICTIONARY),))
        self.assertEqual(codes(result), [r.WARN_CATEGORY_MAJOR_SUGGESTION_REJECTED])

    def test_out_of_scope_items_are_unsupported_with_the_dictionary_reason(self):
        for raw in ("가방", "모자", "부츠"):
            with self.subTest(raw=raw):
                result = self.check(raw, "하의", [], polarity="exclude")
                self.assertEqual(result.unsupported, (r.UnsupportedItem("category", raw, "exclude", r.REASON_NULL_VALUE),))
                self.assertEqual(codes(result), [r.WARN_LLM_SUGGESTION_OVERRIDDEN])

    def test_unknown_items_are_unsupported_never_a_null_category(self):
        # dictionary has no entry for 신발 (unlike 가방), so the reason differs
        result = self.check("신발", None, [], polarity="exclude")
        self.assertEqual(result.unsupported, (r.UnsupportedItem("category", "신발", "exclude", r.REASON_NOT_IN_DICTIONARY),))
        result = self.check("신발", "신발", [])
        self.assertEqual(codes(result), [r.WARN_SUGGESTION_NOT_IN_VOCABULARY])

    def test_hint_only_words_use_the_minor_suggestion(self):
        # dictionary: category "무스탕" -> hint category_major 아우터 only; the hint is never applied
        self.check("무스탕", "코트", [("아우터", "코트", "llm")])
        self.check("무스탕", None, [])


class LengthTests(unittest.TestCase):
    """Decision OQ-9' (body_length, sleeve_length) and OQ-8' (숏패딩)."""

    def test_length_values_from_the_dictionary(self):
        cases = {("미니", "body_length"): ("미니", "exact"), ("긴", "body_length"): ("롱", "alias"),
                 ("숏패딩", "body_length"): ("크롭", "approx"), ("반팔", "sleeve_length"): ("반팔", "exact"),
                 ("반소매", "sleeve_length"): ("반팔", "alias")}
        for (raw, kind), expected in cases.items():
            with self.subTest(raw=raw):
                result = one(raw, kind)
                self.assertEqual([(v.kind, v.value, v.match) for v in result.values], [(kind, *expected)])

    def test_unknown_length_uses_the_suggestion(self):
        self.assertEqual([(v.value, v.match) for v in one("숏", "body_length", suggestion="크롭").values],
                         [("크롭", "llm")])
        self.assertEqual([(v.value, v.match) for v in one("짧은", "body_length", suggestion="반팔").values],
                         [(None, "none")])  # a sleeve value is not a body length

    def test_short_padding_is_padding_plus_crop(self):
        result = r.resolve_conditions([Condition("숏패딩", "category", "include", "패딩"),
                                       Condition("숏패딩", "body_length", "include", "크롭")])
        self.assertEqual([(v.kind, v.minor, v.value, v.match) for v in result.values],
                         [("category", "패딩", "패딩", "llm"), ("body_length", None, "크롭", "approx")])


class GenderG1Tests(unittest.TestCase):
    TABLE = {"여": ["여성", "우먼"], "남": ["남성"], "공용": ["유니섹스"]}

    def gender(self, raw, suggestion=None, table=TABLE):
        result = one(raw, "gender", suggestion=suggestion, gender_aliases=table)
        return [(v.value, v.match) for v in result.values], codes(result)

    def test_standard_value_is_exact(self):
        self.assertEqual(self.gender("여"), ([("여", "exact")], []))
        self.assertEqual(self.gender("공용"), ([("공용", "exact")], []))

    def test_table_hit_is_alias(self):
        self.assertEqual(self.gender("여성", "여"), ([("여", "alias")], []))
        self.assertEqual(self.gender("유니섹스"), ([("공용", "alias")], []))
        self.assertEqual(self.gender("여성 고객"), ([("여", "alias")], []))  # token inside the span

    def test_otherwise_llm_or_none(self):
        self.assertEqual(self.gender("숙녀", "여"), ([("여", "llm")], []))
        self.assertEqual(self.gender("숙녀"), ([(None, "none")], []))
        self.assertEqual(self.gender("숙녀", "female"), ([(None, "none")], [r.WARN_SUGGESTION_NOT_IN_VOCABULARY]))

    def test_table_overrides_the_suggestion(self):
        self.assertEqual(self.gender("남성", "여"), ([("남", "alias")], [r.WARN_LLM_SUGGESTION_OVERRIDDEN]))

    def test_repository_table_is_used_by_default(self):
        cases = {"여성": "여", "여자": "여", "우먼": "여", "남성": "남", "남자": "남", "맨즈": "남",
                 "남녀공용": "공용", "남녀 공용": "공용", "남녀": "공용", "유니섹스": "공용"}
        for raw, value in cases.items():
            with self.subTest(raw=raw):
                result = one(raw, "gender")
                self.assertEqual([(v.value, v.match) for v in result.values], [(value, "alias")])


class GenderG3Tests(unittest.TestCase):
    """Decision G-3: gender include/exclude; included 남 + 여 = 공용 (OQ-7)."""

    @staticmethod
    def run_all(*conditions):
        result = r.resolve_conditions([Condition(raw, "gender", polarity, s) for raw, polarity, s in conditions])
        return [(v.raw, v.polarity, v.value, v.match) for v in result.values], codes(result)

    def test_exclusion_is_kept_as_said(self):
        # "남성 말고" stays exclude 남 (it means 여·공용 by the contract definition; code never complements)
        self.assertEqual(self.run_all(("남성", "exclude", "남")), ([("남성", "exclude", "남", "alias")], []))

    def test_included_men_and_women_become_unisex(self):
        got, warnings = self.run_all(("남성", "include", "남"), ("여성", "include", "여"))
        self.assertEqual(got, [("남성, 여성", "include", "공용", "alias")])
        self.assertEqual(warnings, [r.WARN_GENDER_MERGED_TO_UNISEX])

    def test_merged_match_is_the_least_certain(self):
        got, _ = self.run_all(("남", "include", "남"), ("숙녀", "include", "여"))
        self.assertEqual(got, [("남, 숙녀", "include", "공용", "llm")])

    def test_existing_unisex_entry_is_kept(self):
        got, warnings = self.run_all(("유니섹스", "include", "공용"), ("남성", "include", "남"), ("여성", "include", "여"))
        self.assertEqual(got, [("유니섹스", "include", "공용", "alias")])
        self.assertEqual(warnings, [r.WARN_GENDER_MERGED_TO_UNISEX])

    def test_exclusions_are_never_merged(self):
        got, warnings = self.run_all(("남성", "exclude", "남"), ("여성", "exclude", "여"))
        self.assertEqual(got, [("남성", "exclude", "남", "alias"), ("여성", "exclude", "여", "alias")])
        self.assertEqual(warnings, [])

    def test_one_gender_is_not_merged(self):
        got, warnings = self.run_all(("여성", "include", "여"), ("남성", "exclude", "남"))
        self.assertEqual(got, [("여성", "include", "여", "alias"), ("남성", "exclude", "남", "alias")])
        self.assertEqual(warnings, [])

    def test_conflict_is_checked_after_the_merge(self):
        _, warnings = self.run_all(("남성", "include", "남"), ("여성", "include", "여"), ("공용", "exclude", "공용"))
        self.assertEqual(warnings, [r.WARN_GENDER_MERGED_TO_UNISEX, r.WARN_INCLUDE_EXCLUDE_CONFLICT])


class ConflictTests(unittest.TestCase):
    @staticmethod
    def cat(polarity, major, minor):
        return ResolvedValue("category", polarity, minor or major, minor or major, "exact", major, minor)

    def test_same_value_same_level_conflicts(self):
        warnings = include_exclude_conflicts([
            ResolvedValue("color", "include", "블랙", "블랙", "exact"),
            ResolvedValue("color", "exclude", "검정", "블랙", "alias"),
            self.cat("include", "하의", "팬츠"), self.cat("exclude", "하의", "팬츠"),
        ])
        self.assertEqual([w.code for w in warnings], [r.WARN_INCLUDE_EXCLUDE_CONFLICT] * 2)

    def test_major_include_with_minor_exclude_is_narrowing(self):
        self.assertEqual(include_exclude_conflicts([self.cat("include", "하의", None), self.cat("exclude", "하의", "팬츠")]), ())

    def test_different_kinds_or_unresolved_values_do_not_conflict(self):
        self.assertEqual(include_exclude_conflicts([
            ResolvedValue("color", "include", "블랙", "블랙", "exact"),
            ResolvedValue("pattern", "exclude", "블랙", "블랙", "exact"),
            ResolvedValue("color", "include", "연두", None, "none"),
            ResolvedValue("color", "exclude", "연두", None, "none"),
        ]), ())

    def test_resolve_conditions_reports_conflicts_and_keeps_both(self):
        result = r.resolve_conditions([
            Condition("하의", "category", "include"), Condition("슬랙스", "category", "exclude"),
            Condition("블랙", "color", "include"), Condition("블랙", "color", "exclude"),
        ])
        self.assertEqual(len(result.values), 4)
        self.assertEqual([(w.code, w.detail) for w in result.warnings], [(r.WARN_INCLUDE_EXCLUDE_CONFLICT, "color:블랙")])


class InputValidationTests(unittest.TestCase):
    def test_bad_conditions_are_rejected(self):
        for args in (("", "color", "include"), ("블랙", "size", "include"), ("블랙", "color", "maybe")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                Condition(*args)

    def test_blank_suggestion_is_no_suggestion(self):
        result = one("연두", "color", suggestion="  ")
        self.assertEqual((result.values[0].match, result.warnings), ("none", ()))


if __name__ == "__main__":
    unittest.main()
