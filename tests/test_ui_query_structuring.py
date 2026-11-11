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
    """A draft in the format of the current StructuredQuery prompt (draft_v4: flat conditions + `item`)."""
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
        self.assertEqual(out["prompt_version"], "query-structuring-prompt/v6")
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

    def test_only_out_of_scope_items_give_no_trend_input(self):
        out = server.structure_query_for_ui("벨트 보여줘", "rid-8", enabled=True,
                                            provider_factory=fixed(flat_draft([("벨트", None)])))
        self.assertEqual(out["status"], "ok")
        self.assertIsNone(out["trend_query"])
        self.assertIn("no_item_to_forward", out["warnings"])
        self.assertEqual(out["trend_input_preview"]["forwarded"], {"items": [], "seasons": 0})

    def test_status_reports_the_prompt_of_the_structured_path(self):
        self.assertEqual(server.query_structuring_status()["prompt_version"], "query-structuring-prompt/v6")

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
