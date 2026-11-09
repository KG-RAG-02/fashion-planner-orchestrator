"""Synthetic-only adapters for the local UI preview."""

from __future__ import annotations

from typing import Any

from .core import Orchestrator


def build_demo_orchestrator() -> Orchestrator:
    """Create a four-stage demo pipeline that never calls external services."""

    def trend(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": payload["request_id"],
            "status": "no_results",
            "applied_filters": None,
            "trends": [],
            "evidence": [],
            "limitations": ["합성 데모: trend-analysis-agent 실제 검색을 실행하지 않았습니다."],
        }

    def stock(trend_result: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "request_id": trend_result["request_id"],
            "agent_id": "stock-agent",
            "status": "no_results",
            "summary": "합성 데모: 재고 자료를 조회하지 않았습니다.",
            "products": [],
            "trend_results": [],
            "season_basis": None,
            "evidence": [],
            "errors": [],
        }

    def project_07(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "1.3-proposal",
            "request_id": payload["request_id"],
            "agent_id": "market-research-agent",
            "status": "no_results",
            "summary": "합성 데모: 판매·시장 자료를 조회하지 않았습니다.",
            "requested_scope": {"query": payload["planning_request"].get("query")},
            "sales_data": {
                "status": "no_results", "observations": [], "limitations": [],
                "errors": [], "missing_data": [],
            },
            "market_data": {
                "status": "no_results", "observations": [], "limitations": [],
                "errors": [], "missing_data": [],
            },
            "unit_economics": {
                "status": "no_results", "calculations": [], "limitations": [],
                "errors": [], "missing_data": [],
            },
            "findings": [], "evidence": [],
            "missing_data": [
                {"domain": "sales_market", "reason": "synthetic_demo_no_source", "message": "실제 자료원을 사용하지 않았습니다."}
            ],
            "limitations": ["합성 데모 결과이며 실제 판매·시장 조사가 아닙니다."],
            "errors": [], "sales_retrieval": None, "market_retrieval": None,
            "comparability": [],
        }

    def project_06(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": payload["request_id"],
            "status": "partial",
            "draft": {
                "title": "합성 기획 초안",
                "review_status": "md_review_required",
                "summary": "앞 단계 자료가 없는 합성 데모이므로 실제 기획 권고를 생성하지 않았습니다.",
            },
            "input_evidence": {
                name: {"status": value.get("status") if isinstance(value, dict) else None, "payload": value}
                for name, value in payload.items()
                if name != "request_id"
            },
            "limitations": ["합성 데모: 실제 트렌드·재고·판매·시장 자료를 사용하지 않았습니다."],
        }

    return Orchestrator(
        trend=trend,
        stock=stock,
        project_07=project_07,
        project_06=project_06,
    )
