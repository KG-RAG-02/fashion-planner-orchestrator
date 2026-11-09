from pathlib import Path
import unittest

from vogue_orchestrator.connectors import ConnectorConfigurationError, _service_url, connection_status


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
        self.assertTrue(status["ready"])

    def test_http_is_restricted_to_loopback(self):
        self.assertEqual(
            _service_url({"ENDPOINT": "http://localhost:8766"}, "ENDPOINT", "/api/planning-draft"),
            "http://localhost:8766/api/planning-draft",
        )
        with self.assertRaises(ConnectorConfigurationError):
            _service_url({"ENDPOINT": "http://example.com"}, "ENDPOINT", "/api/planning-draft")


if __name__ == "__main__":
    unittest.main()
