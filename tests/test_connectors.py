from pathlib import Path
import unittest
from unittest.mock import patch

from vogue_orchestrator.connectors import (
    ConnectorConfigurationError, _project07_context_fields, _service_url,
    connection_status, agent_http_v01_adapter, build_v01_http_orchestrator,
    HTTP_CONNECT_TIMEOUT_SECONDS, AGENT_REQUEST_TIMEOUT_SECONDS, _post_json, TransportError,
)


class ConnectorConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.project_07_root = Path(__file__).resolve().parents[2] / "project_07"
        self.base_env = {
            "VOGUE_TREND_ADAPTER": "trend_adapter:run",
            "VOGUE_STOCK_AGENT_URL": "http://127.0.0.1:8002",
            "VOGUE_PROJECT07_ROOT": str(self.project_07_root),
            "VOGUE_PROJECT06_URL": "http://127.0.0.1:8767",
        }

    def test_project07_builtin_source_is_available_without_factory(self):
        status = connection_status(self.base_env)
        self.assertFalse(status["ready"])
        p07 = next(item for item in status["connectors"] if item["id"] == "project_07")
        self.assertTrue(p07["configured"])
        self.assertTrue(p07["source_configured"])

        status = connection_status({**self.base_env, "VOGUE_PROJECT07_SOURCE_ADAPTER": "sources:build"})
        self.assertTrue(status["configuration_complete"])
        self.assertFalse(status["ready"])
        self.assertTrue(any("Trend local HTTP" in blocker for blocker in status["known_blockers"]))
        self.assertTrue(any("Stock" in blocker for blocker in status["known_blockers"]))
        self.assertEqual(status["endpoint_health"], "not_checked")
        self.assertEqual(status["contract_compatibility"], "not_verified")
        self.assertEqual(status["source_permission"], "owner_asserted")
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
            _service_url({"ENDPOINT": "http://localhost:8766", "VOGUE_AGENT_HTTP_DEV_MODE": "1"},
                         "ENDPOINT", "/api/planning-draft"),
            "http://localhost:8766/api/planning-draft",
        )
        with self.assertRaises(ConnectorConfigurationError):
            _service_url({"ENDPOINT": "http://example.com"}, "ENDPOINT", "/api/planning-draft")
        with self.assertRaises(ConnectorConfigurationError):
            _service_url({"ENDPOINT": "http://localhost:8766"}, "ENDPOINT", "/api/planning-draft")

    def test_proposal_http_adapter_sends_shared_envelope_to_runs_endpoint(self):
        sent = {}

        def fake_post(url, payload, *, bearer_token=None):
            sent["url"] = url
            sent["payload"] = payload
            sent["bearer_token"] = bearer_token
            return {"synthetic": True}

        with patch("vogue_orchestrator.connectors._post_json", side_effect=fake_post):
            adapter = agent_http_v01_adapter("http://localhost:8000", "stock-agent",
                                             bearer_token="synthetic-token", dev_mode=True)
            response = adapter({"request_id": "req-synthetic", "trend_result": {"x": 1}})

        self.assertEqual(response, {"synthetic": True})
        self.assertEqual(sent["url"], "http://localhost:8000/v1/agents/stock-agent/runs")
        self.assertEqual(sent["payload"], {
            "contract_version": "vogue-handoff/0.1",
            "request_id": "req-synthetic",
            "input": {"request_id": "req-synthetic", "trend_result": {"x": 1}},
        })
        self.assertEqual(sent["bearer_token"], "synthetic-token")

    def test_trend_http_adapter_requires_explicit_structured_trend_query(self):
        adapter = agent_http_v01_adapter("http://localhost:8000", "trend-analysis-agent", dev_mode=True)
        with self.assertRaisesRegex(ConnectorConfigurationError, "TrendQuery is required"):
            adapter({"request_id": "req-synthetic", "query": "raw query"})

    def test_trend_http_adapter_sends_direct_orchestrator_trend_query(self):
        sent = {}

        def fake_post(url, payload, *, bearer_token=None):
            sent["url"] = url
            sent["payload"] = payload
            return {"synthetic": True}

        trend_query = {"request_id": "req-synthetic", "original_query": "synthetic", "schema_version": "1.0"}
        with patch("vogue_orchestrator.connectors._post_json", side_effect=fake_post):
            adapter = agent_http_v01_adapter("http://localhost:8000", "trend-analysis-agent", dev_mode=True)
            adapter(trend_query)

        self.assertEqual(sent["url"], "http://localhost:8000/v1/agents/trend-analysis-agent/runs")
        self.assertEqual(sent["payload"]["input"], trend_query)

    def test_http_transport_applies_bearer_timeouts_and_makes_one_request(self):
        class FakeSocket:
            timeout = None
            def settimeout(self, value):
                self.timeout = value

        class FakeResponse:
            status = 200
            def read(self):
                return b'{"ok": true}'

        class FakeConnection:
            def __init__(self):
                self.sock = FakeSocket()
                self.requests = []
                self.closed = False
            def connect(self):
                pass
            def request(self, method, path, *, body, headers):
                self.requests.append((method, path, body, headers))
            def getresponse(self):
                return FakeResponse()
            def close(self):
                self.closed = True

        connection = FakeConnection()
        with patch("vogue_orchestrator.connectors.http.client.HTTPConnection", return_value=connection) as factory:
            result = _post_json("http://127.0.0.1:8080/run", {"synthetic": True}, bearer_token="test-token")

        factory.assert_called_once_with("127.0.0.1", 8080, timeout=HTTP_CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(connection.sock.timeout, AGENT_REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(len(connection.requests), 1)
        self.assertEqual(connection.requests[0][0:2], ("POST", "/run"))
        self.assertEqual(connection.requests[0][3]["Authorization"], "Bearer test-token")
        self.assertEqual(result, {"ok": True})
        self.assertTrue(connection.closed)

    def test_http_transport_timeout_and_invalid_json_remain_transport_errors(self):
        class TimeoutConnection:
            sock = None
            def connect(self):
                raise TimeoutError("synthetic timeout")
            def close(self):
                self.closed = True

        timeout_connection = TimeoutConnection()
        with patch("vogue_orchestrator.connectors.http.client.HTTPConnection", return_value=timeout_connection):
            with self.assertRaises(TransportError):
                _post_json("http://127.0.0.1:8080/run", {"synthetic": True})
        self.assertTrue(timeout_connection.closed)

        class InvalidJsonResponse:
            status = 200
            def read(self):
                return b"not-json"

        class InvalidJsonConnection:
            sock = None
            def connect(self):
                pass
            def request(self, *args, **kwargs):
                pass
            def getresponse(self):
                return InvalidJsonResponse()
            def close(self):
                pass

        with patch("vogue_orchestrator.connectors.http.client.HTTPConnection", return_value=InvalidJsonConnection()):
            with self.assertRaises(TransportError):
                _post_json("http://127.0.0.1:8080/run", {"synthetic": True})

    def test_project06_http_adapter_keeps_request_id_only_in_outer_envelope(self):
        sent = {}

        def fake_post(url, payload, *, bearer_token=None):
            sent["url"] = url
            sent["payload"] = payload
            return {"synthetic": True}

        stage_input = {
            "request_id": "req-synthetic", "planning_request": {"schema_version": "1.0"},
            "trend_result": {"schema_version": "1.1"},
            "inventory_product_result": {"schema_version": "1.0"},
            "sales_market_result": {"schema_version": "1.0"},
        }
        with patch("vogue_orchestrator.connectors._post_json", side_effect=fake_post):
            adapter = agent_http_v01_adapter("http://localhost:8000", "new-product-agent", dev_mode=True)
            adapter(stage_input)

        self.assertEqual(sent["url"], "http://localhost:8000/v1/agents/new-product-agent/runs")
        self.assertEqual(sent["payload"]["request_id"], "req-synthetic")
        self.assertEqual(set(sent["payload"]["input"]), {
            "planning_request", "trend_result", "inventory_product_result", "sales_market_result",
        })

    def test_v01_builder_uses_new_explicit_environment_names_and_no_retries(self):
        env = {
            "VOGUE_TREND_AGENT_URL": "http://localhost:8001",
            "VOGUE_STOCK_AGENT_URL": "http://localhost:8002",
            "VOGUE_SALES_MARKET_AGENT_URL": "http://localhost:8003",
            "VOGUE_NEW_PRODUCT_AGENT_URL": "http://localhost:8004",
            "VOGUE_AGENT_HTTP_DEV_MODE": "1",
        }
        runner = build_v01_http_orchestrator(env)
        self.assertTrue(runner.require_envelopes)
        self.assertEqual(HTTP_CONNECT_TIMEOUT_SECONDS, 3)
        self.assertEqual(AGENT_REQUEST_TIMEOUT_SECONDS, 60)

    def test_readiness_separates_mixed_connectors_from_http_v01(self):
        status = connection_status({**self.base_env, "VOGUE_PROJECT07_SOURCE_ADAPTER": "sources:build"})
        self.assertTrue(status["connection_modes"]["mixed_connectors"]["configured"])
        canonical = status["connection_modes"]["agent_http_v01"]
        self.assertFalse(canonical["ready"])
        self.assertEqual(canonical["authentication"], "missing_service_bearer_token")
        self.assertTrue(all(route["endpoint_health"] == "not_checked" for route in canonical["routes"]))
        by_route = {route["route_agent_id"]: route for route in canonical["routes"]}
        self.assertEqual(by_route["trend-analysis-agent"]["known_status"], "local_development_route")
        self.assertTrue(by_route["trend-analysis-agent"]["implemented"])
        self.assertEqual(by_route["trend-analysis-agent"]["local_development_base_url"], "http://127.0.0.1:8768")
        self.assertEqual(by_route["stock-agent"]["known_status"], "source_adapter_verified")
        self.assertTrue(by_route["stock-agent"]["implemented"])
        self.assertEqual(by_route["sales-market-agent"]["known_status"], "runtime_schema_aligned")
        self.assertEqual(by_route["sales-market-agent"]["producer_result_schema_version"], "1.0")
        self.assertEqual(by_route["sales-market-agent"]["local_development_base_url"], "http://127.0.0.1:8766")
        self.assertEqual(by_route["new-product-agent"]["producer_result_schema_version"], "1.0")
        self.assertEqual(by_route["new-product-agent"]["local_development_base_url"], "http://127.0.0.1:8767")


if __name__ == "__main__":
    unittest.main()
