"""Technology-neutral lexical+dense retrieval merger using rank fusion only."""

from __future__ import annotations

from dataclasses import replace
from ..domain.retrieval import EvidenceRef, KGPath, KnowledgeRetriever, RetrievalQuery, RetrievalResult


class HybridKnowledgeRetriever:
    """Combine two injected retrievers without selecting a backend or using raw scores.

    The component adapters must apply the same query filters. Final permission
    and source validation remains the planning retrieval workflow's job.
    """

    def __init__(
        self,
        lexical: KnowledgeRetriever,
        dense: KnowledgeRetriever,
        *,
        rrf_k: int = 60,
    ):
        if rrf_k < 1:
            raise ValueError("rrf_k must be positive")
        self.lexical = lexical
        self.dense = dense
        self.rrf_k = rrf_k

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        candidate_limit = query.candidate_limit or min(500, max(query.limit, query.limit * 3))
        component_query = replace(query, limit=candidate_limit, candidate_limit=None)
        results = (
            self._retrieve_one("lexical", self.lexical, component_query),
            self._retrieve_one("dense", self.dense, component_query),
        )
        usable = [(name, result) for name, result in results if result.status in {"ok", "partial"}]
        scores: dict[str, float] = {}
        records: dict[str, EvidenceRef] = {}
        methods: dict[str, set[str]] = {}
        conflicts: set[str] = set()
        raw_paths: list[KGPath] = []
        for name, result in usable:
            raw_paths.extend(path for path in result.kg_paths if isinstance(path, KGPath))
            seen_in_component: set[str] = set()
            for rank, item in enumerate(result.evidence, start=1):
                if not isinstance(item, EvidenceRef) or not item.evidence_id:
                    continue
                if item.evidence_id in seen_in_component:
                    continue
                seen_in_component.add(item.evidence_id)
                previous = records.get(item.evidence_id)
                if previous and self._conflicting_identity(previous, item):
                    conflicts.add(item.evidence_id)
                    continue
                scores[item.evidence_id] = scores.get(item.evidence_id, 0.0) + 1.0 / (self.rrf_k + rank)
                if previous is None or (not previous.quote and item.quote):
                    records[item.evidence_id] = item
                methods.setdefault(item.evidence_id, set()).add(name)

        for evidence_id in conflicts:
            records.pop(evidence_id, None)
            scores.pop(evidence_id, None)
            methods.pop(evidence_id, None)
        ranked_ids = sorted(scores, key=lambda evidence_id: (-scores[evidence_id], evidence_id))
        fused = tuple(
            replace(records[evidence_id], matched_by="rrf:" + "+".join(sorted(methods[evidence_id])))
            for evidence_id in ranked_ids[:query.limit]
        )
        fused_ids = {item.evidence_id for item in fused}
        graph_paths: list[KGPath] = []
        seen_paths: set[tuple[tuple[str, ...], tuple[str, ...], str, tuple[str, ...]]] = set()
        rejected_paths = False
        for path in raw_paths:
            if not path.evidence_ids or not set(path.evidence_ids).issubset(fused_ids):
                rejected_paths = True
                continue
            key = (path.nodes, path.relationships, path.taxonomy_version, path.evidence_ids)
            if key not in seen_paths:
                graph_paths.append(path)
                seen_paths.add(key)

        statuses = [result.status for _, result in results]
        failures = [name for name, result in results if result.status in {"error", "not_configured"}]
        partials = [name for name, result in results if result.status == "partial"]
        limitations = [limitation for _, result in results for limitation in result.limitations]
        if failures:
            limitations.append("일부 검색 경로를 사용할 수 없어 나머지 경로 결과만 병합했습니다: " + ", ".join(failures) + ".")
        if partials:
            limitations.append("부분 결과를 반환한 검색 경로가 있습니다: " + ", ".join(partials) + ".")
        if conflicts:
            limitations.append("서로 다른 출처 식별 정보를 반환한 Evidence ID를 제외했습니다.")
        if rejected_paths:
            limitations.append("유효한 병합 Evidence로 추적할 수 없는 KG 관계를 제외했습니다.")

        if fused or graph_paths:
            status = "partial" if failures or partials or conflicts or rejected_paths else "ok"
        elif all(value == "not_configured" for value in statuses):
            status = "not_configured"
        elif all(value == "no_results" for value in statuses):
            status = "no_results"
        elif all(value == "error" for value in statuses):
            status = "error"
        elif conflicts or rejected_paths:
            status = "partial"
        elif any(value in {"error", "not_configured", "partial"} for value in statuses):
            status = "partial"
        else:
            status = "no_results"

        versions = [f"{name}={result.index_version}" for name, result in results if result.index_version]
        run_ids = [f"{name}={result.retrieval_run_id}" for name, result in results if result.retrieval_run_id]
        return RetrievalResult(
            status=status,
            evidence=fused,
            kg_paths=tuple(graph_paths),
            limitations=tuple(dict.fromkeys(limitations)),
            index_version=";".join(versions) or None,
            retrieval_run_id=";".join(run_ids) or None,
        )

    @staticmethod
    def _retrieve_one(name: str, retriever: KnowledgeRetriever, query: RetrievalQuery) -> tuple[str, RetrievalResult]:
        try:
            result = retriever.retrieve(query)
        except Exception:
            return name, RetrievalResult("error", limitations=(f"{name} retrieval failed; its results were discarded.",))
        if (
            not isinstance(result, RetrievalResult)
            or not isinstance(result.status, str)
            or result.status not in {"not_configured", "ok", "partial", "no_results", "error"}
            or not isinstance(result.evidence, (tuple, list))
            or not isinstance(result.kg_paths, (tuple, list))
        ):
            return name, RetrievalResult("error", limitations=(f"{name} retrieval returned an invalid response; its results were discarded.",))
        if result.status in {"error", "not_configured", "no_results"}:
            return name, replace(result, evidence=(), kg_paths=())
        evidence = tuple(item for item in result.evidence if isinstance(item, EvidenceRef))
        paths = tuple(item for item in result.kg_paths if isinstance(item, KGPath))
        rejected = len(evidence) != len(result.evidence) or len(paths) != len(result.kg_paths)
        return name, replace(
            result,
            status="partial" if rejected else result.status,
            evidence=evidence,
            kg_paths=paths,
            limitations=tuple((*result.limitations, *(("Malformed retrieval items were discarded.",) if rejected else ()))),
        )

    @staticmethod
    def _conflicting_identity(left: EvidenceRef, right: EvidenceRef) -> bool:
        for field in ("source_id", "locator", "content_hash"):
            a, b = getattr(left, field), getattr(right, field)
            if a and b and a != b:
                return True
        return False
