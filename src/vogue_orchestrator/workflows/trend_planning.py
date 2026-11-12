"""Internal TrendBatch-to-candidate planning use case (no public transport)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Mapping, Sequence

from ..domain.candidates import (
    Candidate, CandidateScore, PortfolioSelection, ScoreWeights,
    generate_existing_candidates, generate_new_candidate, score_candidate,
    select_top_candidates,
)
from ..domain.retrieval import (
    EmptyKnowledgeRetriever, EvidenceRef, KGPath, KnowledgeRetriever, RetrievalQuery, RetrievalResult,
)
from ..domain.trend_batch import TrendBatch
from ..domain.trend_evaluator import (
    CategoryTaxonomy, EvaluatedTrend, EvidenceResolver, MetadataOnlyEvidenceResolver, TrendFitWeights,
    evaluate_trend_batch,
)
from .image_generation import (
    ImageGenerationProvider, ImageGenerationRequest, ImageGenerationResult,
    PROMPT_VERSION, run_image_generation,
)


@dataclass(frozen=True)
class PlanningRunRequest:
    batch: TrendBatch
    existing_products: tuple[Mapping[str, object], ...] = ()
    limit: int = 5
    max_per_category: int = 2
    max_per_trend: int | None = 2
    max_per_type: int | None = 3
    source_snapshot_ids: tuple[str, ...] = ()
    allowed_knowledge_source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateBrief:
    candidate_id: str
    title: str
    candidate_type: str
    category_id: str
    target_season: str | None
    claims: tuple[Mapping[str, object], ...]
    evidence: tuple[EvidenceRef, ...]
    score: CandidateScore
    assumptions: tuple[str, ...]
    unknowns: tuple[str, ...]
    risks: tuple[str, ...]
    validation_questions: tuple[str, ...]
    visual_status: str = "not_requested"


@dataclass(frozen=True)
class PlanningRunResult:
    run_id: str
    status: str
    evaluated_trends: tuple[EvaluatedTrend, ...]
    candidates: tuple[Candidate, ...]
    scores: tuple[CandidateScore, ...]
    selection: PortfolioSelection
    briefs: tuple[CandidateBrief, ...]
    limitations: tuple[str, ...]


def _run_id(request: PlanningRunRequest, taxonomy_version: str,
            trend_fit_weights: TrendFitWeights | None, weights: ScoreWeights | None,
            score_components: Mapping[str, Mapping[str, float | None]] | None) -> str:
    material = {
        "batch_id": request.batch.batch_id,
        "schema_version": request.batch.schema_version,
        "taxonomy_version": taxonomy_version,
        "trend_fit_weights_version": trend_fit_weights.version if trend_fit_weights else None,
        "opportunity_weights_version": weights.version if weights else None,
        "trend_fit_weight_values": ([trend_fit_weights.attribute_relevance, trend_fit_weights.evidence_verification] if trend_fit_weights else None),
        "opportunity_weight_values": ([weights.trend_fit, weights.product_fit, weights.inventory_signal, weights.evidence_confidence] if weights else None),
        "source_snapshot_ids": request.source_snapshot_ids,
        "allowed_knowledge_source_ids": request.allowed_knowledge_source_ids,
        "existing_products": [
            {key: value for key, value in item.items() if key in {"product_id", "category_id", "name", "attributes", "attribute_evidence"}}
            for item in request.existing_products
        ],
        "portfolio_limits": [request.limit, request.max_per_category, request.max_per_trend, request.max_per_type],
        "score_components": score_components or {},
        "trends": [{
            "trend_id": item.trend_id, "name": item.name, "description": item.description,
            "evidence": [{"document_id": doc.document_id, "title": doc.title,
                "source_url": doc.source_url, "published_at": doc.published_at.isoformat() if doc.published_at else None,
                "excerpt": doc.excerpt, "locator": doc.locator} for doc in item.evidence],
        } for item in request.batch.trends],
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:20]
    return f"run-{digest}"


def _brief(candidate: Candidate, score: CandidateScore) -> CandidateBrief:
    claims: list[Mapping[str, object]] = [{
        "field": "concept_direction", "value": candidate.title,
        # Source documents support their linked attributes, not this generated
        # synthesis/title. Keeping this claim unsourced avoids overstating the
        # Evidence-to-recommendation relationship in a review brief.
        "kind": "proposal", "evidence_ids": [],
    }]
    claims.extend({
        "field": item.name, "value": item.value, "kind": item.kind,
        "evidence_ids": list(item.evidence_ids),
    } for item in candidate.attributes)
    return CandidateBrief(candidate.candidate_id, candidate.title, candidate.candidate_type,
        candidate.category_id, candidate.target_season, tuple(claims), candidate.evidence_refs,
        score, candidate.assumptions, candidate.unknowns, candidate.risks,
        candidate.validation_questions)


def _authorized_retrieval(result: RetrievalResult, allowed_sources: tuple[str, ...]) -> RetrievalResult:
    """Discard failed, unverified, unpermitted, or out-of-scope retrieval content."""
    statuses = {"not_configured", "ok", "partial", "no_results", "error"}
    if not isinstance(result, RetrievalResult) or not isinstance(result.status, str) or result.status not in statuses:
        return RetrievalResult("error", limitations=("Knowledge retrieval returned an invalid response; no results were used.",))
    if result.status in {"not_configured", "no_results", "error"}:
        return RetrievalResult(result.status, limitations=result.limitations)
    source_ids = set(allowed_sources)
    raw_evidence = result.evidence if isinstance(result.evidence, (tuple, list)) else ()
    raw_paths = result.kg_paths if isinstance(result.kg_paths, (tuple, list)) else ()
    evidence = tuple(
        item for item in raw_evidence
        if isinstance(item, EvidenceRef)
        and item.source_id in source_ids
        and item.verified
        and item.permission_status == "authorized"
        and "product_planning" in (item.permitted_uses or ())
    )
    allowed_ids = {item.evidence_id for item in evidence}
    paths = tuple(
        path for path in raw_paths
        if isinstance(path, KGPath) and path.evidence_ids and set(path.evidence_ids) <= allowed_ids
    )
    rejected = len(raw_evidence) - len(evidence)
    limitations = [value for value in result.limitations if isinstance(value, str)] if isinstance(result.limitations, (tuple, list)) else []
    status = result.status
    if rejected or len(paths) < len(raw_paths):
        limitations.append("검색 결과 중 권한 또는 source allowlist를 확인하지 못한 항목은 제외했습니다.")
        if status == "ok":
            status = "partial"
    if status == "ok" and not evidence and not paths:
        status = "no_results"
    return RetrievalResult(
        status, evidence, paths, tuple(dict.fromkeys(limitations)),
        result.index_version, result.retrieval_run_id,
    )


def run_trend_planning(
    request: PlanningRunRequest,
    *,
    taxonomy: CategoryTaxonomy,
    evidence_resolver: EvidenceResolver | None = None,
    trend_fit_weights: TrendFitWeights | None = None,
    knowledge: KnowledgeRetriever | None = None,
    weights: ScoreWeights | None = None,
    score_components: Mapping[str, Mapping[str, float | None]] | None = None,
) -> PlanningRunResult:
    """Run deterministic stages with adapter ports; no public API or DB assumed."""
    knowledge = knowledge or EmptyKnowledgeRetriever()
    evaluated = evaluate_trend_batch(request.batch, taxonomy, evidence_resolver, trend_fit_weights)
    candidates: list[Candidate] = []
    limitations: list[str] = []
    products = request.existing_products
    if not products:
        limitations.append("상위 상품·재고 에이전트의 기존 상품 목록이 제공되지 않았습니다.")
    for original, trend in zip(request.batch.trends, evaluated):
        source_docs = {doc.document_id: doc for doc in original.evidence}
        assessments = {item.document_id: item for item in trend.evidence}
        input_refs = tuple(EvidenceRef(
            evidence_id=document_id,
            source_id=document_id,
            locator=source_docs[document_id].locator,
            quote=source_docs[document_id].excerpt,
            source_date=source_docs[document_id].published_at,
            access_status="verified" if assessments[document_id].verified else "unverified",
            verified=assessments[document_id].verified,
        ) for document_id in source_docs if document_id in assessments)
        for evaluation in trend.category_evaluations:
            if evaluation.status != "ok":
                limitations.extend(evaluation.limitations)
                continue
            category_products = tuple(product for product in products
                                      if str(product.get("category_id", "")) == evaluation.category_id)
            if request.allowed_knowledge_source_ids:
                try:
                    raw_retrieval = knowledge.retrieve(RetrievalQuery(
                        (trend.trend_id,) if trend.trend_id else (), (evaluation.category_id,),
                        tuple(item.value for item in trend.attributes if item.status == "evidence_observation"),
                        allowed_source_ids=request.allowed_knowledge_source_ids,
                        limit=50,
                    ))
                    retrieval = _authorized_retrieval(raw_retrieval, request.allowed_knowledge_source_ids)
                except Exception:
                    retrieval = RetrievalResult(
                        "error", limitations=("Knowledge retrieval failed; planning continued without retrieved references.",),
                    )
            else:
                retrieval = RetrievalResult(
                    "not_configured",
                    limitations=("No approved knowledge source allowlist is configured; retrieval was not performed.",),
                )
            limitations.extend(retrieval.limitations)
            if category_products:
                existing = generate_existing_candidates(trend, evaluation.category_id,
                    category_products, target_season=request.batch.target_season,
                    evidence_refs=tuple(ref for ref in retrieval.evidence if ref.verified), kg_paths=retrieval.kg_paths)
                candidates.extend(existing.candidates)
                limitations.extend(existing.limitations)
            refs = tuple({ref.evidence_id: ref for ref in (*input_refs, *retrieval.evidence)}.values())
            new_result = generate_new_candidate(trend, evaluation.category_id,
                target_season=request.batch.target_season, evidence_refs=refs, kg_paths=retrieval.kg_paths)
            candidates.extend(new_result.candidates)
            limitations.extend(new_result.limitations)
    scores: list[CandidateScore] = []
    score_inputs = score_components or {}
    trend_fits = {(trend.trend_id, fit.category_id): fit.score
                  for trend in evaluated for fit in trend.category_evaluations}
    for candidate in candidates:
        components = score_inputs.get(candidate.candidate_id, {})
        trend_id = candidate.responds_to_trend_ids[0] if candidate.responds_to_trend_ids else None
        scores.append(score_candidate(candidate,
            trend_fit=components.get("trend_fit", trend_fits.get((trend_id, candidate.category_id))),
            product_fit=components.get("product_fit"),
            inventory_signal=components.get("inventory_signal"),
            evidence_confidence=components.get("evidence_confidence"),
            weights=weights))
    score_map = {item.candidate_id: item for item in scores}
    selection = select_top_candidates(candidates, score_map, limit=request.limit,
        max_per_category=request.max_per_category, max_per_trend=request.max_per_trend,
        max_per_type=request.max_per_type)
    briefs = tuple(_brief(candidate, score_map[candidate.candidate_id]) for candidate in selection.selected)
    if weights is None:
        limitations.append("Opportunity score weights are not configured; TOP 5 remains empty until an approved scoring configuration is supplied.")
    status = "completed" if selection.selected else ("needs_scoring_configuration" if candidates and weights is None else "no_eligible_candidates")
    return PlanningRunResult(_run_id(request, taxonomy.version, trend_fit_weights, weights, score_components), status, evaluated, tuple(candidates), tuple(scores), selection, briefs,
        tuple(dict.fromkeys(limitations)))


def build_candidate_image_request(candidate: Candidate) -> ImageGenerationRequest:
    """Prepare a review-only visual prompt without calling an image provider."""
    evidence_ids = tuple(dict.fromkeys(
        eid for attribute in candidate.attributes for eid in attribute.evidence_ids
    ))
    evidence_lines = [
        f"- {item.name}={item.value} [Evidence: {', '.join(item.evidence_ids)}]"
        for item in candidate.attributes
        if item.kind == "evidence_observation" and item.evidence_ids
    ]
    proposal_lines = [
        f"- {item.name}={item.value} (검토용 제안; 확정 사실 아님)"
        for item in candidate.attributes
        if item.kind in {"proposal", "hypothesis", "unknown", "to_validate"}
    ]
    prompt = "\n\n".join((
        f"상품 콘셉트: {candidate.title}\n카테고리: {candidate.category_id}\n시즌: {candidate.target_season or '미정'}",
        "Evidence로 확인된 속성:\n" + ("\n".join(evidence_lines) if evidence_lines else "- 없음"),
        "검토용 시각화 제안:\n" + ("\n".join(proposal_lines) if proposal_lines else "- 추가 디자인 세부는 디자이너 제안으로만 표현"),
        "고객 선호, 미래 수요, 원가, 생산 가능성을 암시하지 마세요. 로고나 읽을 수 있는 문구를 만들지 마세요.",
    ))
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return ImageGenerationRequest(
        request_id=candidate.candidate_id, season=candidate.target_season,
        category=candidate.category_id, prompt=prompt, prompt_version=PROMPT_VERSION,
        prompt_sha256=digest, evidence_ids=evidence_ids,
        proposal_fields=tuple(proposal_lines), limitations=tuple((*candidate.unknowns, *candidate.risks)),
        conflicts=(),
    )


def generate_candidate_visual(
    candidate: Candidate,
    *,
    provider: ImageGenerationProvider | None = None,
    enabled: bool = False,
    confirmed: bool = False,
    output_dir: Path = Path("data/local/generated_images"),
) -> ImageGenerationResult:
    """Reuse the existing opt-in provider workflow for an explicit candidate."""
    request = build_candidate_image_request(candidate)
    return run_image_generation(request, provider=provider, enabled=enabled,
                                confirmed=confirmed, output_dir=output_dir)
