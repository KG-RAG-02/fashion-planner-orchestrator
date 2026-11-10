import json
import io
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from vogue_orchestrator.server import OrchestratorHandler


class FakeSocket:
    def __init__(self):
        self.headers = []
        self.status = None
        self.body = b""

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers.append((key, value))

    def end_headers(self):
        pass

    def write(self, value):
        self.body += value


class PublicOrchestrationApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pass

    @classmethod
    def tearDownClass(cls):
        pass

    def post(self, path, body):
        handler = object.__new__(OrchestratorHandler)
        handler.path = path
        handler.headers = {"Content-Length": str(len(json.dumps(body).encode()))}
        handler.rfile = io.BytesIO(json.dumps(body).encode())
        handler.wfile = FakeSocket()
        handler._send_response = handler.wfile
        def send_response(status): handler.wfile.send_response(status)
        def send_header(key, value): handler.wfile.send_header(key, value)
        handler.send_response = send_response
        handler.send_header = send_header
        handler.end_headers = lambda: None
        with patch("vogue_orchestrator.server.build_demo_orchestrator") as build:
            from vogue_orchestrator.demo import build_demo_orchestrator
            build.side_effect = build_demo_orchestrator
            handler.do_POST()
        return handler.wfile.status, json.loads(handler.wfile.body)

    def test_orchestration_endpoint_accepts_request_and_returns_ordered_trace(self):
        status, result = self.post("/v1/orchestrations", {
            "request_id": "api-synthetic-001",
            "input": {"original_query": "synthetic 27SS outerwear", "scope": {
                "season": {"value": "27SS", "origin": "user_requested"},
                "category": "outerwear", "market": ["KR", "JP"], "channel": "online",
            }},
        })
        self.assertEqual(status, 200)
        self.assertEqual(result["request_id"], "api-synthetic-001")
        self.assertEqual([stage["stage"] for stage in result["stages"]],
                         ["trend", "stock", "project_07", "project_06"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["run_id"]), 64)
        self.assertEqual(result["input"]["scope"]["market"][1]["value"], "JP")
        self.assertIsNotNone(result["final_result"])

    def test_orchestration_endpoint_generates_request_id_when_omitted(self):
        status, result = self.post("/v1/orchestrations", {
            "input": {"original_query": "synthetic request"},
        })
        self.assertEqual(status, 200)
        self.assertTrue(result["request_id"])

    def test_orchestration_endpoint_rejects_unapproved_or_malformed_scope_fields(self):
        status, result = self.post("/v1/orchestrations", {
            "input": {"original_query": "synthetic", "scope": {"price": "100"}},
        })
        self.assertEqual(status, 400)
        self.assertEqual(result["error"], "invalid_request")

    def test_agent_v01_ui_mode_requires_query_structuring_opt_in(self):
        status, result = self.post("/api/run", {"query": "synthetic", "mode": "agent_http_v01"})
        self.assertEqual(status, 400)
        self.assertEqual(result["error"], "invalid_request")

    def test_legacy_api_run_remains_available(self):
        status, result = self.post("/api/run", {"query": "synthetic legacy"})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "partial")

    def test_public_agent_http_can_structure_fixture_query_into_trend_stage_input(self):
        from vogue_orchestrator.demo import build_demo_orchestrator

        captured = {}

        class CaptureOrchestrator:
            def run(self, query, **kwargs):
                captured.update(kwargs)
                return build_demo_orchestrator().run(query, **kwargs)

        body = {
            "request_id": "synthetic-query-001",
            "mode": "agent_http_v01",
            "input": {
                "original_query": "2027년 봄 발레코어에서 유행하는 핑크색 상의를 기획하고 싶어",
                "structure_query": True,
            },
        }
        with patch.dict(os.environ, {"VOGUE_QUERY_STRUCTURING_MODE": "synthetic_fixture"}), \
             patch("vogue_orchestrator.server.build_v01_http_orchestrator", return_value=CaptureOrchestrator()):
            status, result = self.post("/v1/orchestrations", body)
        self.assertEqual(status, 200)
        self.assertEqual(captured["trend_query"]["request_id"], body["request_id"])
        self.assertEqual(captured["trend_query"]["original_query"], body["input"]["original_query"])
        self.assertTrue(result["query_structuring"]["trend_input_preview"]["sent"])

    def test_public_query_structuring_failure_stops_before_pipeline(self):
        called = []

        class CaptureOrchestrator:
            def run(self, query, **kwargs):
                called.append(kwargs)
                raise AssertionError("pipeline must not run when structuring fails")

        body = {
            "request_id": "synthetic-query-invalid",
            "mode": "agent_http_v01",
            "input": {"original_query": "unknown synthetic query", "structure_query": True},
        }
        with patch.dict(os.environ, {"VOGUE_QUERY_STRUCTURING_MODE": "synthetic_fixture"}), \
             patch("vogue_orchestrator.server.build_v01_http_orchestrator", return_value=CaptureOrchestrator()):
            status, result = self.post("/v1/orchestrations", body)
        self.assertEqual(status, 422)
        self.assertEqual(result["error"], "query_structuring_failed")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
