"""Safe, bounded KG/RAG preparation for planning drafts."""

from __future__ import annotations

from typing import Iterable

from ..domain.context import ProductPlanningContext
from ..domain.analysis import dimension_mismatches
from ..domain.retrieval import (
    EmptyKnowledgeRetriever,
    EvidenceRef,
    KGPath,
    KnowledgeRetriever,
    PlanningKnowledgeContext,
    PurposeRetrieval,
    RetrievalQuery,
    RetrievalResult,
    RetrievalSourcePolicy,
)


PURPOSES = (
    "evidence_lookup", "planning_reference", "precedent", "taxonomy",
    "catalog_comparison", "assortment_relation",
)
QUERY_PLAN_VERSION = "planning-query-plan-v2"


def _sales_market_terms(payload: object, *, max_terms: int = 24) -> tuple[str, ...]:
    """Extract bounded search terms from producer findings, never IDs or metadata."""
    if not isinstance(payload, dict):
        return ()
    found: list[str] = []
    allowed_keys = {"summary", "findings", "observations", "sales_data", "market", "channel", "category", "product", "text", "name", "value", "label", "segment"}
    skip_keys = {"errors", "limitations", "follow_up_questions", "evidence", "evidence_ids", "evidence_id", "request_id", "agent_id", "schema_version", "status"}
    def visit(value: object, key: str = "") -> None:
        if len(found) >= max_terms or key in skip_keys:
            return
        if isinstance(value, dict):
            for child_key, child in value.items():
                if child_key in allowed_keys:
                    visit(child, child_key)
        elif isinstance(value, (list, tuple)):
            for child in value[:20]:
                visit(child, key)
        elif isinstance(value, str) and value.strip():
            found.append(value.strip()[:160])
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            found.append(str(value))
    visit(payload)
    return _unique(found[:max_terms])


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip()))


def _normalized_terms(values: Iterable[str]) -> tuple[str, ...]:
    return _unique(" ".join(value.casefold().split()) for value in values)


def _input_evidence_ids(context: ProductPlanningContext) -> tuple[str, ...]:
    """Collect only request, trend and stock IDs; Project_07 IDs are not resolved here."""
    values: list[str] = []
    if context.input_statuses.get("planning_request") in {"ok", "partial"}:
        values.extend(context.request.result.evidence_ids)
        for attribute in context.request.requested_attributes:
            values.extend(attribute.evidence_ids)
    if context.input_statuses.get("trend_result") in {"ok", "partial"}:
        values.extend(context.trends.result.evidence_ids)
        for candidate in context.trends.candidates:
            values.extend(candidate.evidence_ids)
            values.extend(candidate.description_evidence_ids)
            for attribute in candidate.attributes:
                values.extend(attribute.evidence_ids)
    if context.input_statuses.get("inventory_product_result") in {"ok", "partial"}:
        values.extend(context.inventory.result.evidence_ids)
        for product in context.inventory.products:
            values.extend(product.evidence_ids)
            for attribute in product.attributes:
                values.extend(attribute.evidence_ids)
    return _unique(values)


