"""Technology-neutral KG/RAG evidence access ports.

No production database is configured in this repository. Callers must provide
adapters backed by an authorized source; the empty adapter is explicit and
deterministic test adapters live alongside their tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Mapping, Protocol


RetrievalPurpose = Literal[
    "evidence_lookup", "planning_reference", "precedent", "taxonomy",
    "catalog_comparison", "assortment_relation",
]
QueryStatus = Literal["not_configured", "ok", "partial", "no_results", "error"]


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    source_id: str
    locator: str | None
    quote: str | None
    source_date: date | None
    access_status: str
    verified: bool
    source_type: str | None = None
    source_name: str | None = None
    permission_status: str = "unknown"
    permitted_uses: tuple[str, ...] = ()
    content_hash: str | None = None
    matched_by: str | None = None
    dataset_id: str | None = None
    is_synthetic: bool = False
    synthetic_notice: str | None = None
    generator_version: str | None = None
    seed: int | None = None
    supports: tuple[str, ...] = ()


@dataclass(frozen=True)
class KGPath:
    nodes: tuple[str, ...]
    relationships: tuple[str, ...]
    taxonomy_version: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalResult:
    status: QueryStatus
    evidence: tuple[EvidenceRef, ...] = ()
    kg_paths: tuple[KGPath, ...] = ()
    limitations: tuple[str, ...] = ()
    index_version: str | None = None
    retrieval_run_id: str | None = None


@dataclass(frozen=True)
class RetrievalQuery:
    trend_ids: tuple[str, ...] = ()
    category_ids: tuple[str, ...] = ()
    attribute_terms: tuple[str, ...] = ()
    allowed_source_ids: tuple[str, ...] = ()
    limit: int = 50
    purpose: RetrievalPurpose = "planning_reference"
    request_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    season: str | None = None
    market: tuple[str, ...] = ()
    channel: tuple[str, ...] = ()
    allowed_source_types: tuple[str, ...] = ()
    query_plan_version: str = "planning-query-plan-v1"
    exact_terms: tuple[str, ...] = ()
    candidate_limit: int | None = None
    product_roles: tuple[str, ...] = ()
    dataset_id: str | None = None
    allow_synthetic: bool = False

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 500:
            raise ValueError("retrieval limit must be between 1 and 500")
        if self.candidate_limit is not None and not self.limit <= self.candidate_limit <= 500:
            raise ValueError("candidate_limit must be between final limit and 500")
        if not self.query_plan_version.strip():
            raise ValueError("query_plan_version must not be empty")
        if self.purpose == "evidence_lookup" and not self.evidence_ids:
            raise ValueError("evidence_lookup requires explicit evidence_ids")
        if not (self.allowed_source_ids or self.allowed_source_types):
            raise ValueError("retrieval requires an explicit source allowlist")
        if any(not value.strip() for value in self.allowed_source_ids + self.allowed_source_types):
            raise ValueError("source allowlist values must not be empty")


class KnowledgeRetriever(Protocol):
    def retrieve(self, query: RetrievalQuery) -> RetrievalResult: ...


@dataclass(frozen=True)
class EmptyKnowledgeRetriever:
    """Explicit no-index adapter. It never pretends the corpus was queried."""

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        return RetrievalResult("not_configured", limitations=("KG/vector index is not configured; no retrieval was performed.",))


@dataclass(frozen=True)
class RetrievalSourcePolicy:
    """Explicit per-purpose source allowlists. Empty entries disable that route."""

    source_ids_by_purpose: Mapping[RetrievalPurpose, tuple[str, ...]] = field(default_factory=dict)
    source_types_by_purpose: Mapping[RetrievalPurpose, tuple[str, ...]] = field(default_factory=dict)
    limit_per_purpose: int = 10
    candidate_limit_per_purpose: int = 30
    dataset_id: str | None = None
    allow_synthetic: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.limit_per_purpose <= 100:
            raise ValueError("planning retrieval limit must be between 1 and 100")
        if not self.limit_per_purpose <= self.candidate_limit_per_purpose <= 500:
            raise ValueError("candidate retrieval limit must be between final limit and 500")


@dataclass(frozen=True)
class PurposeRetrieval:
    purpose: RetrievalPurpose
    status: QueryStatus
    evidence: tuple[EvidenceRef, ...] = ()
    kg_paths: tuple[KGPath, ...] = ()
    limitations: tuple[str, ...] = ()
    index_version: str | None = None
    retrieval_run_id: str | None = None
    query_plan_version: str = "planning-query-plan-v1"
    query_trace: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanningKnowledgeContext:
    """Supplemental, provenance-preserving results kept separate from inputs."""

    by_purpose: tuple[PurposeRetrieval, ...] = ()

    @property
    def limitations(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            limitation for result in self.by_purpose for limitation in result.limitations
        ))

    @property
    def evidence(self) -> tuple[EvidenceRef, ...]:
        found: dict[str, EvidenceRef] = {}
        for result in self.by_purpose:
            for item in result.evidence:
                found.setdefault(item.evidence_id, item)
        return tuple(found.values())
