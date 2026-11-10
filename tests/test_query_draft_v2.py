"""Draft v2 (query/draft.py): the shape of the LLM output under prompt v2. No model call, no resolver."""

import importlib
import unittest

from _query_support import HAS_PYDANTIC, SKIP_REASON


def condition(**overrides):
    base = {"raw": "블랙", "kind": "color", "polarity": "include", "suggestion": "블랙"}
    base.update(overrides)
    return base


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class DraftV2Tests(unittest.TestCase):
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
            "season": {"year": 2027, "season": "SS", "raw": "2027년 봄"},
            "style_names": [{"raw": "고프코어"}],
            "item_terms": [{"raw": "숏패딩"}],
            "conditions": [
                condition(raw="버터옐로우", suggestion="옐로우"),
                condition(raw="블랙", polarity="exclude"),
                condition(raw="오버핏", kind="fit", suggestion="오버사이즈"),
                condition(raw="후드티", kind="category", suggestion="후드티"),
                condition(raw="여성", kind="gender", suggestion="여"),
            ],
            "unsupported": [
                {"type": "abstract", "raw": "꾸안꾸 느낌", "polarity": "include"},
                {"type": "brand", "raw": "자라", "polarity": "exclude"},
            ],
        })
        self.assertEqual(len(draft.conditions), 5)
        self.assertEqual(draft.conditions[1].polarity, "exclude")
        self.assertEqual(draft.unsupported[1].polarity, "exclude")
        self.assertEqual(
            draft.raws(),
            ["2027년 봄", "고프코어", "숏패딩", "버터옐로우", "블랙", "오버핏", "후드티", "여성", "꾸안꾸 느낌", "자라"],
        )

    def test_empty_draft_defaults(self):
        draft = self.validate({})
        self.assertIsNone(draft.season)
        self.assertEqual((draft.style_names, draft.item_terms, draft.conditions, draft.unsupported), ([], [], [], []))
        self.assertEqual(draft.raws(), [])

    def test_suggestion_outside_the_vocabulary_is_accepted_here(self):
        # The resolver discards it with a warning (plan §2.1); the shape check must not drop the whole draft.
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

    def test_suggestion_may_be_null_or_omitted(self):
        self.assertIsNone(self.validate({"conditions": [condition(suggestion=None)]}).conditions[0].suggestion)
        omitted = {k: v for k, v in condition().items() if k != "suggestion"}
        self.assertIsNone(self.validate({"conditions": [omitted]}).conditions[0].suggestion)

    def test_every_kind_is_accepted(self):
        self.assertEqual(
            self.draft.CONDITION_KINDS,
            ("category", "color", "material", "pattern", "detail", "fit", "gender"),
        )
        self.assertEqual(self.draft.CONDITION_KINDS[1:6], self.vocab.ATTRIBUTE_TYPES)
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
            "season code": {"season": {"year": None, "season": "봄", "raw": "봄"}},
        }
        for case, payload in cases.items():
            with self.subTest(case=case):
                self.assert_invalid(payload)

    def test_unsupported_types_cover_the_configured_reasons_and_the_v1_types(self):
        types = set(self.draft.UNSUPPORTED_TYPES)
        self.assertLessEqual(set(self.config.CONFIG.unsupported_reasons), types)
        self.assertLessEqual({"brand", "price", "market", "channel", "age", "category", "attribute", "abstract"}, types)
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
            "blank term raw": {"item_terms": [{"raw": "	"}]},
            "unsupported without polarity": {"unsupported": [{"type": "brand", "raw": "자라"}]},
            "season without raw": {"season": {"year": 2027, "season": "SS"}},
            "season without code": {"season": {"year": 2027, "raw": "2027"}},
            "term without raw": {"style_names": [{}]},
        }
        for case, payload in cases.items():
            with self.subTest(case=case):
                self.assert_invalid(payload)

    def test_unknown_keys_are_rejected(self):
        # `match` and `value` are decided by code; a model that writes them drifted from the prompt.
        cases = {
            "match on a condition": {"conditions": [condition(match="exact")]},
            "value on a condition": {"conditions": [condition(value="블랙")]},
            "reason on unsupported": {"unsupported": [{"type": "brand", "raw": "자라", "polarity": "exclude",
                                                       "reason": "x"}]},
            "v1 top-level key": {"include_categories": []},
            "v1 attributes": {"attributes": []},
            "origin on season": {"season": {"year": 2027, "season": "SS", "raw": "2027", "origin": "default_next"}},
            "value on a term": {"style_names": [{"raw": "고프코어", "value": None}]},
        }
        for case, payload in cases.items():
            with self.subTest(case=case):
                self.assert_invalid(payload)

    def test_season_year_bounds_come_from_the_config(self):
        low, high = self.config.CONFIG.draft_year_min, self.config.CONFIG.draft_year_max
        for year in (low, high, None):
            with self.subTest(year=year):
                draft = self.validate({"season": {"year": year, "season": "FW", "raw": "가을"}})
                self.assertEqual(draft.season.year, year)
        for year in (low - 1, high + 1, 27):
            with self.subTest(year=year):
                self.assert_invalid({"season": {"year": year, "season": "FW", "raw": "가을"}})

    def test_season_year_may_be_omitted(self):
        self.assertIsNone(self.validate({"season": {"season": "SS", "raw": "봄"}}).season.year)

    def test_format_marker(self):
        self.assertEqual(self.draft.DRAFT_FORMAT, "draft_v2")


if __name__ == "__main__":
    unittest.main()
