"""UI hook: server.structure_query_for_ui runs the query module in-process (StructuredQuery path, prompt v6,
decision FW-1). Fake provider, no network."""

import unittest

from _query_support import HAS_PYDANTIC, SKIP_REASON

from vogue_orchestrator import server
from vogue_orchestrator.llm_provider import LLMConfigurationError

SECRET = "synthetic-private-query-marker"


def fixed(produced):
    return lambda: ((lambda bundle: produced), {"provider": "fake", "model": "fake-model"})


class SkippedAndConfigurationTests(unittest.TestCase):
    def test_without_consent_nothing_is_called(self):
        def factory():
            raise AssertionError("provider must not be built without consent")
        out = server.structure_query_for_ui("질의", "rid-1", enabled=False, provider_factory=factory)
        self.assertEqual((out["status"], out["request_id"], out["llm"]), ("skipped", "rid-1", None))

    @unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
    def test_missing_llm_settings_are_reported_as_a_code(self):
        def factory():
            raise LLMConfigurationError("LLM_MODEL and LLM_API_KEY must be set (environment or .env)")
        out = server.structure_query_for_ui("질의", "rid-1", enabled=True, provider_factory=factory)
        self.assertEqual((out["status"], out["error_code"]), ("error", "LLM_NOT_CONFIGURED"))


