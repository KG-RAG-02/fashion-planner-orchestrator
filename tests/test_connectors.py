from pathlib import Path
import unittest
from unittest.mock import patch

from vogue_orchestrator.connectors import (
    ConnectorConfigurationError, _project07_context_fields, _service_url,
    connection_status, proposal_http_adapter,
)


class ConnectorConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.project_07_root = Path(__file__).resolve().parents[2] / "project_07"
        self.base_env = {
            "VOGUE_TREND_ADAPTER": "trend_adapter:run",
            "VOGUE_STOCK_AGENT_URL": "http://127.0.0.1:8002",
            "VOGUE_PROJECT07_ROOT": str(self.project_07_root),
            "VOGUE_PROJECT06_URL": "http://127.0.0.1:8766",
        }

    def test_project07_source_factory_is_required_for_ready_status(self):
        status = connection_status(self.base_env)
        self.assertFalse(status["ready"])
        p07 = next(item for item in status["connectors"] if item["id"] == "project_07")
        self.assertFalse(p07["configured"])
        self.assertFalse(p07["source_configured"])

        status = connection_status({**self.base_env, "VOGUE_PROJECT07_SOURCE_ADAPTER": "sources:build"})
        self.assertTrue(status["configuration_complete"])
        self.assertFalse(status["ready"])
        self.assertTrue(any("Project_06" in blocker for blocker in status["known_blockers"]))
        self.assertTrue(any("Trend runner" in blocker for blocker in status["known_blockers"]))
        self.assertEqual(status["endpoint_health"], "not_checked")
        self.assertEqual(status["contract_compatibility"], "not_verified")
        self.assertEqual(status["source_permission"], "not_verified")
        self.assertEqual(status["operational_approval"], "not_verified")

    def test_project07_checkout_must_contain_the_importable_package(self):
        env = {
            **self.base_env,
            "VOGUE_PROJECT07_ROOT": "/tmp/vogue-project-07-missing",
            "VOGUE_PROJECT07_SOURCE_ADAPTER": "sources:build",
        }
        status = connection_status(env)
        self.assertFalse(status["ready"])
        p07 = next(item for item in status["connectors"] if item["id"] == "project_07")
        self.assertFalse(p07["configured"])

    def test_project07_scope_does_not_silently_inherit_geography_or_channel_defaults(self):
        mapped = _project07_context_fields({
            "query": "synthetic query mentions no scope",
            "context": {"category": {"value": "synthetic category"}},
        })
        self.assertEqual(mapped["requested_category"], "synthetic category")
        self.assertIsNone(mapped["requested_geography"])
        self.assertIsNone(mapped["requested_channel"])
        self.assertIsNone(mapped["requested_period"])
        self.assertEqual(mapped["requested_query"], "synthetic query mentions no scope")

    def test_project07_scope_mapper_does_not_parse_ambiguous_or_multiple_values(self):
        mapped = _project07_context_fields({
            "query": "synthetic query: KR or JP, online and store, maybe spring",
            "context": {},
        })
        self.assertIsNone(mapped["requested_geography"])
        self.assertIsNone(mapped["requested_channel"])
        self.assertIsNone(mapped["requested_period"])

    def test_project07_scope_mapper_passes_only_single_explicit_market_and_channel(self):
        mapped = _project07_context_fields({"query": "synthetic", "context": {
            "market": {"value": "KR", "origin": "user_requested"},
            "channel": [{"value": "online", "origin": "user_requested"}],
        }})
        self.assertEqual(mapped["requested_geography"], "KR")
        self.assertEqual(mapped["requested_channel"], "online")
        multiple = _project07_context_fields({"query": "synthetic", "context": {
            "market": [{"value": "KR"}, {"value": "JP"}],
        }})
        self.assertIsNone(multiple["requested_geography"])

    def test_http_is_restricted_to_loopback(self):
        self.assertEqual(
            _service_url({"ENDPOINT": "http://localhost:8766"}, "ENDPOINT", "/api/planning-draft"),
            "http://localhost:8766/api/planning-draft",
        )
        with self.assertRaises(ConnectorConfigurationError):
            _service_url({"ENDPOINT": "http://example.com"}, "ENDPOINT", "/api/planning-draft")

    def test_proposal_http_adapter_sends_shared_envelope_to_runs_endpoint(self):
        sent = {}

        def fake_post(url, payload):
            sent["url"] = url
            sent["payload"] = payload
            return {"synthetic": True}

        with patch("vogue_orchestrator.connectors._post_json", side_effect=fake_post):
            adapter = proposal_http_adapter("http://localhost:8000", "trend-analysis-agent")
            response = adapter({"request_id": "req-synthetic", "query": "synthetic"})

        self.assertEqual(response, {"synthetic": True})
        self.assertEqual(sent["url"], "http://localhost:8000/v1/agents/trend-analysis-agent/runs")
        self.assertEqual(sent["payload"], {
            "contract_version": "vogue-handoff/0.1-proposal",
            "request_id": "req-synthetic",
            "input": {"request_id": "req-synthetic", "query": "synthetic"},
        })


if __name__ == "__main__":
    unittest.main()
