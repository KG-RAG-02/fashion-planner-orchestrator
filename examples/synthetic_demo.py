"""Run the orchestrator against synthetic stage adapters only."""

import json

from vogue_orchestrator import Orchestrator


def envelope(stage, producer, version="synthetic/0", status="ok", **extra):
    return {"request_id": stage["request_id"], "agent_id": producer,
            "schema_version": version, "status": status, **extra}


def trend(payload):
    rid = payload["request_id"]
    return envelope(payload, "trend-analysis-agent", "1.0", "partial",
                    planning_request={"request_id": rid, "schema_version": "1.0", "query": payload["query"]},
                    trend_result=envelope(payload, "trend-analysis-agent", "1.0", "partial",
                                          findings=[], evidence=[], limitations=["합성 트렌드 fixture"]))


def stock(payload):
    return envelope(payload, "stock-agent", "1.0", "no_results", products=[], evidence=[],
                    limitations=["합성 fixture: 결과 없음"])


def project_07(payload):
    return envelope(payload, "market-research-agent", "1.3-proposal", "no_results",
                    summary="합성 fixture 전용", findings=[], evidence=[], limitations=["실제 자료 미사용"],
                    errors=[], sales_data={"status": "no_results", "observations": [], "limitations": [],
                                           "errors": [], "missing_data": []})


def project_06(payload):
    return {"request_id": payload["request_id"], "status": "partial", "draft": {"review_status": "md_review_required"},
            "input_evidence": {"sales_market_result": {"payload": payload["sales_market_result"]}}}


if __name__ == "__main__":
    result = Orchestrator(trend=trend, stock=stock, project_07=project_07,
                          project_06=project_06).run("합성: 2027SS 온라인 여성 니트")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
