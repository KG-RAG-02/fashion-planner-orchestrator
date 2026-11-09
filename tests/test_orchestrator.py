import unittest
import json
from pathlib import Path

from vogue_orchestrator import Orchestrator, StageName
from vogue_orchestrator.core import (
    CONTRACT_VERSION, ContractError, project_trend_result_for_p07,
    unwrap_p07_for_project06,
)


RID = "synthetic-req-001"
FIXTURES = Path(__file__).resolve().parents[1] / "examples" / "fixtures"


def trend_adapter(payload):
    return json.loads((FIXTURES / "trend-analysis-result.no-results.synthetic.json").read_text())


def stock_adapter(payload):
    return json.loads((FIXTURES / "stock-agent-result.no-results.synthetic.json").read_text())


def p07_adapter(payload):
    rid = payload["request_id"]
    return {"schema_version": "1.3-proposal", "request_id": rid,
            "agent_id": "market-research-agent", "status": "no_results",
            "summary": "synthetic only", "findings": [], "evidence": [],
            "sales_data": {"status": "no_results", "observations": [], "limitations": [],
                           "errors": [], "missing_data": []}}


def make_runner(p07=p07_adapter, p06=None, stock=stock_adapter):
    seen = {"p07": None}

    def market(payload):
        seen["p07"] = payload
        return p07(payload)

    def planning(payload):
        seen["p06"] = payload
        return {"request_id": payload["request_id"], "status": "partial", "draft": {}}

    runner = Orchestrator(trend=trend_adapter, stock=stock, project_07=market,
                          project_06=p06 or planning, request_id_factory=lambda: RID)
    return runner, seen


class OrchestratorTests(unittest.TestCase):
    def test_sequential_flow_preserves_request_and_raw_results(self):
        runner, seen = make_runner()
        result = runner.run("합성 쿼리")
        self.assertEqual([s.stage for s in result.stages], list(StageName))
        self.assertEqual({s.request_id for s in result.stages}, {RID})
        self.assertEqual(result.stages[0].raw_response["status"], "no_results")
        self.assertIsNone(result.stages[0].producer_schema_version)
        self.assertEqual(seen["p07"]["trend_result"]["schema_version"], "1.0")
        self.assertEqual(seen["p06"]["sales_market_result"]["schema_version"], "1.3-proposal")
        self.assertEqual(result.status, "partial")

    def test_p07_projection_preserves_producer_raw_and_rejects_unknown_version(self):
        raw = trend_adapter({"request_id": RID})
        projected = project_trend_result_for_p07(raw, RID)
        self.assertNotIn("schema_version", raw)
        self.assertEqual(projected["schema_version"], "1.0")
        with self.assertRaises(ContractError):
            project_trend_result_for_p07({**raw, "schema_version": "2.0"}, RID)

    def test_mismatched_request_stops_without_losing_prior_stage(self):
        def broken_stock(payload):
            value = stock_adapter(payload)
            return {**value, "request_id": "wrong-id"}

        runner, _ = make_runner(stock=broken_stock)
        result = runner.run("합성 쿼리", request_id=RID)
        self.assertEqual(result.stopped_at, StageName.STOCK)
        self.assertEqual(len(result.stages), 2)
        self.assertEqual(result.stages[0].status, "no_results")
        self.assertEqual(result.stages[1].error_code, "CONTRACT_ERROR")

    def test_wrong_stock_producer_is_rejected(self):
        def wrong_producer(payload):
            return {**stock_adapter(payload), "agent_id": "unexpected-agent"}

        runner, _ = make_runner(stock=wrong_producer)
        result = runner.run("synthetic query")
        self.assertEqual(result.stopped_at, StageName.STOCK)
        self.assertEqual(result.stages[-1].error_code, "CONTRACT_ERROR")

    def test_unexpected_exception_does_not_echo_query_or_exception_text(self):
        secret_like_text = "synthetic-private-query-marker"

        def raises(payload):
            raise RuntimeError(secret_like_text)

        runner = Orchestrator(trend=raises, stock=stock_adapter, project_07=p07_adapter,
                              project_06=lambda payload: {})
        result = runner.run(secret_like_text, request_id=RID).to_dict()
        self.assertNotIn(secret_like_text, json.dumps(result))
        self.assertEqual(result["stages"][0]["error_message"], "Stage adapter failed; details suppressed.")

    def test_proposal_envelope_unwraps_only_after_consistency_checks(self):
        body = p07_adapter({"request_id": RID})
        envelope = {"contract_version": CONTRACT_VERSION, "request_id": RID,
                    "producer": {"agent_id": body["agent_id"], "schema_version": body["schema_version"]},
                    "status": body["status"], "result": body}
        self.assertEqual(unwrap_p07_for_project06(envelope, RID), body)
        bad = {**envelope, "status": "ok"}
        with self.assertRaises(ContractError):
            unwrap_p07_for_project06(bad, RID)

    def test_bad_p07_envelope_is_rejected_and_project06_is_not_called(self):
        body = p07_adapter({"request_id": RID})
        bad_envelope = {"contract_version": CONTRACT_VERSION, "request_id": "other-id",
                        "producer": {"agent_id": body["agent_id"], "schema_version": body["schema_version"]},
                        "status": body["status"], "result": body}
        called = []
        runner, _ = make_runner(p07=lambda payload: bad_envelope,
                                p06=lambda payload: called.append(payload) or {})
        result = runner.run("합성 쿼리")
        self.assertEqual(result.stopped_at, StageName.PROJECT_07)
        self.assertEqual(result.stages[-1].error_code, "CONTRACT_ERROR")
        self.assertFalse(called)

    def test_p07_error_preserves_result_and_skips_project06(self):
        def failed(payload):
            return {"request_id": payload["request_id"], "agent_id": "market-research-agent",
                    "schema_version": "1.3-proposal", "status": "error", "errors": [{"code": "SYNTHETIC"}]}

        called = []
        runner, _ = make_runner(p07=failed, p06=lambda payload: called.append(payload) or {})
        result = runner.run("합성 쿼리")
        self.assertEqual(result.stopped_at, StageName.PROJECT_07)
        self.assertEqual(result.stages[-1].raw_response["errors"][0]["code"], "SYNTHETIC")
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
