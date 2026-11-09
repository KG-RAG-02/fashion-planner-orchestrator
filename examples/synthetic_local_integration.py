"""Call the actual local Project_07 and Project_06 code with synthetic payloads only.

Run from project_08 with:
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 examples/synthetic_local_integration.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
sys.dont_write_bytecode = True
sys.path[:0] = [str(WORKSPACE / "project_07" / "src"), str(WORKSPACE / "project_06" / "src")]

from market_research_agent import MarketResearchAgent, MarketResearchContext  # noqa: E402
from vogue_npp.prototype.server import _build_planning_draft_response  # noqa: E402
from vogue_orchestrator import Orchestrator  # noqa: E402


def main() -> None:
    rid = "synthetic-req-001"
    trend_raw = json.loads((ROOT / "examples/fixtures/trend-analysis-result.no-results.synthetic.json").read_text())
    stock_raw = json.loads((ROOT / "examples/fixtures/stock-agent-result.no-results.synthetic.json").read_text())

    def trend_adapter(payload):
        return {**trend_raw, "request_id": payload["request_id"]}

    def stock_adapter(payload):
        return {**stock_raw, "request_id": payload["request_id"]}

    def project_07_adapter(payload):
        context = MarketResearchContext(
            request_id=payload["request_id"], planning_request=payload["planning_request"],
            trend_result=payload["trend_result"], inventory_product_result=payload["inventory_product_result"],
            requested_query=payload["planning_request"].get("query"),
        )
        return MarketResearchAgent().run(context).to_proposal_v1_3_dict()

    orchestrator = Orchestrator(
        trend=trend_adapter, stock=stock_adapter, project_07=project_07_adapter,
        project_06=lambda payload: _build_planning_draft_response(payload),
    )
    execution = orchestrator.run("synthetic query", request_id=rid)
    result = execution.to_dict()
    p07_raw = result["stages"][2]["raw_response"]
    p06_result = result["stages"][3]["raw_response"]
    assert [stage["stage"] for stage in result["stages"]] == ["trend", "stock", "project_07", "project_06"]
    assert all(stage["request_id"] == rid for stage in result["stages"])
    assert p07_raw["schema_version"] == "1.3-proposal"
    assert p06_result["input_evidence"]["sales_market_result"]["payload"] == p07_raw
    assert p06_result["draft"]["review_status"] == "md_review_required"
    print(json.dumps({
        "request_id": rid,
        "synthetic_only": True,
        "stages": [{"stage": s["stage"], "status": s["status"]} for s in result["stages"]],
        "project_07_schema_version": p07_raw["schema_version"],
        "project_06_draft_status": p06_result["draft"]["review_status"],
        "p07_payload_preserved": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
