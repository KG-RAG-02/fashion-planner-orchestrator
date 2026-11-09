"""End-to-end structuring with a fixture-backed draft provider, and failure behaviour."""

import importlib
import json
import unittest
from datetime import date

from _query_support import HAS_PYDANTIC, SKIP_REASON, load_fixtures

TODAY = date(2026, 11, 9)
SECRET = "synthetic-private-query-marker"


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class StructureQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.structure = importlib.import_module("vogue_orchestrator.query.structure")
        cls.errors = importlib.import_module("vogue_orchestrator.query.errors")
        cls.synthetic = importlib.import_module("vogue_orchestrator.query.synthetic")
        cls.models = importlib.import_module("vogue_orchestrator.query.models")

    def run_structure(self, provider, query="합성 질의", request_id="rid-1", **kwargs):
        return self.structure.structure_query(
            query, request_id=request_id, draft_provider=provider, today=kwargs.pop("today", TODAY), **kwargs,
        )

    def assert_fails(self, code, *args, **kwargs):
        with self.assertRaises(self.errors.QueryStructuringError) as caught:
            self.run_structure(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_every_fixture_produces_its_expected_trend_query(self):
        fixtures = load_fixtures()
        self.assertGreaterEqual(len(fixtures), 5)
        for fixture in fixtures:
            with self.subTest(case=fixture["case"]):
                provider = self.synthetic.FixtureDraftProvider({fixture["query"]: fixture["draft"]})
                result = self.structure.structure_query(
                    fixture["query"], request_id=fixture["request_id"], draft_provider=provider,
                    today=date.fromisoformat(fixture["today"]),
                )
                self.assertEqual(result.trend_query.model_dump(mode="json"), fixture["expected_trend_query"])
                self.assertEqual(list(result.warnings), fixture["expected_warnings"])
                self.assertEqual(result.prompt_version, "query-structuring-prompt/v1")

    def test_result_is_json_serialisable_and_round_trips_through_the_contract_model(self):
        fixture = load_fixtures()[0]
        provider = self.synthetic.FixtureDraftProvider({fixture["query"]: fixture["draft"]})
        result = self.run_structure(provider, query=fixture["query"], request_id="rid-9")
        payload = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
        self.models.TrendQuery.model_validate(payload["trend_query"])
        self.assertEqual(payload["trend_query"]["request_id"], "rid-9")

    def test_provider_may_return_json_text_with_or_without_a_code_fence(self):
        body = json.dumps({"include_categories": [{"raw": "코트", "major": "아우터", "minor": "코트"}]},
                          ensure_ascii=False)
        for text in (body, f"```json\n{body}\n```", f"  {body}  "):
            with self.subTest(text=text[:12]):
                result = self.run_structure(lambda bundle, text=text: text, query="코트")
                self.assertEqual(result.trend_query.requested.categories.include[0].minor, "코트")

    def test_provider_failure_is_reported_without_echoing_query_or_exception_text(self):
        def failing(bundle):
            raise RuntimeError(SECRET)

        error = self.assert_fails(self.errors.PROVIDER_FAILED, failing, query=SECRET)
        self.assertNotIn(SECRET, error.message)
        self.assertNotIn(SECRET, str(error))
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__)

    def test_unknown_query_in_the_fixture_double_fails_like_a_provider_outage(self):
        provider = self.synthetic.FixtureDraftProvider({})
        self.assert_fails(self.errors.PROVIDER_FAILED, provider)

    def test_non_json_output_is_rejected(self):
        for produced in ("그냥 문장입니다", "[1, 2]", "", None, 42):
            with self.subTest(produced=produced):
                self.assert_fails(self.errors.DRAFT_NOT_JSON, lambda bundle, p=produced: p)

    def test_draft_with_unknown_keys_or_bad_types_is_rejected_without_echoing_values(self):
        error = self.assert_fails(self.errors.DRAFT_INVALID, lambda bundle: {"brand": SECRET}, query=SECRET)
        self.assertNotIn(SECRET, str(error))
        self.assert_fails(self.errors.DRAFT_INVALID, lambda bundle: {"season": {"season": "봄", "raw": "봄"}})
        self.assert_fails(self.errors.DRAFT_INVALID, lambda bundle: {"season": {"year": 1999, "season": "SS", "raw": "x"}})

    def test_invalid_input_is_rejected_before_the_provider_is_called(self):
        calls = []

        def provider(bundle):
            calls.append(bundle)
            return {}

        for query in ("", "   ", "가" * 1001):
            self.assert_fails(self.errors.INPUT_INVALID, provider, query=query)
        self.assert_fails(self.errors.INPUT_INVALID, provider, request_id=" ")
        self.assert_fails(self.errors.INPUT_INVALID, provider, today="2026-11-09")
        self.assertEqual(calls, [])

    def test_provider_receives_the_prompt_bundle_for_this_query(self):
        seen = []

        def provider(bundle):
            seen.append(bundle)
            return {}

        self.run_structure(provider, query="합성 질의")
        self.assertEqual(seen[0].query, "합성 질의")
        self.assertIn("합성 질의", seen[0].user)

    def test_today_defaults_to_the_system_date(self):
        result = self.structure.structure_query("합성 질의", request_id="rid", draft_provider=lambda b: {})
        season = result.trend_query.trend_terms.season
        self.assertEqual(season.origin, "default_next")
        self.assertGreaterEqual(season.year, date.today().year)


if __name__ == "__main__":
    unittest.main()
