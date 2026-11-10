"""Call the actual local Project_07 and Project_06 code with synthetic payloads only.

Run from project_08 with:
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 examples/synthetic_local_integration.py
"""

from __future__ import annotations

import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
sys.dont_write_bytecode = True
sys.path[:0] = [str(WORKSPACE / "project_07" / "src"), str(WORKSPACE / "project_06" / "src")]

from market_research_agent import (  # noqa: E402
    EvidenceRecord, MarketDataResult, MarketObservation,
    PermissionStatus, ResultStatus, SalesDataResult,
    SalesObservation, SourceReport,
)
from vogue_npp.prototype.server import PrototypeHandler  # noqa: E402
from vogue_orchestrator import Orchestrator  # noqa: E402
from vogue_orchestrator.connectors import _post_json, _project07_adapter  # noqa: E402


class SyntheticSalesMarketSource:
    """Explicit synthetic Project_07 source; no local datasets are read."""

    def research(self, context):
        sales_evidence = EvidenceRecord(
            evidence_id="synthetic-sales:E-001", source_id="synthetic-fixture-sales",
            source_type="synthetic_fixture", source_name="Synthetic sales fixture",
            permission_status=PermissionStatus.AUTHORIZED,
        )
        market_evidence = EvidenceRecord(
            evidence_id="synthetic-market:E-001", source_id="synthetic-fixture-market",
            source_type="synthetic_fixture", source_name="Synthetic market fixture",
            permission_status=PermissionStatus.AUTHORIZED,
        )
        return SourceReport(
            status=ResultStatus.OK,
            evidence=(sales_evidence, market_evidence),
            sales_data=SalesDataResult(
                status="ok",
                observations=(SalesObservation(
                    observation_id="synthetic-sales:O-001", metric_name="units_sold",
                    value=123, unit="pieces", evidence_ids=(sales_evidence.evidence_id,),
                    category="outerwear", period_start="2026-10-01", period_end="2026-10-31",
                ),),
            ),
            market_data=MarketDataResult(
                status="ok",
                observations=(MarketObservation(
                    observation_id="synthetic-market:O-001", metric_name="category_demand_index",
                    measure_type="aggregate_index", value=42.3, unit="index",
                    evidence_ids=(market_evidence.evidence_id,), category="outerwear",
                    geography="KR", period_start="2026-10-01", period_end="2026-10-31",
                ),),
            ),
        )


def main() -> None:
    rid = "synthetic-req-001"
    trend_raw = json.loads((ROOT / "examples/fixtures/trend-analysis-result.no-results.synthetic.json").read_text())
    stock_raw = json.loads((ROOT / "examples/fixtures/stock-agent-result.no-results.synthetic.json").read_text())

    def trend_adapter(payload):
        return {**trend_raw, "request_id": payload["request_id"]}

    def stock_adapter(payload):
        return {**stock_raw, "request_id": payload["request_id"]}

    project_07_adapter = _project07_adapter(
        WORKSPACE / "project_07", SyntheticSalesMarketSource,
    )

    # Reuse Project_06's real HTTP handler on an ephemeral loopback port.
    # POST /api/planning-draft does not initialize or write its SQLite repository.
    p06_server = ThreadingHTTPServer(("127.0.0.1", 0), PrototypeHandler)
    p06_thread = Thread(target=p06_server.serve_forever, daemon=True)
    p06_thread.start()
    p06_url = f"http://127.0.0.1:{p06_server.server_address[1]}/api/planning-draft"

    orchestrator = Orchestrator(
        trend=trend_adapter, stock=stock_adapter, project_07=project_07_adapter,
        project_06=lambda payload: _post_json(p06_url, payload),
    )
    try:
        execution = orchestrator.run("synthetic query", request_id=rid)
    finally:
        p06_server.shutdown()
        p06_server.server_close()
        p06_thread.join(timeout=2)
    result = execution.to_dict()
    p07_raw = result["stages"][2]["raw_response"]
    p06_result = result["stages"][3]["raw_response"]
    assert [stage["stage"] for stage in result["stages"]] == ["trend", "stock", "project_07", "project_06"]
    assert all(stage["request_id"] == rid for stage in result["stages"])
    assert p07_raw["schema_version"] == "1.3-proposal"
    assert len(p07_raw["sales_data"]["observations"]) == 1
    assert len(p07_raw["market_data"]["observations"]) == 1
    # HTTP JSON converts tuples to arrays; compare the serialized producer value.
    p07_wire = json.loads(json.dumps(p07_raw, ensure_ascii=False))
    assert p06_result["input_evidence"]["sales_market_result"]["payload"] == p07_wire
    assert p06_result["input_statuses"]["sales_market_result"] in {"ok", "partial"}
    assert p06_result["draft"]["sales_market_result"] == p07_wire
    assert p07_wire["sales_data"]["observations"][0]["value"] == 123
    assert p07_wire["market_data"]["observations"][0]["value"] == 42.3
    assert p06_result["draft"]["review_status"] == "md_review_required"
    print(json.dumps({
        "request_id": rid,
        "synthetic_only": True,
        "project_06_transport": "actual HTTP handler on ephemeral loopback",
        "stages": [{"stage": s["stage"], "status": s["status"]} for s in result["stages"]],
        "project_07_schema_version": p07_raw["schema_version"],
        "sales_observations": len(p07_raw["sales_data"]["observations"]),
        "market_observations": len(p07_raw["market_data"]["observations"]),
        "project_06_sales_market_input_status": p06_result["input_statuses"]["sales_market_result"],
        "project_06_draft_status": p06_result["draft"]["review_status"],
        "p07_payload_preserved": True,
        "p06_draft_payload_preserved": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