def _queries(
    context: ProductPlanningContext,
    policy: RetrievalSourcePolicy,
) -> tuple[tuple[str, RetrievalQuery | None], ...]:
    request = context.request
    compatible_trends = tuple(
        candidate for candidate in context.trends.candidates
        if context.input_statuses.get("trend_result") in {"ok", "partial"}
        and context.input_statuses.get("planning_request") in {"ok", "partial"}
        and not dimension_mismatches(request, candidate)
    )
    request_usable = context.input_statuses.get("planning_request") in {"ok", "partial"}
    categories = _unique((
        request.category if request_usable else "",
        *(item.category or "" for item in compatible_trends),
    ))
    trend_ids = _unique(item.name for item in compatible_trends)
    terms = _normalized_terms((
        *(str(item.value) for item in request.requested_attributes
          if request_usable and item.value is not None),
        *(item.name for item in request.requested_attributes if request_usable),
        *(str(attribute.value) for candidate in compatible_trends for attribute in candidate.attributes
          if attribute.value is not None and attribute.evidence_ids),
        *(attribute.name for candidate in compatible_trends for attribute in candidate.attributes
          if attribute.evidence_ids),
        *(_sales_market_terms(context.sales_market_result)
          if context.input_statuses.get("sales_market_result") in {"ok", "partial"} else ()),
    ))
    evidence_ids = _input_evidence_ids(context)

    queries: list[tuple[str, RetrievalQuery | None]] = []
    for purpose in PURPOSES:
        sources = policy.source_ids_by_purpose.get(purpose, ())
        source_types = policy.source_types_by_purpose.get(purpose, ())
        if not sources and not source_types:
            queries.append((purpose, None))
            continue
        if purpose == "evidence_lookup" and not evidence_ids:
            queries.append((purpose, None))
            continue
        if purpose != "evidence_lookup" and not (categories or trend_ids or terms):
            queries.append((purpose, None))
            continue
        queries.append((purpose, RetrievalQuery(
            trend_ids=trend_ids if purpose != "evidence_lookup" else (),
            category_ids=categories,
            attribute_terms=terms,
            allowed_source_ids=tuple(sources),
            limit=policy.limit_per_purpose,
            purpose=purpose,
            request_id=context.request_id,
            evidence_ids=evidence_ids if purpose == "evidence_lookup" else (),
            season=request.season if request_usable else None,
            market=request.market if request_usable else (),
            channel=request.channel if request_usable else (),
            allowed_source_types=tuple(source_types),
            query_plan_version=QUERY_PLAN_VERSION,
            exact_terms=_normalized_terms((*categories, *trend_ids, *terms)),
            candidate_limit=policy.candidate_limit_per_purpose,
            product_roles=(request.product_role,) if request_usable and request.product_role else (),
            dataset_id=policy.dataset_id,
            allow_synthetic=policy.allow_synthetic,
        )))
    return tuple(queries)


def _allowed_reference(item, query: RetrievalQuery) -> bool:
    if not item.verified:
        return False
    if item.permission_status == "authorized":
        pass
    elif not (query.allow_synthetic and item.is_synthetic and item.permission_status == "synthetic_only"
              and (not query.dataset_id or item.dataset_id == query.dataset_id)):
        return False
    if "product_planning" not in item.permitted_uses:
        return False
    if query.allowed_source_ids and item.source_id not in query.allowed_source_ids:
        return False
    if query.allowed_source_types and item.source_type not in query.allowed_source_types:
        return False
    if query.purpose == "evidence_lookup" and item.evidence_id not in query.evidence_ids:
        return False
    return bool(item.evidence_id and item.source_id)


def _query_trace(query: RetrievalQuery) -> dict[str, object]:
    return {
        "purpose": query.purpose,
        "query_plan_version": query.query_plan_version,
        "category_ids": list(query.category_ids),
        "product_roles": list(query.product_roles),
        "season": query.season,
        "market": list(query.market),
        "channel": list(query.channel),
        "attribute_terms": list(query.attribute_terms),
        "trend_ids": list(query.trend_ids),
        "exact_terms": list(query.exact_terms),
        "evidence_ids": list(query.evidence_ids),
        "allowed_source_ids": list(query.allowed_source_ids),
        "allowed_source_types": list(query.allowed_source_types),
        "limit": query.limit,
        "candidate_limit": query.candidate_limit,
        "allow_synthetic": query.allow_synthetic,
        "dataset_id": query.dataset_id,
    }