def flat_draft(items, conditions=(), seasons=()):
    """A draft in the model's format (draft.QueryDraft: flat conditions with an `item` field)."""
    return {"seasons": list(seasons), "style_names": [], "product_model_names": [],
            "items": [{"raw": raw, "suggestion": suggestion} for raw, suggestion in items],
            "conditions": list(conditions), "unsupported": []}


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class StructuringTests(unittest.TestCase):
    # FW-1 (2026-11-11): the UI runs the StructuredQuery path; trend would get the first item + first season
    QUERY = "위민즈 코듀로이 점퍼랑 레깅스, 올봄이랑 가을"
    DRAFT = flat_draft(
        [("점퍼", "점퍼"), ("레깅스", "레깅스")],
        [{"raw": "위민즈", "kind": "gender", "polarity": "include", "suggestion": "여", "item": None},
         {"raw": "코듀로이", "kind": "material", "polarity": "include", "suggestion": "코듀로이", "item": "점퍼"}],
        [{"year": None, "season": "SS", "raw": "올봄"}, {"year": None, "season": "FW", "raw": "가을"}])

    def test_query_is_structured_and_only_the_first_item_and_season_are_previewed_for_trend(self):
        out = server.structure_query_for_ui(self.QUERY, "rid-7", enabled=True, provider_factory=fixed(self.DRAFT))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["llm"], {"provider": "fake", "model": "fake-model"})
        self.assertEqual(out["prompt_id"], "query-structuring")
        self.assertRegex(out["prompt_sha256"], r"^[0-9a-f]{64}$")
        sq = out["structured_query"]
        self.assertEqual([i["item_id"] for i in sq["items"]], ["i1", "i2"])
        self.assertEqual(len(sq["trend_terms"]["seasons"]), 2)
        tq = out["trend_query"]
        self.assertEqual(tq["request_id"], "rid-7")
        self.assertEqual([c["minor"] for c in tq["requested"]["categories"]["include"]], ["점퍼"])
        self.assertEqual([a["value"] for a in tq["requested"]["attributes"]], ["코듀로이"])
        self.assertEqual(tq["requested"]["gender"], {"raw": "위민즈", "value": "여"})
        self.assertEqual(tq["trend_terms"]["season"]["raw"], "올봄")
        self.assertIn("items_not_forwarded", out["warnings"])
        self.assertIn("seasons_not_forwarded", out["warnings"])
        preview = out["trend_input_preview"]
        self.assertEqual((preview["sent"], preview["request_id"], preview["query"]), (False, "rid-7", self.QUERY))
        self.assertEqual(preview["trend_query"], tq)
        self.assertEqual(preview["forwarded"], {"items": ["i1"], "seasons": 1})

    def test_only_out_of_scope_items_still_give_a_trend_input_over_every_category(self):
        # IT-7′ (2026-11-12): the season reaches trend; 벨트 goes to unsupported as a category
        out = server.structure_query_for_ui("2027SS 벨트 보여줘", "rid-8", enabled=True, provider_factory=fixed(
            flat_draft([("벨트", None)], seasons=[{"year": 2027, "season": "SS", "raw": "2027SS"}])))
        self.assertEqual(out["status"], "ok")
        tq = out["trend_query"]
        self.assertEqual(tq["requested"]["categories"]["include"], [])
        self.assertEqual((tq["trend_terms"]["season"]["year"], tq["trend_terms"]["season"]["season"]), (2027, "SS"))
        self.assertEqual([(u["type"], u["raw"]) for u in tq["unsupported"]], [("category", "벨트")])
        self.assertIn("only_out_of_scope_items", out["warnings"])
        self.assertNotIn("no_item_to_forward", out["warnings"])
        self.assertEqual(out["trend_input_preview"]["forwarded"], {"items": ["i1"], "seasons": 1})

    def test_a_sleeve_length_alone_reaches_trend_with_its_season(self):
        # IT-10 (2026-11-12): the query reported on 2026-11-12 ("2027SS 여성 반팔 제작하고 싶어")
        draft = flat_draft([("반팔", "반팔")], conditions=[
            {"raw": "여성", "kind": "gender", "polarity": "include", "suggestion": "여", "item": None}],
            seasons=[{"year": 2027, "season": "SS", "raw": "2027SS"}])
        out = server.structure_query_for_ui("2027SS 여성 반팔 제작하고 싶어", "rid-10", enabled=True,
                                            provider_factory=fixed(draft))
        tq = out["trend_query"]
        self.assertIsNotNone(tq)
        self.assertEqual(tq["requested"]["categories"]["include"], [])
        self.assertEqual(tq["requested"]["gender"], {"raw": "여성", "value": "여"})
        self.assertEqual([(u["type"], u["raw"]) for u in tq["unsupported"]], [("sleeve_length", "반팔")])

    def test_synthetic_fixtures_give_the_trend_query_of_the_v1_path(self):
        # main's offline E2E mode (VOGUE_QUERY_STRUCTURING_MODE=synthetic_fixture) serves each fixture's
        # `structured_draft`; the first-item TrendQuery must match the fixture's expected_trend_query
        # (categories, gender, attributes, user-named season), except the three documented differences.
        from _query_support import load_fixtures

        for case in load_fixtures():
            with self.subTest(case=case["case"]):
                out = server.structure_query_for_ui(case["query"], case["request_id"], enabled=True,
                                                    provider_factory=fixed(case["structured_draft"]))
                self.assertEqual(out["status"], "ok", out.get("error_code"))
                tq, expected = out["trend_query"], case["expected_trend_query"]
                requested = expected["requested"]
                if case["case"] == "nonstandard-values":  # γ: the alias dictionary answers first (approx 베이지)
                    requested = {**requested, "attributes": [{"type": "color", "raw": "카키베이지", "value": "베이지"}]}
                    color = out["structured_query"]["items"][0]["attributes"]["include"]["color"][0]
                    self.assertEqual((color["value"], color["match"]), ("베이지", "approx"))
                self.assertEqual(tq["requested"], requested)
                if expected["trend_terms"]["season"]["origin"] == "user_requested":
                    self.assertEqual(tq["trend_terms"], expected["trend_terms"])
                got = [(u["type"], u["raw"]) for u in tq["unsupported"]]
                want = [(u["type"], u["raw"]) for u in expected["unsupported"]]
                if case["case"] == "abstract-and-length":  # a length is a body_length condition now (OQ-9′)
                    want = [("abstract", "포멀한 느낌"), ("body_length", "길이가 긴")]
                if case["case"] == "nonstandard-values":  # 가방 is out of scope (IT-7), reported as a category (IT-7′)
                    want = [("category", "가방")]
                    self.assertEqual([o["raw"] for o in out["structured_query"]["out_of_scope_items"]], ["가방"])
                self.assertEqual(sorted(got), sorted(want))

    def test_synthetic_fixture_mode_serves_the_structured_drafts(self):
        provider, info = server._synthetic_fixture_provider_factory()
        self.assertEqual(info["provider"], "synthetic_fixture")
        out = server.structure_query_for_ui("하의 빼고 해줘", "rid-9", enabled=True,
                                            provider_factory=lambda: (provider, info))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["trend_query"]["requested"]["categories"]["exclude"][0]["major"], "하의")

    def test_provider_failure_keeps_only_the_exception_class_name(self):
        def factory():
            def provider(bundle):
                raise TimeoutError(f"timed out for {SECRET}")
            return provider, {"provider": "fake", "model": "fake-model"}
        out = server.structure_query_for_ui(f"질의 {SECRET}", "rid-1", enabled=True, provider_factory=factory)
        self.assertEqual((out["status"], out["error_code"], out["provider_error"]),
                         ("error", "PROVIDER_FAILED", "TimeoutError"))
        self.assertNotIn(SECRET, str(out))

    def test_non_json_or_invalid_drafts_are_reported_without_echoing_output(self):
        for produced, code in ((f"not json {SECRET}", "DRAFT_NOT_JSON"), ({"secret": SECRET}, "DRAFT_INVALID")):
            with self.subTest(code=code):
                out = server.structure_query_for_ui("질의", "rid-1", enabled=True, provider_factory=fixed(produced))
                self.assertEqual((out["status"], out["error_code"]), ("error", code))
                self.assertNotIn(SECRET, str(out))


if __name__ == "__main__":
    unittest.main()
