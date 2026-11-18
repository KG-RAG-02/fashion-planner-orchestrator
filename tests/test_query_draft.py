"""query/draft.py: the shape of the model's draft, the label repair, and the grouped draft the assembly reads.
No model call, no resolver. Words are not from the evaluation sets (the rules are about labels, not words)."""

import importlib
import unittest

from _query_support import HAS_PYDANTIC, SKIP_REASON


def condition(**overrides):
    base = {"raw": "블랙", "kind": "color", "polarity": "include", "suggestion": "블랙", "item": None}
    base.update(overrides)
    return base


def grouped(items=(), common=(), common_unsupported=()):
    return {"seasons": [], "style_names": [], "product_model_names": [], "items": list(items),
            "common": {"conditions": list(common), "unsupported": list(common_unsupported)}}


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class QueryDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.draft = importlib.import_module("vogue_orchestrator.query.draft")
        cls.vocab = importlib.import_module("vogue_orchestrator.query.vocabulary")
        cls.config = importlib.import_module("vogue_orchestrator.query.config")
        cls.ValidationError = importlib.import_module("pydantic").ValidationError

    def validate(self, payload):
        return self.draft.QueryDraft.model_validate(payload)

    def assert_invalid(self, payload):
        with self.assertRaises(self.ValidationError):
            self.validate(payload)

    def test_full_draft_is_accepted(self):
        draft = self.validate({
            "seasons": [{"year": 2027, "season": "SS", "raw": "2027년 봄"}],
            "style_names": [{"raw": "고프코어"}],
            "product_model_names": [{"raw": "에어맥스"}],
            "items": [{"raw": "숏패딩", "suggestion": "패딩"}, {"raw": "후드티", "suggestion": "후드티"}],
            "conditions": [
                condition(raw="버터옐로우", suggestion="옐로우", item="후드티"),
                condition(raw="블랙", polarity="exclude"),
                condition(raw="오버핏", kind="fit", suggestion="오버사이즈", item="후드티"),
                condition(raw="여성", kind="gender", suggestion="여"),
            ],
            "unsupported": [
                {"type": "abstract", "raw": "꾸안꾸 느낌", "polarity": "include", "item": None},
                {"type": "brand", "raw": "자라", "polarity": "exclude", "item": "숏패딩"},
            ],
        })
        self.assertEqual((len(draft.items), len(draft.conditions)), (2, 4))
        self.assertEqual((draft.conditions[0].item, draft.conditions[1].item), ("후드티", None))
        self.assertEqual(draft.unsupported[1].polarity, "exclude")
        self.assertEqual(
            draft.raws(),
            ["2027년 봄", "고프코어", "에어맥스", "숏패딩", "후드티", "버터옐로우", "블랙", "오버핏", "여성", "꾸안꾸 느낌", "자라"],
        )

    def test_empty_draft_defaults(self):
        draft = self.validate({})
        self.assertEqual((draft.seasons, draft.style_names, draft.product_model_names, draft.items,
                          draft.conditions, draft.unsupported), ([], [], [], [], [], []))
        self.assertEqual(draft.raws(), [])

    def test_suggestion_outside_the_vocabulary_is_accepted_here(self):
        # The resolver discards it with a warning; the shape check must not drop the whole draft.
        for payload in (
            condition(suggestion="연두"),                                # not a standard color
            condition(kind="category", raw="블레이저", suggestion="블레이저"),  # not a standard minor
            condition(kind="fit", suggestion="블랙"),                     # standard value of another kind
            condition(kind="gender", raw="여성", suggestion="여성"),
            condition(suggestion=""),
        ):
            with self.subTest(suggestion=payload["suggestion"], kind=payload["kind"]):
                draft = self.validate({"conditions": [payload]})
                self.assertEqual(draft.conditions[0].suggestion, payload["suggestion"])

    def test_suggestion_and_item_may_be_null_or_omitted(self):
        self.assertIsNone(self.validate({"conditions": [condition(suggestion=None)]}).conditions[0].suggestion)
        omitted = {k: v for k, v in condition().items() if k not in ("suggestion", "item")}
        parsed = self.validate({"conditions": [omitted]}).conditions[0]
        self.assertEqual((parsed.suggestion, parsed.item), (None, None))

    def test_every_kind_is_accepted(self):
        self.assertEqual(
            self.draft.CONDITION_KINDS,
            ("category", "color", "material", "pattern", "detail", "fit", "body_length", "sleeve_length",
             "gender"),
        )
        self.assertEqual(self.draft.CONDITION_KINDS[1:6], self.vocab.ATTRIBUTE_TYPES)
        self.assertEqual(self.draft.UNSUPPORTED_TYPES, (
            "brand", "category", "attribute", "abstract", "price", "market", "channel", "age", "other"))
        for kind in self.draft.CONDITION_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(self.validate({"conditions": [condition(kind=kind)]}).conditions[0].kind, kind)

    def test_closed_sets_are_enforced(self):
        cases = {
            "unknown kind": {"conditions": [condition(kind="length")]},
            "attribute type as kind": {"conditions": [condition(kind="silhouette")]},
            "unknown polarity": {"conditions": [condition(polarity="neutral")]},
            "unknown unsupported type": {"unsupported": [{"type": "occasion", "raw": "x", "polarity": "include"}]},
            "unsupported polarity": {"unsupported": [{"type": "brand", "raw": "x", "polarity": "maybe"}]},
            "season code": {"seasons": [{"year": None, "season": "봄", "raw": "봄"}]},
        }
        for case, payload in cases.items():
            with self.subTest(case=case):
                self.assert_invalid(payload)

    def test_unsupported_types_cover_the_configured_reasons(self):
        types = set(self.draft.UNSUPPORTED_TYPES)
        self.assertLessEqual(set(self.config.CONFIG.unsupported_reasons), types)
        for unsupported_type in self.draft.UNSUPPORTED_TYPES:
            with self.subTest(type=unsupported_type):
                self.validate({"unsupported": [{"type": unsupported_type, "raw": "x", "polarity": "include"}]})

    def test_required_fields(self):
        cases = {
            "condition without raw": {"conditions": [{k: v for k, v in condition().items() if k != "raw"}]},
            "condition without kind": {"conditions": [{k: v for k, v in condition().items() if k != "kind"}]},
            "condition without polarity": {"conditions": [{k: v for k, v in condition().items() if k != "polarity"}]},
            "empty raw": {"conditions": [condition(raw="")]},
            "blank raw": {"conditions": [condition(raw="  ")]},
            "blank unsupported raw": {"unsupported": [{"type": "brand", "raw": " ", "polarity": "include"}]},
            "blank item raw": {"items": [{"raw": "\t"}]},
            "unsupported without polarity": {"unsupported": [{"type": "brand", "raw": "자라"}]},
            "season without raw": {"seasons": [{"year": 2027, "season": "SS"}]},
            "season without code": {"seasons": [{"year": 2027, "raw": "2027"}]},
            "term without raw": {"style_names": [{}]},
        }
        for case, payload in cases.items():
            with self.subTest(case=case):
                self.assert_invalid(payload)

    def test_unknown_keys_and_other_shapes_are_rejected(self):
        # `match` and `value` are decided by code; a model that writes them drifted from the prompt.
        cases = {
            "match on a condition": {"conditions": [condition(match="exact")]},
            "value on a condition": {"conditions": [condition(value="블랙")]},
            "reason on unsupported": {"unsupported": [{"type": "brand", "raw": "자라", "polarity": "exclude",
                                                       "reason": "x"}]},
            "origin on season": {"seasons": [{"year": 2027, "season": "SS", "raw": "2027", "origin": "default_next"}]},
            "value on a term": {"style_names": [{"raw": "고프코어", "value": None}]},
            "single season key": {"season": None},
            "item_terms key": {"item_terms": []},
            "grouped shape": grouped(),
            "conditions inside an item": {"items": [{"raw": "점퍼", "suggestion": "점퍼", "conditions": []}]},
        }
        for case, payload in cases.items():
            with self.subTest(case=case):
                self.assert_invalid(payload)

    def test_season_year_bounds_come_from_the_config(self):
        low, high = self.config.CONFIG.draft_year_min, self.config.CONFIG.draft_year_max
        for year in (low, high, None):
            with self.subTest(year=year):
                draft = self.validate({"seasons": [{"year": year, "season": "FW", "raw": "가을"}]})
                self.assertEqual(draft.seasons[0].year, year)
        for year in (low - 1, high + 1, 27):
            with self.subTest(year=year):
                self.assert_invalid({"seasons": [{"year": year, "season": "FW", "raw": "가을"}]})
        self.assertIsNone(self.validate({"seasons": [{"season": "SS", "raw": "봄"}]}).seasons[0].year)


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class GroupedQueryDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.draft = importlib.import_module("vogue_orchestrator.query.draft")
        cls.ValidationError = importlib.import_module("pydantic").ValidationError

    def validate(self, payload):
        return self.draft.GroupedQueryDraft.model_validate(payload)

    def test_shape_and_defaults(self):
        parsed = self.validate({})
        self.assertEqual((parsed.items, parsed.common.conditions, parsed.common.unsupported), ([], [], []))
        parsed = self.validate(grouped(items=[{"raw": "점퍼", "suggestion": "점퍼"}]))
        self.assertEqual((parsed.items[0].conditions, parsed.items[0].unsupported), ([], []))

    def test_unknown_keys_are_rejected(self):
        c = {k: v for k, v in condition(raw="카키").items() if k != "item"}
        for bad in (
            grouped(items=[{"raw": "점퍼", "suggestion": "점퍼", "category": "점퍼"}]),
            grouped(items=[{"raw": " ", "suggestion": None}]),                     # blank raw
            {**grouped(), "common": {"conditions": [], "unsupported": [], "items": []}},
            grouped(items=[{"raw": "점퍼", "suggestion": "점퍼", "conditions": [{**c, "match": "exact"}]}]),
            grouped(common=[condition()]),                                          # `item` belongs to the model's shape
        ):
            with self.subTest(draft=bad):
                with self.assertRaises(self.ValidationError):
                    self.validate(bad)

    def test_raws_cover_every_block_in_order(self):
        c = lambda raw, kind: {"raw": raw, "kind": kind, "polarity": "include", "suggestion": None}  # noqa: E731
        parsed = self.validate({**grouped(
            items=[{"raw": "점퍼", "suggestion": "점퍼", "conditions": [c("카키", "color")],
                    "unsupported": [{"type": "brand", "raw": "어떤브랜드", "polarity": "include"}]}],
            common=[c("남성", "gender")]), "style_names": [{"raw": "고딕"}]})
        self.assertEqual(parsed.raws(), ["고딕", "점퍼", "카키", "어떤브랜드", "남성"])

    def test_flatten_then_group_returns_the_grouped_draft(self):
        c = lambda raw, kind: {"raw": raw, "kind": kind, "polarity": "include", "suggestion": None}  # noqa: E731
        payload = grouped(
            items=[{"raw": "점퍼", "suggestion": "점퍼", "conditions": [c("카키", "color")], "unsupported": []},
                   {"raw": "레깅스", "suggestion": "레깅스", "conditions": [],
                    "unsupported": [{"type": "brand", "raw": "어떤브랜드", "polarity": "exclude"}]}],
            common=[c("남성", "gender")])
        flat = self.draft.QueryDraft.model_validate(self.draft.flatten_draft(payload))
        result = self.draft.group_draft(flat, "카키 점퍼랑 어떤브랜드 말고 레깅스, 남성")
        self.assertEqual(result.draft, self.validate(payload))
        self.assertEqual(result.warnings, ())


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class LabelRepairTests(unittest.TestCase):
    """Only the two closed label sets are repaired, never the structure; a moved entry keeps its `item`."""

    @classmethod
    def setUpClass(cls):
        cls.draft = importlib.import_module("vogue_orchestrator.query.draft")

    def repair(self, conditions=(), unsupported=()):
        payload = {"seasons": [], "style_names": [], "product_model_names": [], "items": [{"raw": "점퍼"}],
                   "conditions": list(conditions), "unsupported": list(unsupported)}
        fixed, warnings = self.draft.repair_draft_labels(payload)
        self.draft.QueryDraft.model_validate(fixed)  # the repaired draft is valid
        return fixed, list(warnings)

    def test_unsupported_type_written_as_kind_moves_to_unsupported(self):
        fixed, warnings = self.repair(conditions=[
            {"raw": "고급스러운", "kind": "abstract", "polarity": "include", "item": "점퍼"},
            {"raw": "백화점", "kind": "channel", "polarity": "exclude", "suggestion": None},
            {"raw": "코듀로이", "kind": "material", "polarity": "include", "suggestion": "코듀로이"},
        ])
        self.assertEqual(fixed["conditions"], [{"raw": "코듀로이", "kind": "material", "polarity": "include", "suggestion": "코듀로이"}])
        self.assertEqual(fixed["unsupported"], [{"type": "abstract", "raw": "고급스러운", "polarity": "include", "item": "점퍼"},
                                                {"type": "channel", "raw": "백화점", "polarity": "exclude"}])
        self.assertEqual(warnings, [self.draft.WARN_DRAFT_KIND_MOVED_TO_UNSUPPORTED])

    def test_unknown_kind_becomes_other(self):
        fixed, warnings = self.repair(conditions=[{"raw": "세미정장", "kind": "occasion", "polarity": "include"}])
        self.assertEqual(fixed["unsupported"], [{"type": "other", "raw": "세미정장", "polarity": "include"}])
        self.assertEqual(warnings, [self.draft.WARN_DRAFT_UNKNOWN_KIND])

    def test_condition_kind_written_as_unsupported_type_moves_to_conditions(self):
        fixed, warnings = self.repair(unsupported=[{"type": "sleeve_length", "raw": "칠부", "polarity": "include", "item": "점퍼"},
                                                   {"type": "category", "raw": "부츠", "polarity": "exclude"}])
        self.assertEqual(fixed["conditions"], [{"raw": "칠부", "kind": "sleeve_length", "polarity": "include",
                                                "suggestion": None, "item": "점퍼"}])
        self.assertEqual(fixed["unsupported"], [{"type": "category", "raw": "부츠", "polarity": "exclude"}])
        self.assertEqual(warnings, [self.draft.WARN_DRAFT_TYPE_MOVED_TO_CONDITIONS])

    def test_unknown_unsupported_type_becomes_other(self):
        fixed, warnings = self.repair(unsupported=[{"type": "size", "raw": "XL", "polarity": "include"}])
        self.assertEqual(fixed["unsupported"], [{"type": "other", "raw": "XL", "polarity": "include"}])
        self.assertEqual(warnings, [self.draft.WARN_DRAFT_UNKNOWN_UNSUPPORTED_TYPE])

    def test_a_valid_draft_is_returned_unchanged(self):
        payload = {"conditions": [{"raw": "셔츠", "kind": "category", "polarity": "include", "suggestion": "셔츠"}]}
        self.assertEqual(self.draft.repair_draft_labels(payload), (payload, ()))

    def test_structural_errors_are_not_repaired(self):
        for entry in ({"raw": "x", "kind": "abstract", "polarity": "maybe"},   # bad polarity
                      {"raw": " ", "kind": "abstract", "polarity": "include"},  # blank raw
                      {"kind": "abstract", "polarity": "include"},              # no raw
                      "abstract"):                                               # not an object
            with self.subTest(entry=entry):
                payload = {"conditions": [entry]}
                fixed, warnings = self.draft.repair_draft_labels(payload)
                self.assertEqual((fixed, warnings), (payload, ()))
                with self.assertRaises(importlib.import_module("pydantic").ValidationError):
                    self.draft.QueryDraft.model_validate(fixed)
        for odd in ("text", None, {"conditions": "x"}):
            with self.subTest(payload=odd):
                self.assertEqual(self.draft.repair_draft_labels(odd), (odd, ()))


if __name__ == "__main__":
    unittest.main()
