"""Draft v3 (query/draft_v3.py): the per-item LLM output of prompt v4 and its label repair. No model call.

Words are not from the evaluation data (W-10)."""

import importlib
import unittest

from _query_support import HAS_PYDANTIC, SKIP_REASON


def c(raw, kind, polarity="include", suggestion=None):
    return {"raw": raw, "kind": kind, "polarity": polarity, "suggestion": suggestion}


def draft(items=(), common=(), common_unsupported=()):
    return {"seasons": [], "style_names": [], "product_model_names": [], "items": list(items),
            "common": {"conditions": list(common), "unsupported": list(common_unsupported)}}


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class DraftV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module("vogue_orchestrator.query.draft_v3")
        cls.ValidationError = importlib.import_module("pydantic").ValidationError

    def validate(self, payload):
        return self.module.QueryDraftV3.model_validate(payload)

    def test_shape_and_defaults(self):
        self.assertEqual(self.module.DRAFT_FORMAT, "draft_v3")
        parsed = self.validate({})
        self.assertEqual((parsed.items, parsed.common.conditions, parsed.common.unsupported), ([], [], []))
        parsed = self.validate(draft(items=[{"raw": "점퍼", "suggestion": "점퍼"}]))
        self.assertEqual((parsed.items[0].conditions, parsed.items[0].unsupported), ([], []))

    def test_unknown_keys_and_the_flat_v2_shape_are_rejected(self):
        for bad in (
            {"conditions": []},                                                   # draft v2 shape
            {**draft(), "season": None},                                           # renamed to seasons (SQ-2)
            {**draft(), "item_terms": []},                                         # renamed (SQ-1)
            draft(items=[{"raw": "점퍼", "suggestion": "점퍼", "category": "점퍼"}]),
            draft(items=[{"raw": " ", "suggestion": None}]),                       # blank raw
            {**draft(), "common": {"conditions": [], "unsupported": [], "items": []}},
            draft(items=[{"raw": "점퍼", "suggestion": "점퍼", "conditions": [{**c("카키", "color"), "match": "exact"}]}]),
        ):
            with self.subTest(draft=bad):
                with self.assertRaises(self.ValidationError):
                    self.validate(bad)

    def test_raws_cover_every_block_in_order(self):
        parsed = self.validate({**draft(
            items=[{"raw": "점퍼", "suggestion": "점퍼", "conditions": [c("카키", "color")],
                    "unsupported": [{"type": "brand", "raw": "어떤브랜드", "polarity": "include"}]}],
            common=[c("남성", "gender")]), "style_names": [{"raw": "고딕"}]})
        self.assertEqual(parsed.raws(), ["고딕", "점퍼", "카키", "어떤브랜드", "남성"])

    def test_label_repair_runs_in_common_and_in_every_item(self):
        payload = draft(
            items=[{"raw": "점퍼", "suggestion": "점퍼", "conditions": [c("30대", "age")], "unsupported": []},
                   {"raw": "레깅스", "suggestion": "레깅스", "conditions": [],
                    "unsupported": [{"type": "color", "raw": "카키", "polarity": "include"}]}],
            common=[c("힙한", "vibe")])
        repaired, warnings = self.module.repair_draft_v3_labels(payload)
        parsed = self.validate(repaired)
        self.assertEqual([(u.type, u.raw) for u in parsed.items[0].unsupported], [("age", "30대")])
        self.assertEqual([(x.kind, x.raw) for x in parsed.items[1].conditions], [("color", "카키")])
        self.assertEqual([(u.type, u.raw) for u in parsed.common.unsupported], [("other", "힙한")])
        self.assertEqual(set(warnings), {"draft_kind_moved_to_unsupported", "draft_unsupported_type_moved_to_conditions",
                                         "draft_unknown_kind"})

    def test_repair_leaves_good_or_malformed_drafts_alone(self):
        good = draft(items=[{"raw": "점퍼", "suggestion": "점퍼", "conditions": [c("카키", "color")], "unsupported": []}])
        self.assertEqual(self.module.repair_draft_v3_labels(good), (good, ()))
        for odd in ("text", None, {"items": "x"}, {"common": None}):
            with self.subTest(payload=odd):
                self.assertEqual(self.module.repair_draft_v3_labels(odd), (odd, ()))


if __name__ == "__main__":
    unittest.main()
