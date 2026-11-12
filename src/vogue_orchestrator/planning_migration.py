"""Local P08 entry point for the PlanningDraft engine migrated from P06."""
from __future__ import annotations

from typing import Any, Mapping

from .new_product import build_planning_draft_response
from .planning_handoff import normalize_handoff_inputs


def _explicit_context(scope: Mapping[str, Any], trend_query: Mapping[str, Any] | None) -> dict[str, Any]:
    context = dict(scope)
    if not isinstance(trend_query, Mapping):
        return context
    terms = trend_query.get("trend_terms")
    season = terms.get("season") if isinstance(terms, Mapping) else None
    if isinstance(season, Mapping) and season.get("origin") == "user_requested":
        year, value = season.get("year"), season.get("season")
        if isinstance(year, int) and not isinstance(year, bool) and value in {"SS", "FW"}:
            context.setdefault("season", {"value": f"{year}{value}", "source": "user"})
    requested = trend_query.get("requested")
    categories = requested.get("categories") if isinstance(requested, Mapping) else None
    include = categories.get("include") if isinstance(categories, Mapping) else None
    if isinstance(include, list) and include:
        item = include[0]
        if isinstance(item, Mapping) and item.get("origin") == "user_requested":
            category = item.get("minor") or item.get("major")
            if isinstance(category, str) and category.strip():
                context.setdefault("category", {"value": category.strip(), "source": "user"})
    return context


def build_migrated_planning_draft(
    request_id: str, query: str, scope: Mapping[str, Any], trend_query: Mapping[str, Any] | None,
    trend_result: Mapping[str, Any], inventory_product_result: Mapping[str, Any], **generator_options: Any,
) -> dict[str, Any]:
    planning_request = {"schema_version": "1.0", "request_id": request_id, "query": query}
    context = _explicit_context(scope, trend_query)
    if context:
        planning_request["context"] = context
    adapted = normalize_handoff_inputs(request_id, {
        "planning_request": planning_request,
        "trend_result": dict(trend_result),
        "inventory_product_result": dict(inventory_product_result),
        "sales_market_result": None,
    })
    normalized = adapted.normalized_inputs
    draft = build_planning_draft_response({"request_id": request_id, **normalized}, **generator_options)
    evidence = draft.get("input_evidence")
    if isinstance(evidence, dict) and isinstance(evidence.get("trend_result"), dict):
        evidence["trend_result"]["payload"] = adapted.source_inputs["trend_result"]
        evidence["trend_result"]["internal_schema_version"] = "1.0"
        evidence["trend_result"]["source_schema_version"] = "1.1"
    draft["compatibility"] = adapted.provenance
    draft["request_id"] = request_id
    return draft
