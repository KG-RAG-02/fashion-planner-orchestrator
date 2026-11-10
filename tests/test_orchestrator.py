import unittest
import json
from pathlib import Path

from vogue_orchestrator import Orchestrator, StageName
from vogue_orchestrator.core import (
    CONTRACT_VERSION, ContractError, prepare_trend_result_for_p07,
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
    def test_http_envelopes_are_validated_recorded_and_unwrapped_for_handoffs(self):
        seen = {}

        def envelope(agent_id, version, body):
            return {"contract_version": CONTRACT_VERSION, "request_id": RID,
                    "producer": {"agent_id": agent_id, "schema_version": version},
                    "status": body["status"], "result": body}

        trend_body = {"request_id": RID, "status": "no_results", "trends": [],
                      "evidence": [], "limitations": ["synthetic"]}
        stock_body = {"schema_version": "1.0", "request_id": RID, "agent_id": "stock-agent",
                      "status": "no_results", "products": [], "trend_results": [],
                      "evidence": [], "errors": []}
        p07_body = {"schema_version": "1.3-proposal", "request_id": RID,
                    "agent_id": "market-research-agent", "status": "partial",
                    "findings": [], "evidence": [], "limitations": ["synthetic"], "errors": []}

        def trend(payload):
            return envelope("trend-analysis-agent", "1.0", trend_body)

        def stock(payload):
            seen["stock_input"] = payload
            return envelope("stock-agent", "1.0", stock_body)

        def p07(payload):
            seen["p07_input"] = payload
            return envelope("market-research-agent", "1.3-proposal", p07_body)

        def p06(payload):
            seen["p06_input"] = payload
            body = {"request_id": RID, "status": "partial", "draft": {}}
            return envelope("new-product-agent", "1.0", body)

        result = Orchestrator(trend=trend, stock=stock, project_07=p07, project_06=p06).run(
            "synthetic query", request_id=RID,
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.stages[0].raw_response["result"], trend_body)
        self.assertEqual(result.stages[0].producer_schema_version, "1.0")
        self.assertEqual(seen["stock_input"], trend_body)
        self.assertEqual(seen["p07_input"]["inventory_product_result"], stock_body)
        self.assertEqual(seen["p06_input"]["sales_market_result"], p07_body)
        self.assertEqual(result.stages[-1].raw_response["result"]["draft"], {})

    def test_http_envelope_request_id_mismatch_is_rejected_and_preserved(self):
        def bad_stock(payload):
            body = stock_adapter(payload)
            return {"contract_version": CONTRACT_VERSION, "request_id": "different",
                    "producer": {"agent_id": "stock-agent", "schema_version": "1.0"},
                    "status": body["status"], "result": body}

        runner, _ = make_runner(stock=bad_stock)
        result = runner.run("synthetic query", request_id=RID)
        self.assertEqual(result.stopped_at, StageName.STOCK)
        self.assertEqual(result.stages[-1].raw_response["request_id"], "different")
        self.assertEqual(result.stages[-1].error_code, "CONTRACT_ERROR")

    def test_opt_in_continuation_delivers_domain_error_inputs_without_relabeling(self):
        delivered = {}

        def trend(payload):
            return {"schema_version": "1.0", "request_id": RID, "status": "error",
                    "trends": [], "evidence": [], "limitations": [], "errors": [{"code": "TREND_SYNTHETIC"}]}

        def stock(payload):
            return {"schema_version": "1.0", "request_id": RID, "agent_id": "stock-agent",
                    "status": "error", "products": [], "trend_results": [], "evidence": [],
                    "errors": [{"code": "STOCK_SYNTHETIC"}]}

        def p07(payload):
            return {"schema_version": "1.3-proposal", "request_id": RID,
                    "agent_id": "market-research-agent", "status": "error",
                    "findings": [], "evidence": [], "limitations": [],
                    "errors": [{"code": "MARKET_SYNTHETIC"}]}

        def p06(payload):
            delivered.update(payload)
            return {"request_id": RID, "status": "partial", "draft": {"review_status": "md_review_required"}}

        result = Orchestrator(trend=trend, stock=stock, project_07=p07, project_06=p06,
                              continue_after_error=True).run("synthetic query", request_id=RID)
        self.assertIsNone(result.stopped_at)
        self.assertEqual([record.status for record in result.stages], ["error", "error", "error", "partial"])
        self.assertEqual(result.status, "error")
        self.assertEqual(delivered["trend_result"]["status"], "error")
        self.assertEqual(delivered["inventory_product_result"]["errors"][0]["code"], "STOCK_SYNTHETIC")
        self.assertEqual(delivered["sales_market_result"]["errors"][0]["code"], "MARKET_SYNTHETIC")

    def test_sequential_flow_preserves_request_and_raw_results(self):
        runner, seen = make_runner()
        result = runner.run("합성 쿼리")
        self.assertEqual([s.stage for s in result.stages], list(StageName))
        self.assertEqual({s.request_id for s in result.stages}, {RID})
        self.assertEqual(result.stages[0].raw_response["status"], "no_results")
        self.assertIsNone(result.stages[0].producer_schema_version)
        self.assertNotIn("schema_version", seen["p07"]["trend_result"])
        self.assertEqual(seen["p06"]["sales_market_result"]["schema_version"], "1.3-proposal")
        self.assertEqual(result.status, "partial")

    def test_p07_input_preserves_actual_trend_schema_version(self):
        raw = trend_adapter({"request_id": RID})
        prepared = prepare_trend_result_for_p07(raw, RID)
        self.assertNotIn("schema_version", raw)
        self.assertNotIn("schema_version", prepared)
        current = {**raw, "schema_version": "1.1"}
        prepared_current = prepare_trend_result_for_p07(current, RID)
        self.assertEqual(prepared_current["schema_version"], "1.1")
        self.assertEqual(current["schema_version"], "1.1")
        with self.assertRaises(ContractError):
            prepare_trend_result_for_p07({**raw, "schema_version": ""}, RID)

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

    def test_ok_partial_and_no_results_complete_the_ordered_pipeline(self):
        for case, trend_status, stock_status in (
            ("ok", "ok", "ok"),
            ("partial", "ok", "partial"),
            ("no_results", "no_results", "no_results"),
        ):
            with self.subTest(case=case):
                calls = []

                def trend(payload):
                    calls.append("trend")
                    return {"request_id": payload["request_id"], "status": trend_status,
                            "applied_filters": None, "trends": [], "evidence": [],
                            "limitations": [f"{case} trend fixture"]}

                def stock(payload):
                    calls.append("stock")
                    return {"schema_version": "1.0", "request_id": payload["request_id"],
                            "agent_id": "stock-agent", "status": stock_status,
                            "products": [], "trend_results": [], "evidence": [], "errors": [],
                            "limitations": [f"{case} stock fixture"]}

                def p07(payload):
                    calls.append("project_07")
                    return {"schema_version": "1.3-proposal", "request_id": payload["request_id"],
                            "agent_id": "market-research-agent", "status": stock_status,
                            "findings": [], "evidence": [], "limitations": [f"{case} P07 fixture"],
                            "errors": []}

                def p06(payload):
                    calls.append("project_06")
                    return {"request_id": payload["request_id"], "status": "partial",
                            "draft": {"review_status": "md_review_required"},
                            "input_evidence": {"sales_market_result": payload["sales_market_result"]}}

                result = Orchestrator(
                    trend=trend, stock=stock, project_07=p07, project_06=p06,
                ).run("synthetic query", request_id=RID)
                self.assertEqual(calls, ["trend", "stock", "project_07", "project_06"])
                self.assertEqual([stage.status for stage in result.stages[:3]],
                                 [trend_status, stock_status, stock_status])
                self.assertEqual(result.stages[1].raw_response["limitations"],
                                 [f"{case} stock fixture"])
                self.assertEqual(result.stages[2].raw_response["evidence"], [])
                self.assertEqual(result.stages[3].raw_response["draft"]["review_status"],
                                 "md_review_required")

    def test_error_status_is_preserved_and_stops_later_stages(self):
        calls = []

        def stock(payload):
            calls.append("stock")
            return {"schema_version": "1.0", "request_id": payload["request_id"],
                    "agent_id": "stock-agent", "status": "error",
                    "products": [], "evidence": [], "errors": [{"code": "SYNTHETIC_ERROR"}]}

        runner, _ = make_runner(stock=stock)
        runner.adapters[StageName.PROJECT_07] = lambda payload: calls.append("project_07") or p07_adapter(payload)
        runner.adapters[StageName.PROJECT_06] = lambda payload: calls.append("project_06") or {}
        result = runner.run("synthetic query", request_id=RID)
        self.assertEqual(result.stopped_at, StageName.STOCK)
        self.assertEqual(calls, ["stock"])
        self.assertEqual(result.stages[-1].status, "error")
        self.assertEqual(result.stages[-1].raw_response["errors"][0]["code"], "SYNTHETIC_ERROR")

    def test_supplied_empty_request_id_is_rejected_without_replacement(self):
        runner, _ = make_runner()
        with self.assertRaisesRegex(ValueError, "request_id must be non-empty"):
            runner.run("synthetic query", request_id="")

    def test_trend_producer_metadata_is_checked_when_present(self):
        def unexpected_trend(payload):
            return {**trend_adapter(payload), "agent_id": "unexpected-agent"}

        runner = Orchestrator(trend=unexpected_trend, stock=stock_adapter,
                              project_07=p07_adapter, project_06=lambda payload: {})
        result = runner.run("synthetic query", request_id=RID)
        self.assertEqual(result.stopped_at, StageName.TREND)
        self.assertEqual(result.stages[-1].error_code, "CONTRACT_ERROR")

    def test_current_trend_v11_reaches_p07_but_current_p06_version_gate_stops(self):
        seen = {"p07_trend": None, "p06": False}

        def current_trend(payload):
            return {**trend_adapter(payload), "schema_version": "1.1"}

        def p07(payload):
            seen["p07_trend"] = payload["trend_result"]
            return p07_adapter(payload)

        def p06(payload):
            seen["p06"] = True
            return {"request_id": payload["request_id"], "status": "ok"}

        runner = Orchestrator(trend=current_trend, stock=stock_adapter,
                              project_07=p07, project_06=p06)
        result = runner.run("synthetic query", request_id=RID)
        self.assertEqual(result.stopped_at, StageName.PROJECT_06)
        self.assertEqual(result.stages[0].raw_response["schema_version"], "1.1")
        self.assertEqual(seen["p07_trend"]["schema_version"], "1.1")
        self.assertFalse(seen["p06"])
        self.assertEqual(result.stages[-1].error_code, "CONTRACT_ERROR")


if __name__ == "__main__":
    unittest.main()
