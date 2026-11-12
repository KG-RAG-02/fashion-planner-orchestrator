import json
import io
import os
from pathlib import Path
import tempfile
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
        # Legacy /api/run saves UI history; keep it out of the repository's real .local/ directory.
        with tempfile.TemporaryDirectory() as tmp, \
                patch("vogue_orchestrator.server.LATEST_RUN_PATH", Path(tmp) / "latest-run.json"), \
                patch("vogue_orchestrator.server.RUN_HISTORY_DIR", Path(tmp) / "runs"), \
                patch("vogue_orchestrator.server.build_demo_orchestrator") as build:
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

    def test_agent_http_creates_local_p08_draft_before_single_p07_review(self):
        from vogue_orchestrator import Orchestrator

        seen = {}

        def envelope(agent_id, version, body, contract="vogue-handoff/0.1"):
            return {"contract_version": contract, "request_id": body["request_id"],
                    "producer": {"agent_id": agent_id, "schema_version": version},
                    "status": body["status"], "result": body}

        def trend(payload):
            rid = payload["request_id"]
            return envelope("trend-analysis-agent", "1.1", {
                "request_id": rid, "schema_version": "1.1", "status": "ok",
                "trends": [], "evidence": [], "limitations": [],
            })

        def stock(payload):
            rid = payload["request_id"]
            return envelope("stock-agent", "1.0", {
                "request_id": rid, "schema_version": "1.0", "agent_id": "stock-agent",
                "status": "ok", "products": [], "trend_results": [], "evidence": [],
                "limitations": [], "errors": [],
            })

        planning_body = {
            "request_id": "", "schema_version": "1.0", "status": "partial",
            "draft": {"category": "상의", "season": "2027SS", "attributes": []},
            "image": {"status": "blocked", "image_url": None},
            "planning_board": {"status": "blocked", "image_url": None},
        }

        def unexpected_project07(_payload):
            raise AssertionError("P07 must run only after P06 creates the draft")

        runner = Orchestrator(trend=trend, stock=stock, project_07=unexpected_project07,
                              project_06=unexpected_project07, require_envelopes=True)
        review = {"contract_version": "vogue-sales-market-review/0.1-proposal",
                  "request_id": "", "producer": {"agent_id": "sales-market-agent",
                  "schema_version": "sales-market-review/0.1-internal"},
                  "status": "no_results", "result": {"request_id": "", "status": "no_results",
                  "producer_id": "sales-market-agent", "schema_version": "sales-market-review/0.1-internal"}}

        def make_planning(request_id, query, scope, trend_query, trend_result, stock_result):
            seen["upstream"] = (trend_result, stock_result)
            planning_body["request_id"] = request_id
            return planning_body

        def post_review(request_id, draft):
            seen["draft"] = draft
            review["request_id"] = request_id
            review["result"]["request_id"] = request_id
            return review

        body = {"mode": "agent_http_v01", "query": "2027년 봄 발레코어에서 유행하는 핑크색 상의를 기획하고 싶어",
                "structure": True}
        with patch.dict(os.environ, {"VOGUE_QUERY_STRUCTURING_MODE": "synthetic_fixture"}), \
             patch("vogue_orchestrator.server.build_v01_http_orchestrator", return_value=runner), \
             patch("vogue_orchestrator.server.build_migrated_planning_draft", side_effect=make_planning), \
             patch("vogue_orchestrator.server.request_sales_market_review", side_effect=post_review):
            status, result = self.post("/api/run", body)

        self.assertEqual(status, 200)
        self.assertEqual([stage["stage"] for stage in result["stages"]],
                         ["trend", "stock", "planning_draft", "project_07"])
        self.assertEqual(seen["draft"], {
            "schema_version": "1.0", "draft_id": result["request_id"],
            "product": {"category": "상의", "season": "2027SS"},
        })
        self.assertEqual(result["sales_market_review"], review)
        self.assertEqual(result["image_generation"]["status"], "blocked")

    def test_legacy_api_run_remains_available(self):
        status, result = self.post("/api/run", {"query": "synthetic legacy"})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "partial")
        final_stage = next(stage for stage in result["stages"] if stage["stage"] == "project_06")
        self.assertEqual(final_stage["raw_response"]["request_id"], result["request_id"])
        self.assertTrue(final_stage["raw_response"]["draft"]["title"])
        self.assertNotIn("planning_draft", result)

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
