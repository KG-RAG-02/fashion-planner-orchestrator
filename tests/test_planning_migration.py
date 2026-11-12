import os
import unittest
from unittest.mock import patch

from vogue_orchestrator.planning_migration import build_migrated_planning_draft


class PlanningGeneratorMigrationTests(unittest.TestCase):
    def test_migrated_generator_keeps_source_payload_and_reports_source_schema(self):
        request_id = "migration-test-001"
        trend = {"schema_version": "1.1", "request_id": request_id, "status": "ok",
                 "trends": [], "evidence": [], "limitations": []}
        stock = {"schema_version": "1.0", "request_id": request_id, "agent_id": "stock-agent",
                 "status": "ok", "products": [], "trend_results": [], "evidence": [],
                 "limitations": [], "errors": []}
        with patch.dict(os.environ, {"VOGUE_SYNTHETIC_PLANNING_RAG_ENABLED": "false",
                                    "VOGUE_IMAGE_GENERATION_ENABLED": "false"}):
            result = build_migrated_planning_draft(
                request_id, "2027 SS floral dress", {}, None, trend, stock,
            )
        self.assertEqual(result["request_id"], request_id)
        self.assertIn("draft", result)
        self.assertEqual(result["input_evidence"]["trend_result"]["source_schema_version"], "1.1")
        self.assertEqual(result["input_evidence"]["trend_result"]["payload"], trend)
        self.assertEqual(result["compatibility"]["input_schemas"]["sales_market_result"]["status"], "not_provided")

    def test_only_explicit_structured_season_and_category_enter_request_context(self):
        request_id = "migration-test-002"
        trend = {"schema_version": "1.1", "request_id": request_id, "status": "ok",
                 "trends": [], "evidence": [], "limitations": []}
        stock = {"schema_version": "1.0", "request_id": request_id, "agent_id": "stock-agent",
                 "status": "ok", "products": [], "trend_results": [], "evidence": [],
                 "limitations": [], "errors": []}
        trend_query = {"trend_terms": {"season": {"year": 2027, "season": "SS", "origin": "user_requested"}},
                       "requested": {"categories": {"include": [{"minor": "원피스", "origin": "user_requested"}]}}}
        with patch("vogue_orchestrator.planning_migration.build_planning_draft_response") as generate:
            generate.return_value = {"input_evidence": {"trend_result": {}}}
            build_migrated_planning_draft(request_id, "query", {}, trend_query, trend, stock)
        request = generate.call_args.args[0]["planning_request"]
        self.assertEqual(request["context"]["season"], {"value": "2027SS", "source": "user"})
        self.assertEqual(request["context"]["category"], {"value": "원피스", "source": "user"})


if __name__ == "__main__":
    unittest.main()
