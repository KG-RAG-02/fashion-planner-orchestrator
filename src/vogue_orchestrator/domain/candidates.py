"""Evidence-aware existing/new product candidates and conservative portfolio selection."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from .retrieval import EvidenceRef, KGPath
from .trend_evaluator import EvaluatedTrend


GENERATOR_VERSION = "candidate-generator-rules-v1"
SCORER_VERSION = "opportunity-scorer-configurable-v1"
SELECTOR_VERSION = "portfolio-selector-v1"


@dataclass(frozen=True)
class CandidateAttribute:
    name: str
    value: str | int | float | None
    kind: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    candidate_type: str
    title: str
    category_id: str
    target_season: str | None
    responds_to_trend_ids: tuple[str, ...]
    product_id: str | None
    attributes: tuple[CandidateAttribute, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    source_record_refs: tuple[str, ...]
    kg_paths: tuple[KGPath, ...]
    assumptions: tuple[str, ...]
    unknowns: tuple[str, ...]
    risks: tuple[str, ...]
    validation_questions: tuple[str, ...]
    generator_version: str = GENERATOR_VERSION

    @property
    def fingerprint(self) -> str:
        attrs = sorted((item.name.casefold(), str(item.value).casefold()) for item in self.attributes)
        canonical = "|".join((self.candidate_type, self.category_id.casefold(), self.product_id or "", repr(attrs)))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateGenerationResult:
    status: str
    candidates: tuple[Candidate, ...]
    limitations: tuple[str, ...] = ()


def _stable_id(kind: str, trend: EvaluatedTrend, category_id: str, product_id: str | None, seed: str) -> str:
    material = "|".join((kind, trend.batch_id, trend.trend_id or trend.normalized_name, category_id, product_id or "", seed))
    return f"cand-{hashlib.sha256(material.encode()).hexdigest()[:16]}"


def generate_existing_candidates(
    trend: EvaluatedTrend,
    category_id: str,
    products: Sequence[Mapping[str, object]],
    *,
    target_season: str | None = None,
    evidence_refs: Sequence[EvidenceRef] = (),
    kg_paths: Sequence[KGPath] = (),
) -> CandidateGenerationResult:
    candidates: list[Candidate] = []
    for product in products:
        product_id = str(product.get("product_id", "")).strip()
        if not product_id or str(product.get("category_id", "")) != category_id:
            continue
        product_evidence_refs = tuple(ref for ref in product.get("evidence_refs", ()) if isinstance(ref, EvidenceRef))
        combined_evidence_refs = tuple({ref.evidence_id: ref for ref in (*product_evidence_refs, *evidence_refs)}.values())
        verified_evidence_ids = {ref.evidence_id for ref in combined_evidence_refs if ref.verified}
        attributes_raw = product.get("attributes", {})
        attributes: list[CandidateAttribute] = []
        if isinstance(attributes_raw, Mapping):
            for name, value in attributes_raw.items():
                if value is not None:
                    evidence_ids = tuple(product.get("attribute_evidence", {}).get(name, ())) if isinstance(product.get("attribute_evidence", {}), Mapping) else ()
                    is_verified = bool(evidence_ids) and set(evidence_ids).issubset(verified_evidence_ids)
                    attributes.append(CandidateAttribute(str(name), value, "evidence_observation" if is_verified else "unknown", evidence_ids))
        evidence_ids = tuple(dict.fromkeys(eid for attr in attributes for eid in attr.evidence_ids))
        evidence_ids += tuple(ref.evidence_id for ref in combined_evidence_refs)
        unknowns: list[str] = []
        if not attributes:
            unknowns.append("기존 상품 속성을 제공 자료에서 확인하지 못했습니다.")
        candidates.append(Candidate(
            _stable_id("existing_improvement", trend, category_id, product_id, "existing-v1"),
            "existing_improvement", f"{product.get('name', product_id)} 개선 검토", category_id,
            target_season,
            (trend.trend_id,) if trend.trend_id else (), product_id, tuple(attributes), combined_evidence_refs,
            (), tuple(kg_paths),
            ("트렌드 속성을 기존 상품에 적용하는 방향은 검토 제안입니다.",),
            tuple(unknowns),
            ("상품 속성 근거만으로 판매 성과·미래 수요·생산 가능성을 판단하지 않습니다.",),
            ("MD가 적용할 트렌드 속성과 수정 범위를 확인해야 합니다.",),
        ))
    result_status = "ok" if candidates else "no_results"
    limitations: list[str] = []
    if not candidates:
        limitations.append("해당 카테고리에 매핑된 기존 상품이 없습니다.")
    return CandidateGenerationResult(result_status, tuple(candidates), tuple(limitations))


def generate_new_candidate(
    trend: EvaluatedTrend,
    category_id: str,
    *,
    target_season: str | None,
    evidence_refs: Sequence[EvidenceRef] = (),
    kg_paths: Sequence[KGPath] = (),
) -> CandidateGenerationResult:
    fit = next((item for item in trend.category_evaluations if item.category_id == category_id), None)
    if fit is None or fit.status != "ok" or fit.score is None:
        reason = fit.status if fit else "category_not_evaluated"
        return CandidateGenerationResult("insufficient_evidence", (), (f"New candidate generation withheld: category fit status is {reason}.",))
    attributes = tuple(CandidateAttribute(item.attribute_type, item.value, "evidence_observation", item.evidence_ids)
                       for item in trend.attributes if item.status == "evidence_observation" and item.evidence_ids)
    if not attributes:
        return CandidateGenerationResult("insufficient_evidence", (), ("근거로 확인된 디자인 속성이 없어 신규 후보를 만들지 않았습니다.",))
    refs = tuple(ref for ref in evidence_refs if ref.verified and ref.evidence_id in fit.evidence_ids)
    if not refs:
        return CandidateGenerationResult("insufficient_evidence", (), ("검증된 EvidenceRef가 없어 신규 후보를 만들지 않았습니다.",))
    candidate = Candidate(
        _stable_id("new_product", trend, category_id, None, repr(attributes)), "new_product",
        f"{trend.original_name} 기반 {category_id} 신규 콘셉트", category_id, target_season,
        (trend.trend_id,) if trend.trend_id else (), None, attributes, refs, (), tuple(kg_paths),
        ("추가 디자인 세부는 검토용 제안이며 확정 사양이 아닙니다.",),
        ("목표 가격, 마진, 고객 선호, 생산 가능성은 입력되지 않았습니다.",),
        ("카테고리 적합도와 사업 타당성은 MD 검토가 필요합니다.",),
        ("소재·핏·가격·제조 조건은 후속 검증이 필요합니다.",),
    )
    return CandidateGenerationResult("ok", (candidate,))


@dataclass(frozen=True)
class ScoreWeights:
    trend_fit: float
    product_fit: float
    inventory_signal: float
    evidence_confidence: float
    version: str

    def __post_init__(self) -> None:
        values = (self.trend_fit, self.product_fit, self.inventory_signal, self.evidence_confidence)
        if not self.version.strip() or any(not math.isfinite(value) or value < 0 for value in values) or sum(values) <= 0:
            raise ValueError("Score weights must be non-negative with a positive sum.")


@dataclass(frozen=True)
class CandidateScore:
    candidate_id: str
    status: str
    score: float | None
    breakdown: Mapping[str, float | None]
    coverage: float
    weights_version: str
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...] = ()


def score_candidate(
    candidate: Candidate,
    *,
    trend_fit: float | None,
    product_fit: float | None,
    inventory_signal: float | None,
    evidence_confidence: float | None,
    weights: ScoreWeights | None = None,
) -> CandidateScore:
    components = {
        "trend_fit": trend_fit,
        "product_fit": product_fit,
        "inventory_signal": inventory_signal,
        "evidence_confidence": evidence_confidence,
    }
    for name, value in components.items():
        if value is not None and (not math.isfinite(value) or value < 0 or value > 100):
            raise ValueError(f"{name} must be within the 0..100 score range.")
    evidence_ids = tuple(dict.fromkeys((
        *(ref.evidence_id for ref in candidate.evidence_refs),
        *(eid for attribute in candidate.attributes for eid in attribute.evidence_ids),
    )))
    coverage = sum(value is not None for value in components.values()) / len(components)
    if weights is None:
        return CandidateScore(candidate.candidate_id, "unconfigured", None, components, coverage, "none", evidence_ids,
                              ("가중치/평가 기준이 승인되지 않아 단일 점수를 계산하지 않았습니다.",))
    weight_values = {
        "trend_fit": weights.trend_fit,
        "product_fit": weights.product_fit,
        "inventory_signal": weights.inventory_signal,
        "evidence_confidence": weights.evidence_confidence,
    }
    active_weight = sum(weight_values[name] for name, value in components.items() if value is not None)
    has_verified_evidence = any(ref.verified for ref in candidate.evidence_refs)
    if coverage < 0.8 or active_weight <= 0 or not evidence_ids or not has_verified_evidence:
        return CandidateScore(candidate.candidate_id, "insufficient_data", None, components, coverage, weights.version, evidence_ids,
                              ("score coverage is below 0.8, candidate evidence is missing, or no verified evidence reference is attached.",))
    score = sum(float(value) * weight_values[name] for name, value in components.items() if value is not None) / active_weight
    return CandidateScore(candidate.candidate_id, "scored", round(score, 4), components, coverage, weights.version, evidence_ids)


@dataclass(frozen=True)
class PortfolioSelection:
    selected: tuple[Candidate, ...]
    excluded: Mapping[str, str]
    status: str
    selector_version: str = SELECTOR_VERSION


def _fingerprint(candidate: Candidate) -> str:
    normalized_title = re.sub(r"[^a-z0-9가-힣]+", " ", candidate.title.casefold()).strip()
    return candidate.fingerprint if candidate.attributes else f"{candidate.candidate_type}|{candidate.category_id}|{normalized_title}"


def select_top_candidates(
    candidates: Sequence[Candidate],
    scores: Mapping[str, CandidateScore],
    *,
    limit: int = 5,
    min_score: float = 0,
    max_per_category: int = 2,
    max_per_trend: int | None = 2,
    max_per_type: int | None = 3,
) -> PortfolioSelection:
    if limit < 1 or max_per_category < 1 or (max_per_trend is not None and max_per_trend < 1) or (max_per_type is not None and max_per_type < 1):
        raise ValueError("limit and max_per_category must be positive.")
    if min_score < 0 or min_score > 100:
        raise ValueError("min_score must be within the 0..100 score range.")
    excluded: dict[str, str] = {}
    eligible: list[tuple[Candidate, CandidateScore]] = []
    seen_candidate_ids: set[str] = set()
    for candidate in candidates:
        if candidate.candidate_id in seen_candidate_ids:
            continue
        seen_candidate_ids.add(candidate.candidate_id)
        score = scores.get(candidate.candidate_id)
        if score is None or score.status != "scored" or score.score is None:
            excluded[candidate.candidate_id] = "점수가 없거나 데이터가 불충분합니다."
            continue
        if score.score < min_score:
            excluded[candidate.candidate_id] = "최소 점수 기준 미달입니다."
            continue
        eligible.append((candidate, score))
    eligible.sort(key=lambda pair: (-float(pair[1].score), -pair[1].coverage, pair[0].candidate_id))
    # Deduplicate only after sorting so the strongest supported version of a
    # concept survives even when generators emit a weaker duplicate first.
    deduplicated: list[tuple[Candidate, CandidateScore]] = []
    seen_fingerprints: set[str] = set()
    for candidate, score in eligible:
        fingerprint = _fingerprint(candidate)
        if fingerprint in seen_fingerprints:
            excluded[candidate.candidate_id] = "중복 후보로 제외되었습니다."
            continue
        seen_fingerprints.add(fingerprint)
        deduplicated.append((candidate, score))
    # A single requested category is a scope constraint, not a diversity
    # dimension. Applying the default cap there would silently truncate a
    # five-item brief to two candidates even when trend/type diversity exists.
    effective_category_cap = (
        max_per_category
        if len({candidate.category_id for candidate, _ in deduplicated}) > 1
        else None
    )
    category_counts: dict[str, int] = {}
    trend_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    selected: list[Candidate] = []
    for candidate, score in deduplicated:
        if len(selected) >= limit:
            excluded[candidate.candidate_id] = "선정 한도 초과입니다."
            continue
        category_count = category_counts.get(candidate.category_id, 0)
        if effective_category_cap is not None and category_count >= effective_category_cap:
            excluded[candidate.candidate_id] = "카테고리 다양성 제한을 초과했습니다."
            continue
        trend_key = candidate.responds_to_trend_ids[0] if candidate.responds_to_trend_ids else "unknown"
        if max_per_trend is not None and trend_counts.get(trend_key, 0) >= max_per_trend:
            excluded[candidate.candidate_id] = "트렌드 다양성 제한을 초과했습니다."
            continue
        if max_per_type is not None and type_counts.get(candidate.candidate_type, 0) >= max_per_type:
            excluded[candidate.candidate_id] = "기존/신규 후보 유형 다양성 제한을 초과했습니다."
            continue
        selected.append(candidate)
        category_counts[candidate.category_id] = category_count + 1
        trend_counts[trend_key] = trend_counts.get(trend_key, 0) + 1
        type_counts[candidate.candidate_type] = type_counts.get(candidate.candidate_type, 0) + 1
    return PortfolioSelection(tuple(selected), excluded, "ok" if selected else "no_eligible_candidates")

