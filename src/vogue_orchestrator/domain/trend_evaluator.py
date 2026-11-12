"""Deterministic, provenance-preserving Trend × Category evaluation core.

This module does not infer source rank, call an LLM, access a graph database,
or fetch URLs. Production extraction and evidence resolution can be supplied
through explicit ports later.
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from datetime import date
from typing import Protocol, Sequence

from .trend_batch import EvidenceDocument, TrendBatch, TrendInput


EVALUATOR_VERSION = "trend-evaluator-category-affinity-v2"
DEFAULT_TAXONOMY_VERSION = "unconfigured"


@dataclass(frozen=True)
class Category:
    category_id: str
    name: str
    aliases: tuple[str, ...] = ()
    taxonomy_version: str = DEFAULT_TAXONOMY_VERSION
    relevant_attribute_types: tuple[str, ...] = ()
    # Optional taxonomy-owned applicability values, keyed as "type:canonical value".
    # These are category-fit judgements, not upstream trend popularity scores.
    attribute_value_affinities: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        keys = [normalize_text(key) for key, _ in self.attribute_value_affinities]
        if len(keys) != len(set(keys)):
            raise ValueError("Category attribute affinity keys must be unique.")
        if any(not math.isfinite(score) or score < 0 or score > 1 for _, score in self.attribute_value_affinities):
            raise ValueError("Category attribute affinities must be within 0..1.")


@dataclass(frozen=True)
class CategoryMapping:
    status: str
    category_id: str | None = None
    candidates: tuple[str, ...] = ()
    taxonomy_version: str = DEFAULT_TAXONOMY_VERSION


class CategoryTaxonomy(Protocol):
    version: str

    def map_category(self, label: str) -> CategoryMapping: ...
    def categories(self) -> Sequence[Category]: ...


@dataclass(frozen=True)
class InMemoryCategoryTaxonomy:
    """Small explicit taxonomy adapter for local use and tests."""

    items: tuple[Category, ...]
    version: str

    def categories(self) -> Sequence[Category]:
        return self.items

    def map_category(self, label: str) -> CategoryMapping:
        target = normalize_text(label)
        exact = [item for item in self.items if normalize_text(item.category_id) == target or normalize_text(item.name) == target]
        if len(exact) == 1:
            return CategoryMapping("exact", exact[0].category_id, taxonomy_version=self.version)
        if len(exact) > 1:
            return CategoryMapping("ambiguous", candidates=tuple(item.category_id for item in exact), taxonomy_version=self.version)
        aliases = [item for item in self.items if target and target in {normalize_text(a) for a in item.aliases}]
        if len(aliases) == 1:
            return CategoryMapping("alias", aliases[0].category_id, taxonomy_version=self.version)
        if len(aliases) > 1:
            return CategoryMapping("ambiguous", candidates=tuple(item.category_id for item in aliases), taxonomy_version=self.version)
        return CategoryMapping("unmapped", taxonomy_version=self.version)


@dataclass(frozen=True)
class EvidenceAssessment:
    document_id: str
    status: str
    verified: bool
    published_at: date | None
    limitations: tuple[str, ...] = ()
    independence_group: str | None = None


class EvidenceResolver(Protocol):
    def assess(self, document: EvidenceDocument) -> EvidenceAssessment: ...


@dataclass(frozen=True)
class MetadataOnlyEvidenceResolver:
    """Safe fallback: no network access and no unsupported verification claim."""

    def assess(self, document: EvidenceDocument) -> EvidenceAssessment:
        limitations: list[str] = ["원문 저장소에 연결되지 않아 인용과 출처 권한을 검증하지 않았습니다."]
        if document.published_at is None:
            limitations.append("게시일이 없어 근거 최신성을 평가할 수 없습니다.")
        if not document.source_url and not document.locator:
            limitations.append("문서 조회 경로가 없어 ID와 전달된 excerpt만 보존했습니다.")
        return EvidenceAssessment(document.document_id, "unverified", False, document.published_at, tuple(limitations))


@dataclass(frozen=True)
class ExtractedAttribute:
    attribute_type: str
    value: str
    status: str
    evidence_ids: tuple[str, ...] = ()
    confidence: float | None = None


@dataclass(frozen=True)
class TrendCategoryEvaluation:
    trend_id: str | None
    trend_name: str
    category_id: str
    status: str
    score: float | None
    components: dict[str, float | None]
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    evaluator_version: str
    taxonomy_version: str


@dataclass(frozen=True)
class TrendFitWeights:
    attribute_relevance: float
    evidence_verification: float
    version: str

    def __post_init__(self) -> None:
        values = (self.attribute_relevance, self.evidence_verification)
        if not self.version.strip() or any(not math.isfinite(value) or value < 0 for value in values) or sum(values) <= 0:
            raise ValueError("Trend fit weights must be non-negative with a positive sum.")


@dataclass(frozen=True)
class EvaluatedTrend:
    batch_id: str
    trend_id: str | None
    original_name: str
    normalized_name: str
    description: str
    attributes: tuple[ExtractedAttribute, ...]
    evidence: tuple[EvidenceAssessment, ...]
    category_evaluations: tuple[TrendCategoryEvaluation, ...]
    limitations: tuple[str, ...]
    evaluator_version: str
    taxonomy_version: str


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _contains_term(text: str, term: str) -> bool:
    """Match English tokens exactly while allowing Korean compound forms."""
    normalized_text = normalize_text(text)
    normalized_term = normalize_text(term)
    if normalized_term.isascii():
        return re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])",
            normalized_text,
        ) is not None
    return normalized_term in normalized_text


_ATTRIBUTE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("color", ("버건디", "burgundy", "red", "빨강", "레드", "blue", "파랑", "블루", "green", "초록", "그린", "brown", "브라운", "black", "검정", "화이트", "white")),
    ("material", ("가죽", "레더", "leather", "데님", "denim", "울", "wool", "캐시미어", "cashmere", "니트", "knit", "실크", "silk", "린넨", "linen", "코튼", "cotton")),
    ("silhouette", ("오버사이즈", "oversized", "슬림핏", "wide-leg", "와이드핏", "크롭", "cropped", "테일러드", "tailored")),
    ("pattern", ("체크", "check", "스트라이프", "stripe", "플로럴", "floral", "프린트", "print", "도트", "polka dot")),
    ("style", ("미니멀리즘", "minimalism", "minimalist", "미니멀", "minimal", "맥시멀리즘", "maximalism", "maximalist", "레트로", "retro", "유틸리티", "utility", "고프코어", "gorpcore", "발레코어", "balletcore", "ballet-core", "보헤미안", "bohemian")),
)


def extract_attributes(trend: TrendInput) -> tuple[ExtractedAttribute, ...]:
    """Extract conservative dictionary matches, supported only by excerpts.

    Description-only matches remain hypotheses and are excluded from the
    evaluator's evidence-supported fit calculation.
    """
    haystack = normalize_text(f"{trend.name} {trend.description}")
    found: list[ExtractedAttribute] = []
    for attribute_type, terms in _ATTRIBUTE_PATTERNS:
        matches = [term for term in terms if _contains_term(haystack, term)]
        # Prefer the longer matched phrase when one alias is contained in
        # another (e.g. "minimalism" also contains the alias "minimal").
        matches = [term for term in matches if not any(
            term != other and normalize_text(term) in normalize_text(other)
            for other in matches
        )]
        for term in matches:
            support = tuple(
                doc.document_id for doc in trend.evidence
                if _contains_term(doc.excerpt or "", term)
            )
            found.append(ExtractedAttribute(attribute_type, term, "evidence_observation" if support else "hypothesis", support, 1.0 if support else None))
    # Deduplicate equivalent dictionary hits while preserving deterministic order.
    unique: dict[tuple[str, str], ExtractedAttribute] = {}
    for item in found:
        unique.setdefault((item.attribute_type, normalize_text(item.value)), item)
    return tuple(unique.values())


def _category_fit(trend_id: str | None, trend_name: str, category: Category, attributes: Sequence[ExtractedAttribute], evidence: Sequence[EvidenceAssessment], evidence_ids: tuple[str, ...], weights: TrendFitWeights | None) -> TrendCategoryEvaluation:
    verified_docs = [item for item in evidence if item.verified]
    verified_evidence_ids = {item.document_id for item in verified_docs}
    # An excerpt match from an unverified document must not become an input to
    # a scored category assessment merely because a different document in the
    # same batch happened to verify successfully.
    supported = [
        item for item in attributes
        if item.status == "evidence_observation"
        and item.evidence_ids
        and verified_evidence_ids.intersection(item.evidence_ids)
    ]
    limitations: list[str] = []
    taxonomy = category.taxonomy_version
    if not supported:
        return TrendCategoryEvaluation(trend_id, trend_name, category.category_id, "insufficient_evidence", None,
            {"attribute_relevance": None, "evidence_verification": None, "recency": None, "independence": None},
            evidence_ids, ("검증된 원문과 연결된 패션 속성이 없어 category fit 점수를 계산하지 않았습니다.",), EVALUATOR_VERSION, taxonomy)
    if not verified_docs:
        return TrendCategoryEvaluation(trend_id, trend_name, category.category_id, "insufficient_evidence", None,
            {"attribute_relevance": 0.0, "evidence_verification": 0.0, "recency": None, "independence": None},
            evidence_ids, ("문서 원문 검증기가 연결되지 않아 점수를 산출하지 않았습니다.",), EVALUATOR_VERSION, taxonomy)
    if not category.relevant_attribute_types:
        limitations.append("Category별 속성 taxonomy가 아직 연결되지 않아 적합도 점수는 보류했습니다.")
        return TrendCategoryEvaluation(trend_id, trend_name, category.category_id, "insufficient_evidence", None,
            {"attribute_relevance": None, "evidence_verification": 1.0, "recency": None, "independence": None},
            evidence_ids, tuple(limitations), EVALUATOR_VERSION, taxonomy)
    affinities = {normalize_text(key): value for key, value in category.attribute_value_affinities}
    relevant_types = {normalize_text(value) for value in category.relevant_attribute_types}
    applicable = [item for item in supported if normalize_text(item.attribute_type) in relevant_types]
    if not applicable:
        relevance = 0.0
    else:
        # A broad type match (e.g. every trend is "style") cannot establish
        # value-level product applicability. Do not silently treat a missing
        # taxonomy judgement as full affinity.
        missing_values = [
            f"{item.attribute_type}:{item.value}" for item in applicable
            if normalize_text(f"{item.attribute_type}:{item.value}") not in affinities
        ]
        if missing_values:
            limitations.append(
                "Category taxonomy에 속성값별 적합도 기준이 없어 점수를 보류했습니다: "
                + ", ".join(missing_values)
            )
            return TrendCategoryEvaluation(
                trend_id, trend_name, category.category_id, "unconfigured", None,
                {"attribute_relevance": None, "evidence_verification": len(verified_docs) / max(1, len(evidence)),
                 "recency": None, "independence": None},
                evidence_ids, tuple(limitations), EVALUATOR_VERSION, taxonomy,
            )
        values = [affinities[normalize_text(f"{item.attribute_type}:{item.value}")] for item in applicable]
        relevance = sum(values) / len(values)
    verification = len(verified_docs) / max(1, len(evidence))
    # No target-season validity window or independent-source resolver exists
    # yet. Avoid making up recency/independence values; score only configured,
    # observed components and identify the missing dimensions.
    if len(evidence) > 1 and len({item.independence_group for item in verified_docs if item.independence_group}) < len(verified_docs):
        limitations.append("근거의 독립 출처 그룹을 모두 확인할 수 없어 독립성 평가는 누락했습니다.")
    if any(item.published_at is None for item in evidence):
        limitations.append("발행일 미상 문서가 있어 최신성 평가는 누락했습니다.")
    if weights is None:
        limitations.append("Trend × Category 점수 가중치가 설정되지 않아 점수를 계산하지 않았습니다.")
        return TrendCategoryEvaluation(trend_id, trend_name, category.category_id, "unconfigured", None,
            {"attribute_relevance": relevance, "evidence_verification": verification, "recency": None, "independence": None},
            evidence_ids, tuple(limitations), EVALUATOR_VERSION, taxonomy)
    weight_sum = weights.attribute_relevance + weights.evidence_verification
    score = 0.0 if relevance == 0 else round(
        100 * (weights.attribute_relevance * relevance + weights.evidence_verification * verification) / weight_sum,
        2,
    )
    return TrendCategoryEvaluation(trend_id, trend_name, category.category_id, "ok", score,
        {"attribute_relevance": round(relevance, 4), "evidence_verification": round(verification, 4), "recency": None, "independence": None},
        evidence_ids, tuple((*limitations, f"fit weights: {weights.version}")), EVALUATOR_VERSION, taxonomy)


def evaluate_trend_batch(
    batch: TrendBatch,
    taxonomy: CategoryTaxonomy,
    evidence_resolver: EvidenceResolver | None = None,
    fit_weights: TrendFitWeights | None = None,
) -> tuple[EvaluatedTrend, ...]:
    """Evaluate each trend against each configured category without using input order."""
    resolver = evidence_resolver or MetadataOnlyEvidenceResolver()
    categories = tuple(taxonomy.categories())
    results: list[EvaluatedTrend] = []
    for trend in batch.trends:
        evidence = tuple(resolver.assess(doc) for doc in trend.evidence)
        attributes = extract_attributes(trend)
        evidence_ids = tuple(dict.fromkeys(doc.document_id for doc in trend.evidence))
        evaluations = tuple(_category_fit(trend.trend_id, trend.name, category, attributes, evidence, evidence_ids, fit_weights) for category in categories)
        limitations = tuple(dict.fromkeys(
            limitation
            for assessment in evidence for limitation in assessment.limitations
        ))
        results.append(EvaluatedTrend(
            batch_id=batch.batch_id,
            trend_id=trend.trend_id,
            original_name=trend.name,
            normalized_name=normalize_text(trend.name),
            description=trend.description,
            attributes=attributes,
            evidence=evidence,
            category_evaluations=evaluations,
            limitations=limitations,
            evaluator_version=EVALUATOR_VERSION,
            taxonomy_version=taxonomy.version,
        ))
    return tuple(results)
