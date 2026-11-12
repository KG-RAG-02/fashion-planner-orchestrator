"""Explicit local retriever for the synthetic planning RAG demo corpus.

This adapter is deterministic and intended for demos/tests only. It does not
read from a production index or infer permission for real company documents.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from ..domain.retrieval import EvidenceRef, RetrievalQuery, RetrievalResult


PURPOSE_SOURCE_TYPES = {
    "evidence_lookup": {"supplied_input_evidence"},
    "planning_reference": {"approved_brand_guideline"},
    "precedent": {"approved_precedent"},
    "taxonomy": {"approved_taxonomy"},
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _contains(scope_values: tuple[str, ...], value: str) -> bool:
    return not scope_values or _norm(value) in {_norm(item) for item in scope_values}


class SyntheticCorpusRetriever:
    """Search an explicitly supplied synthetic JSON corpus with strict filters."""

    def __init__(self, corpus_path: str | Path):
        path = Path(corpus_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("synthetic") is not True or not isinstance(data.get("documents"), list):
            raise ValueError("Synthetic corpus must declare synthetic=true and contain documents.")
        if any(not isinstance(doc, dict) or doc.get("synthetic") is not True for doc in data["documents"]):
            raise ValueError("Every synthetic corpus document must explicitly declare synthetic=true.")
        required = {"document_id", "evidence_id", "source_id", "source_type", "permission_status", "permitted_uses", "verified"}
        seen_ids: set[str] = set()
        for doc in data["documents"]:
            missing = required - doc.keys()
            if missing:
                raise ValueError(f"Synthetic document is missing required fields: {', '.join(sorted(missing))}.")
            if doc["evidence_id"] in seen_ids:
                raise ValueError(f"Duplicate synthetic evidence_id: {doc['evidence_id']}.")
            seen_ids.add(doc["evidence_id"])
        self.index_version = str(data.get("index_version") or "synthetic-unversioned")
        self.documents = tuple(data["documents"])
        self.queries: list[RetrievalQuery] = []

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        self.queries.append(query)
        permitted_types = PURPOSE_SOURCE_TYPES.get(query.purpose)
        if permitted_types is None:
            return RetrievalResult("error", limitations=("Unsupported retrieval purpose.",), index_version=self.index_version)

        candidates: list[tuple[int, EvidenceRef]] = []
        for doc in self.documents:
            if not self._in_scope(doc, query, permitted_types):
                continue
            ref = self._to_evidence_ref(doc)
            score, matched_by = self._match(doc, query)
            if score <= 0:
                continue
            candidates.append((score, EvidenceRef(
                evidence_id=ref.evidence_id,
                source_id=ref.source_id,
                locator=ref.locator,
                quote=ref.quote,
                source_date=ref.source_date,
                access_status=ref.access_status,
                verified=ref.verified,
                source_type=ref.source_type,
                source_name=ref.source_name,
                permission_status=ref.permission_status,
                permitted_uses=ref.permitted_uses,
                content_hash=ref.content_hash,
                matched_by=matched_by,
            )))

        candidates.sort(key=lambda pair: (-pair[0], pair[1].evidence_id))
        evidence = tuple(ref for _, ref in candidates[:query.limit])
        return RetrievalResult(
            status="ok" if evidence else "no_results",
            evidence=evidence,
            limitations=("합성 fixture 검색 결과이며 운영 자료원이나 회사 승인 문서를 조회하지 않았습니다.",),
            index_version=self.index_version,
            retrieval_run_id=f"synthetic:{query.purpose}:{query.request_id or 'no-request-id'}",
        )

    @staticmethod
    def _in_scope(doc: dict[str, Any], query: RetrievalQuery, permitted_types: set[str]) -> bool:
        if doc.get("source_type") not in permitted_types:
            return False
        if query.allowed_source_ids and doc.get("source_id") not in query.allowed_source_ids:
            return False
        if query.allowed_source_types and doc.get("source_type") not in query.allowed_source_types:
            return False
        if doc.get("permission_status") != "authorized":
            return False
        if doc.get("verified") is not True:
            return False
        if "product_planning" not in doc.get("permitted_uses", ()):
            return False
        if query.purpose == "evidence_lookup":
            return bool(query.evidence_ids) and doc.get("evidence_id") in query.evidence_ids
        if doc.get("season") and query.season and _norm(doc["season"]) != _norm(query.season):
            return False
        if doc.get("category_id") and query.category_ids and not _contains(query.category_ids, doc["category_id"]):
            return False
        if doc.get("market") and query.market and not ({_norm(v) for v in doc["market"]} & {_norm(v) for v in query.market}):
            return False
        if doc.get("channel") and query.channel and not ({_norm(v) for v in doc["channel"]} & {_norm(v) for v in query.channel}):
            return False
        return True

    @staticmethod
    def _match(doc: dict[str, Any], query: RetrievalQuery) -> tuple[int, str | None]:
        if query.purpose == "evidence_lookup":
            return (1000, "exact_evidence_id") if doc.get("evidence_id") in query.evidence_ids else (0, None)

        text = _norm(" ".join((
            str(doc.get("title", "")), str(doc.get("text", "")),
            str(doc.get("category_id", "")), str(doc.get("source_name", "")),
            " ".join(str(value) for value in doc.get("attributes", ())),
        )))
        exact_terms = tuple(dict.fromkeys(_norm(value) for value in query.exact_terms if _norm(value)))
        terms = tuple(dict.fromkeys(
            _norm(value) for value in (*query.attribute_terms, *query.trend_ids)
            if _norm(value)
        ))
        exact_matches = [term for term in exact_terms if term in text]
        broad_matches = [term for term in terms if term in text and term not in exact_terms]
        matches = [*exact_matches, *broad_matches]
        if matches:
            return len(exact_matches) * 2 + len(broad_matches), "lexical:" + ",".join(matches)
        # Category-scoped guidance/taxonomy is still useful when no attribute
        # term is present in the synthetic text; scope itself is not a claim.
        if (not terms or query.purpose == "taxonomy") and query.category_ids and any(
            _norm(doc.get("category_id")) == _norm(cat) for cat in query.category_ids
        ):
            return 1, "category_scope"
        return 0, None

    @staticmethod
    def _to_evidence_ref(doc: dict[str, Any]) -> EvidenceRef:
        published = doc.get("published_at") or doc.get("observed_at") or doc.get("as_of")
        try:
            source_date = date.fromisoformat(published) if published else None
        except (TypeError, ValueError):
            source_date = None
        return EvidenceRef(
            evidence_id=str(doc["evidence_id"]),
            source_id=str(doc["source_id"]),
            locator=doc.get("locator"),
            quote=doc.get("text"),
            source_date=source_date,
            access_status=str(doc.get("permission_status", "unknown")),
            verified=doc.get("verified") is True,
            source_type=doc.get("source_type"),
            source_name=doc.get("source_name"),
            permission_status=str(doc.get("permission_status", "unknown")),
            permitted_uses=tuple(doc.get("permitted_uses", ())),
            content_hash=str(doc.get("content_hash") or hashlib.sha256(
                f"{doc.get('source_id', '')}\n{doc.get('locator', '')}\n{doc.get('text', '')}".encode("utf-8")
            ).hexdigest()),
            dataset_id=doc.get("dataset_id"),
            is_synthetic=doc.get("synthetic") is True or doc.get("is_synthetic") is True,
            synthetic_notice=doc.get("synthetic_notice"),
            generator_version=doc.get("generator_version"),
            seed=doc.get("seed"),
        )
