"""Deterministic RAG adapter for the explicitly synthetic planning workbench."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from ..domain.retrieval import EvidenceRef, RetrievalQuery, RetrievalResult
from ..synthetic_planning_workbench import document_eligibility, load_dataset, validate_dataset


PURPOSE_DOCUMENT_TYPES = {
    "evidence_lookup": None,
    "planning_reference": {"guideline", "season_brief"},
    "precedent": {"precedent"},
    "taxonomy": {"taxonomy"},
    "catalog_comparison": {"catalog_record", "product_specification", "product_development_record"},
    "assortment_relation": {"assortment_relation"},
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


class SyntheticWorkbenchRetriever:
    """Search catalog, guidance, precedent and taxonomy rows from one synthetic snapshot.

    The adapter refuses mixed or unmarked datasets and returns no result unless
    every query explicitly opts into the dataset namespace and synthetic lane.
    """

    def __init__(
        self,
        dataset_path: str | Path | None = None,
        *,
        dataset: dict[str, Any] | None = None,
        snapshot_sha256: str | None = None,
        index_version: str | None = None,
    ):
        if dataset is None:
            if dataset_path is None:
                raise ValueError("A dataset or explicit local dataset_path is required; runtime retrieval uses R2.")
            self.dataset, file_hash = load_dataset(Path(dataset_path))
            self.snapshot_sha256 = snapshot_sha256 or file_hash
        else:
            self.dataset = validate_dataset(dataset)
            canonical = json.dumps(dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.snapshot_sha256 = snapshot_sha256 or hashlib.sha256(canonical).hexdigest()
        self.dataset_id = str(self.dataset["dataset_id"])
        self.index_version = index_version or f"{self.dataset_id}:{self.snapshot_sha256[:16]}"
        self.queries: list[RetrievalQuery] = []
        self._evidence = {row["evidence_id"]: row for row in self.dataset.get("evidence", [])}
        self._products = {row["style_id"]: row for row in self.dataset.get("products", [])}
        self._documents = tuple(self.dataset.get("documents", []))
        self._aliases = {
            _norm(alias): row["category_id"]
            for row in self.dataset.get("taxonomy", [])
            for alias in row.get("aliases", [])
        }
        self.allowed_source_types_by_purpose = {
            purpose: tuple(sorted(types)) if types else tuple(sorted({doc.get("document_type", "") for doc in self._documents}))
            for purpose, types in PURPOSE_DOCUMENT_TYPES.items()
        }

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        self.queries.append(query)
        if not query.allow_synthetic or query.dataset_id != self.dataset_id:
            return RetrievalResult(
                "no_results",
                limitations=("합성 RAG 검색에는 명시적인 allow_synthetic와 일치하는 dataset_id가 필요합니다.",),
                index_version=self.index_version,
                retrieval_run_id=self._run_id(query),
            )
        document_types = PURPOSE_DOCUMENT_TYPES.get(query.purpose, set())
        if document_types is None:
            document_types = {doc.get("document_type", "") for doc in self._documents}
        if not document_types:
            return RetrievalResult("error", limitations=("지원하지 않는 합성 검색 목적입니다.",), index_version=self.index_version)

        candidates: list[tuple[int, str, EvidenceRef]] = []
        for document in self._documents:
            if query.purpose == "evidence_lookup" and not set(query.evidence_ids).intersection(document.get("evidence_ids", [])):
                continue
            if query.purpose != "evidence_lookup" and document.get("document_type") not in document_types:
                continue
            if query.allowed_source_ids and document.get("source_id") not in query.allowed_source_ids:
                continue
            if query.allowed_source_types and document.get("document_type") not in query.allowed_source_types:
                continue
            eligible, _ = document_eligibility(self.dataset, document)
            if not eligible:
                continue
            if not self._in_scope(document, query):
                continue
            score, matched_by = self._match(document, query)
            if score <= 0:
                continue
            for evidence_id in document.get("evidence_ids", []):
                if query.purpose == "evidence_lookup" and evidence_id not in query.evidence_ids:
                    continue
                evidence = self._evidence.get(evidence_id)
                if not evidence or evidence.get("verified") is not True:
                    continue
                if "product_planning" not in evidence.get("permitted_uses", []):
                    continue
                if query.purpose == "catalog_comparison" and query.attribute_terms:
                    # Attribute-scoped searches return only Evidence attached to
                    # the matching field/value; a color query must not inherit
                    # unrelated fit, material, or whole-record Evidence.
                    match = re.match(r"([A-Z0-9-]+):", str(document.get("body", "")))
                    product = self._products.get(match.group(1)) if match else None
                    linked_fields = [
                        support.removeprefix("product.attributes.")
                        for support in evidence.get("supports", [])
                        if isinstance(support, str) and support.startswith("product.attributes.")
                    ]
                    if not product or not linked_fields or not any(
                        field in product.get("attributes", {})
                        and any(term in _norm(field) or term in _norm(product["attributes"][field])
                                for term in query.attribute_terms if _norm(term))
                        for field in linked_fields
                    ):
                        continue
                candidates.append((score, evidence_id, self._to_ref(document, evidence, matched_by)))

        candidates.sort(key=lambda row: (-row[0], row[1]))
        deduplicated: dict[str, EvidenceRef] = {}
        for _, evidence_id, ref in candidates:
            deduplicated.setdefault(evidence_id, ref)
        selected = tuple(list(deduplicated.values())[:query.limit])
        return RetrievalResult(
            status="ok" if selected else "no_results",
            evidence=selected,
            limitations=(self.dataset["notice"],),
            index_version=self.index_version,
            retrieval_run_id=self._run_id(query),
        )

    def _in_scope(self, document: dict[str, Any], query: RetrievalQuery) -> bool:
        doc_season = document.get("season")
        if doc_season and _norm(doc_season) not in {"all_synthetic", "all"} and query.season:
            if _norm(doc_season) != _norm(query.season):
                return False
        doc_category = _norm(document.get("category_id"))
        query_categories = {_norm(value) for value in query.category_ids}
        query_categories.update(self._aliases[value] for value in tuple(query_categories) if value in self._aliases)
        if doc_category and doc_category not in {"all_synthetic", "all"} and query_categories:
            if doc_category not in query_categories:
                return False
        doc_role = _norm(document.get("product_role"))
        query_roles = {_norm(value) for value in query.product_roles}
        if doc_role and query_roles and doc_role not in query_roles:
            return False
        if query.purpose == "assortment_relation" and query_roles:
            # Relations are retrieved from the requested product's source role.
            body = _norm(document.get("body"))
            if not any(role in body for role in query_roles):
                return False
        return True

    @staticmethod
    def _match(document: dict[str, Any], query: RetrievalQuery) -> tuple[int, str | None]:
        searchable = _norm(" ".join((
            str(document.get("title", "")), str(document.get("body", "")),
            str(document.get("category_id", "")), str(document.get("product_role", "")),
            str(document.get("brand_line", "")), str(document.get("document_id", "")),
        )))
        exact = tuple(dict.fromkeys(_norm(value) for value in query.exact_terms if _norm(value)))
        broad = tuple(dict.fromkeys(_norm(value) for value in (*query.attribute_terms, *query.trend_ids) if _norm(value)))
        topic_hits = [term for term in broad if term in searchable]
        if broad and query.purpose not in {"taxonomy", "assortment_relation"} and not topic_hits:
            return 0, None
        exact_hits = [term for term in exact if term in searchable]
        broad_hits = [term for term in broad if term in searchable and term not in exact_hits]
        if exact_hits or broad_hits:
            return (len(exact_hits) * 2 + len(broad_hits), "lexical:" + ",".join((*exact_hits, *broad_hits)))
        if query.purpose in {"taxonomy", "assortment_relation"} or (not exact and not broad):
            return 1, "category_role_scope"
        return 0, None

    def _to_ref(self, document: dict, evidence: dict, matched_by: str) -> EvidenceRef:
        published = document.get("effective_from") or self.dataset.get("snapshot_date")
        try:
            source_date = date.fromisoformat(published) if published else None
        except (TypeError, ValueError):
            source_date = None
        quote = document.get("body")
        supports = tuple(str(value) for value in evidence.get("supports", ()))
        if document.get("document_type") in {"catalog_record", "product_specification"}:
            match = re.match(r"([A-Z0-9-]+):", str(document.get("body", "")))
            product = self._products.get(match.group(1)) if match else None
            attribute = next((value.rsplit(".", 1)[-1] for value in supports if value.startswith("product.attributes.")), None)
            if product and attribute in product.get("attributes", {}):
                quote = f"{product['style_id']}: {attribute}={product['attributes'][attribute]}"
        return EvidenceRef(
            evidence_id=evidence["evidence_id"],
            source_id=evidence["source_id"],
            locator=evidence.get("locator") or document.get("source_locator"),
            quote=quote,
            source_date=source_date,
            access_status="synthetic_only",
            verified=True,
            source_type=document.get("document_type"),
            source_name=document.get("title"),
            permission_status="synthetic_only",
            permitted_uses=tuple(evidence.get("permitted_uses", ())),
            content_hash=evidence.get("content_hash") or document.get("content_hash"),
            matched_by=matched_by,
            dataset_id=self.dataset_id,
            is_synthetic=True,
            synthetic_notice=self.dataset["notice"],
            generator_version=self.dataset["generator_version"],
            seed=self.dataset["seed"],
            supports=supports,
        )

    def _run_id(self, query: RetrievalQuery) -> str:
        payload = {
            "dataset": self.snapshot_sha256,
            "purpose": query.purpose,
            "request_id": query.request_id,
            "season": query.season,
            "categories": query.category_ids,
            "roles": query.product_roles,
            "terms": query.attribute_terms,
            "exact_terms": query.exact_terms,
            "source_ids": query.allowed_source_ids,
            "limit": query.limit,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
        return f"synthetic-workbench:{query.purpose}:{digest}"
