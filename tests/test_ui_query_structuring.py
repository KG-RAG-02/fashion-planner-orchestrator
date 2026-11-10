"""UI hook: server.structure_query_for_ui runs the query module in-process. Fake provider, no network."""

import unittest

from _query_support import HAS_PYDANTIC, SKIP_REASON, load_fixtures

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


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class StructuringTests(unittest.TestCase):
    def test_fixture_query_is_structured_and_previewed_for_the_trend_stage(self):
        case = load_fixtures()[0]
        out = server.structure_query_for_ui(case["query"], "rid-7", enabled=True,
                                            provider_factory=fixed(case["draft"]))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["llm"], {"provider": "fake", "model": "fake-model"})
        self.assertEqual(out["prompt_version"], "query-structuring-prompt/v1")
        self.assertEqual(out["trend_query"]["request_id"], "rid-7")
        preview = out["trend_input_preview"]
        self.assertEqual((preview["sent"], preview["request_id"], preview["query"]), (False, "rid-7", case["query"]))
        self.assertEqual(preview["trend_query"], out["trend_query"])

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