def retrieve_planning_knowledge(
    context: ProductPlanningContext,
    *,
    retriever: KnowledgeRetriever | None = None,
    policy: RetrievalSourcePolicy | None = None,
) -> PlanningKnowledgeContext:
    """Run only allowlisted planning searches; failures remain local to each route."""
    retriever = retriever or EmptyKnowledgeRetriever()
    policy = policy or RetrievalSourcePolicy()
    results: list[PurposeRetrieval] = []

    sales_status = context.input_statuses.get("sales_market_result", "not_provided")
    sales_evidence_present = bool(context.sales_market_result and sales_status in {"ok", "partial"})
    for purpose, query in _queries(context, policy):
        if query is None:
            note = "검색 자료원 또는 허용 목록이 설정되지 않았습니다."
            if purpose == "evidence_lookup" and sales_evidence_present:
                note += " Project_07 Evidence resolver는 공유 계약·권한 합의 전이라 조회하지 않았습니다."
            results.append(PurposeRetrieval(
                purpose, "not_configured", limitations=(note,),
                query_plan_version=QUERY_PLAN_VERSION,
                query_trace={"purpose": purpose, "query_plan_version": QUERY_PLAN_VERSION, "search_executed": False},
            ))
            continue
        try:
            raw = retriever.retrieve(query)
        except Exception:
            results.append(PurposeRetrieval(
                purpose, "error", limitations=(f"{purpose} 검색에 실패했습니다. 검색 결과는 사용하지 않았습니다.",),
                query_plan_version=QUERY_PLAN_VERSION, query_trace=_query_trace(query),
            ))
            continue
        if not isinstance(raw, RetrievalResult):
            results.append(PurposeRetrieval(
                purpose, "error", limitations=(f"{purpose} 검색 응답 형식이 올바르지 않아 결과를 사용하지 않았습니다.",),
                query_plan_version=QUERY_PLAN_VERSION, query_trace=_query_trace(query),
            ))
            continue
        if not isinstance(raw.status, str) or raw.status not in {"not_configured", "ok", "partial", "no_results", "error"}:
            results.append(PurposeRetrieval(
                purpose, "error", limitations=(f"{purpose} 검색 상태를 인식할 수 없습니다.",),
                query_plan_version=QUERY_PLAN_VERSION, query_trace=_query_trace(query),
            ))
            continue

        if raw.status in {"not_configured", "no_results", "error"}:
            limitations = tuple(value for value in raw.limitations if isinstance(value, str)) if isinstance(raw.limitations, (tuple, list)) else ()
            results.append(PurposeRetrieval(
                purpose=purpose,
                status=raw.status,
                limitations=limitations,
                index_version=raw.index_version,
                retrieval_run_id=raw.retrieval_run_id,
                query_plan_version=query.query_plan_version,
                query_trace=_query_trace(query),
            ))
            continue

        raw_evidence = raw.evidence if isinstance(raw.evidence, (tuple, list)) else ()
        raw_paths = raw.kg_paths if isinstance(raw.kg_paths, (tuple, list)) else ()
        evidence = tuple(
            item for item in raw_evidence
            if isinstance(item, EvidenceRef) and _allowed_reference(item, query)
        )
        rejected = len(raw_evidence) - len(evidence)
        allowed_ids = {item.evidence_id for item in evidence}
        paths = tuple(
            path for path in raw_paths
            if isinstance(path, KGPath) and path.evidence_ids and set(path.evidence_ids) <= allowed_ids
        )
        limitations = [value for value in raw.limitations if isinstance(value, str)] if isinstance(raw.limitations, (tuple, list)) else []
        status = raw.status
        if rejected or len(paths) < len(raw_paths):
            limitations.append("권한, 사용 목적 또는 source allowlist를 검증하지 못한 검색 결과를 제외했습니다.")
            if status in {"ok", "no_results"}:
                status = "partial"
        if status == "ok" and not evidence and not paths:
            status = "no_results"
        results.append(PurposeRetrieval(
            purpose=purpose,
            status=status,
            evidence=evidence,
            kg_paths=paths,
            limitations=tuple(dict.fromkeys(limitations)),
            index_version=raw.index_version,
            retrieval_run_id=raw.retrieval_run_id,
            query_plan_version=query.query_plan_version,
            query_trace=_query_trace(query),
        ))

    return PlanningKnowledgeContext(tuple(results))
