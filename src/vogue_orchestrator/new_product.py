"""Migrated P06 PlanningDraft generation engine, sourced from Git commit 07e7d4d.

This module contains domain draft generation, evidence-aware RAG, text/image
workflow orchestration, and planning board rendering. Product persistence and
variant creation remain in Project_06.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .domain.context import (
    AgentResult, AttributeSignal, InventoryProduct, InventoryProductResult,
    InventoryTrendResult, PlanningInputs, PlanningRequest, TrendCandidate, TrendResult,
    build_planning_context,
)
from .domain.generator import ConceptGenerator
from .domain.retrieval import KnowledgeRetriever, RetrievalResult, RetrievalSourcePolicy
from .planning_settings import load_settings
from .workflows.image_generation import (
    DEFAULT_IMAGE_DIR, OpenAIImageEditProvider, OpenAIImageProvider,
    build_outfit_image_prompt, fetch_inventory_image, load_r2_inventory_image,
    run_concept_to_image, selected_outfit_inventory,
)
from .workflows.planning_retrieval import retrieve_planning_knowledge
from .workflows.planning_board import build_planning_board, render_planning_board_svg
from .workflows.text_concept_generation import PromptedConceptGenerator

GENERATED_IMAGE_ROOT = Path(__file__).resolve().parents[2] / DEFAULT_IMAGE_DIR
COMPARISON_IMAGE_MODELS = (
    "gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2",
)

def _r2_image_provider_upload_allowed() -> bool:
    return os.getenv("VOGUE_R2_IMAGE_ALLOW_PROVIDER_UPLOAD", "true").strip().casefold() == "true"

def _is_hm_kaggle_image(product) -> bool:
    """Identify the H&M Kaggle article-image layout used in R2."""
    article_id = getattr(product, "article_id", None)
    image_url = getattr(product, "image_url", None) or ""
    is_hm_kaggle = bool(
        (article_id and re.fullmatch(r"\d{10}", str(article_id)))
        or re.search(r"(?:^|/)\d{3}/\d{10}\.jpg(?:$|[?#])", image_url)
    )
    return is_hm_kaggle

class _UnavailableSyntheticRetriever:
    """Keep planning available while reporting a cloud-index outage explicitly."""

    def retrieve(self, query):
        return RetrievalResult(
            "error",
            limitations=("R2 기획 데이터베이스에 연결하지 못해 이번 초안에서 검색 근거를 사용하지 않았습니다.",),
            index_version="r2:unavailable",
            retrieval_run_id=f"r2-unavailable:{query.purpose}:{query.request_id or 'no-request-id'}",
        )

def _configure_synthetic_retrieval():
    """Select the configured R2 snapshot; runtime local fixture search is disabled."""
    if not _synthetic_planning_rag_enabled():
        return None, None, {"source": "disabled", "status": "not_configured"}
    source = os.getenv("VOGUE_SYNTHETIC_PLANNING_RAG_SOURCE", "r2").strip().casefold()
    from .sources.synthetic_workbench_retriever import PURPOSE_DOCUMENT_TYPES
    from .sources.r2_synthetic_workbench import R2SyntheticWorkbenchRetriever, WORKBENCH_DOCUMENT_TYPES

    try:
        if source == "r2":
            retriever = R2SyntheticWorkbenchRetriever()
        else:
            raise ValueError("unsupported source")
    except Exception:
        retriever = _UnavailableSyntheticRetriever()
        dataset_id = os.getenv("VOGUE_SYNTHETIC_PLANNING_DATASET_ID", "SYNTHETIC-PLANNING-WORKBENCH-SEED-20261110")
        status = {"source": source, "status": "unavailable", "dataset_id": dataset_id, "index_version": "r2:unavailable"}
        document_types = {purpose: tuple(sorted(types or WORKBENCH_DOCUMENT_TYPES))
                          for purpose, types in PURPOSE_DOCUMENT_TYPES.items()}
        return retriever, RetrievalSourcePolicy(
            source_types_by_purpose=document_types,
            dataset_id=dataset_id,
            allow_synthetic=True,
        ), status

    source_ids: dict[str, tuple[str, ...]] = {}
    source_types: dict[str, tuple[str, ...]] = {}
    from .synthetic_planning_workbench import document_eligibility
    for purpose, types in PURPOSE_DOCUMENT_TYPES.items():
        eligible = [
            doc for doc in retriever.dataset["documents"]
            if document_eligibility(retriever.dataset, doc)[0]
            and (types is None or doc.get("document_type") in types)
        ]
        source_ids[purpose] = tuple(dict.fromkeys(doc["source_id"] for doc in eligible))
        source_types[purpose] = tuple(sorted(types or {doc["document_type"] for doc in eligible}))
    return retriever, RetrievalSourcePolicy(
        source_ids_by_purpose=source_ids,
        source_types_by_purpose=source_types,
        dataset_id=retriever.dataset_id,
        allow_synthetic=True,
    ), {
        "source": source,
        "status": "ready",
        "dataset_id": retriever.dataset_id,
        "index_version": retriever.index_version,
    }

def build_planning_draft_response(
    payload: dict,
    *,
    knowledge_retriever: KnowledgeRetriever | None = None,
    retrieval_policy: RetrievalSourcePolicy | None = None,
    concept_generator: ConceptGenerator | None = None,
) -> dict:
    """Build a readable draft from the four upstream planning inputs."""
    settings = load_settings()  # Load .env before feature/source selection.
    planning_knowledge_source = {"source": "injected" if knowledge_retriever else "disabled", "status": "not_configured"}
    if knowledge_retriever is None and retrieval_policy is None:
        knowledge_retriever, retrieval_policy, planning_knowledge_source = _configure_synthetic_retrieval()
    request_payload = payload.get("planning_request")
    trend_payload = payload.get("trend_result")
    inventory_payload = payload.get("inventory_product_result")
    if not all(isinstance(value, dict) for value in (request_payload, trend_payload, inventory_payload)):
        raise ValueError("planning_request, trend_result, inventory_product_result JSON 객체가 필요합니다.")

    request_id = str(request_payload.get("request_id", payload.get("request_id", ""))).strip()[:160]
    schema = str(request_payload.get("schema_version", payload.get("schema_version", "1.0"))).strip()[:80]
    if not request_id or not schema:
        raise ValueError("planning_request.request_id와 schema_version이 필요합니다.")

    def evidence_ids_in(value) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "evidence_id" and isinstance(child, str) and child:
                    found.append(child)
                elif key in {"evidence_ids", "input_evidence_ids"}:
                    if isinstance(child, str) and child:
                        found.append(child)
                    elif isinstance(child, (list, tuple)):
                        found.extend(str(item) for item in child if item)
                else:
                    found.extend(evidence_ids_in(child))
        elif isinstance(value, (list, tuple)):
            for child in value:
                found.extend(evidence_ids_in(child))
        return list(dict.fromkeys(found))

    def envelope_evidence_ids(data: dict) -> tuple[str, ...]:
        return tuple(evidence_ids_in(data))

    def collect_list_values(data, keys: set[str]) -> tuple:
        found = []

        def visit(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in keys and isinstance(child, (list, tuple)):
                        found.extend(child)
                    elif key in keys and child is not None:
                        found.append(child)
                    visit(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child)

        visit(data)
        return tuple(found)

    def collect_limitations(data) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            str(item) for item in collect_list_values(data, {"limitations", "data_limitations"})
        ))

    def context_value(value):
        return value.get("value") if isinstance(value, dict) else value

    def context_origin(value) -> str:
        source = value.get("source") if isinstance(value, dict) else None
        return source if source in {"user", "inferred"} else "unknown"

    def attribute_signal(item: dict, *, requested: bool = False) -> AttributeSignal:
        evidence_ids = tuple(item.get("evidence_ids", ()))
        raw_origin = item.get("origin")
        if requested:
            origin = "user_requested" if item.get("source") == "user" else "unknown"
        else:
            origin = raw_origin if raw_origin in {"user_requested", "evidence_observation", "agent_proposal", "unknown"} else (
                "evidence_observation" if evidence_ids else "unknown"
            )
        return AttributeSignal(item["name"], item.get("value"), origin, evidence_ids)

    def attribute_signals(value, *, requested: bool = False) -> tuple[AttributeSignal, ...]:
        if isinstance(value, dict):
            normalized = []
            for name, item in value.items():
                if isinstance(item, dict):
                    normalized.append(attribute_signal({"name": name, **item}, requested=requested))
                elif isinstance(item, (list, tuple)):
                    normalized.extend(
                        attribute_signal({"name": name, "value": child}, requested=requested)
                        for child in item if child is not None
                    )
                else:
                    normalized.append(attribute_signal({"name": name, "value": item}, requested=requested))
            return tuple(normalized)
        return tuple(attribute_signal(item, requested=requested) for item in value or () if isinstance(item, dict))

    for name, data in (("trend_result", trend_payload), ("inventory_product_result", inventory_payload)):
        if data.get("request_id") != request_id:
            raise ValueError(f"{name}.request_id가 planning_request와 일치해야 합니다.")
        version = data.get("schema_version", schema)
        if version != schema:
            raise ValueError("입력 schema_version이 서로 일치해야 합니다.")

    def result_meta(data: dict, default_agent: str) -> AgentResult:
        status = data.get("status", "ok")
        if not isinstance(status, str) or status not in {"ok", "partial", "no_results", "error"}:
            raise ValueError("지원하지 않는 입력 상태입니다.")
        agent_id = data.get("agent_id", default_agent)
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("입력 producer agent_id가 비어 있거나 문자열이 아닙니다.")
        return AgentResult(
            agent_id=agent_id,
            request_id=str(data.get("request_id", request_id)),
            schema_version=str(data.get("schema_version", schema)),
            status=status,
            evidence_ids=envelope_evidence_ids(data),
            limitations=collect_limitations(data),
            producer_id_origin="payload" if "agent_id" in data else "adapter_assumption",
            source_schema_version=data.get("schema_version"),
            schema_version_origin="payload" if "schema_version" in data else "planning_request_fallback",
            status_origin="payload" if "status" in data else "adapter_default",
            errors=collect_list_values(data, {"errors"}),
            missing_data=collect_list_values(data, {"missing_data", "missing_fields", "missing_attributes"}),
        )

    raw_context = request_payload.get("context", {})
    raw_context = raw_context if isinstance(raw_context, dict) else {}
    request = PlanningRequest(
        result=result_meta(request_payload, "user-request"),
        original_query=request_payload.get("query", request_payload.get("original_query")),
        season=context_value(raw_context.get("season", request_payload.get("season"))),
        market=tuple(context_value(item) for item in raw_context.get("market", request_payload.get("market", ()))),
        channel=tuple(context_value(item) for item in raw_context.get("channel", request_payload.get("channel", ()))),
        category=context_value(raw_context.get("category", request_payload.get("category"))),
        product_role=context_value(raw_context.get("product_role", request_payload.get("product_role"))),
        target_customer=context_value(raw_context.get("target_customer", request_payload.get("target_customer"))),
        requested_attributes=attribute_signals(request_payload.get("requested_attributes", ()), requested=True),
        missing_fields=tuple(request_payload.get("missing_fields", ())),
        constraints=tuple(request_payload.get("constraints", ())),
        field_origins={
            key: context_origin(raw_context.get(key, request_payload.get(key)))
            for key in ("season", "category", "target_customer")
            if raw_context.get(key, request_payload.get(key)) is not None
        } | request_payload.get("field_origins", {}),
        field_evidence_ids={
            key: tuple((raw_context.get(key, request_payload.get(key)) or {}).get("evidence_ids", ()))
            for key in ("season", "category", "target_customer")
            if isinstance(raw_context.get(key, request_payload.get(key)), dict)
            and (raw_context.get(key, request_payload.get(key)) or {}).get("evidence_ids")
        },
    )
    def normalize_trend_candidates(data: dict) -> tuple[TrendCandidate, ...]:
        candidates = data.get("trend_candidates", data.get("candidates"))
        if candidates is not None:
            return tuple(TrendCandidate(
                name=item["name"], qualification=item.get("qualification", "candidate"),
                season=item.get("season"), market=tuple(item.get("market", ())),
                channel=tuple(item.get("channel", ())), category=item.get("category"),
                attributes=attribute_signals(item.get("attributes", ())),
                evidence_ids=tuple(item.get("evidence_ids", ())),
                description=item.get("description"),
                description_evidence_ids=tuple(item.get("description_evidence_ids", ())),
                source_style_id=item.get("id", item.get("style_id")),
            ) for item in candidates if isinstance(item, dict) and item.get("name"))

        normalized = []
        for trend in data.get("trends", ()):
            if not isinstance(trend, dict):
                continue
            style = trend.get("style") or {}
            if not isinstance(style, dict) or not style.get("name"):
                continue
            descriptions = style.get("description", ())
            description_texts = []
            description_evidence = []
            for item in descriptions:
                if isinstance(item, dict) and item.get("text"):
                    description_texts.append(str(item["text"]))
                    description_evidence.extend(item.get("evidence_ids", ()))
            signals = []
            item_evidence = []
            for product in trend.get("items", ()):
                if not isinstance(product, dict):
                    continue
                refs = tuple(str(value) for value in product.get("evidence_ids", ()) if value)
                item_evidence.extend(refs)
                for field_name in ("colors", "materials", "patterns"):
                    label = {"colors": "color", "materials": "material", "patterns": "pattern"}[field_name]
                    for value in product.get(field_name, ()) or ():
                        signals.append(AttributeSignal(label, value, "evidence_observation" if refs else "unknown", refs))
                for field_name in ("fit", "shape", "length"):
                    value = product.get(field_name)
                    if value:
                        signals.append(AttributeSignal(field_name, value, "evidence_observation" if refs else "unknown", refs))
                for detail in product.get("details", ()) or ():
                    if isinstance(detail, dict):
                        value = detail.get("name") or detail.get("raw")
                        if value:
                            signals.append(AttributeSignal("detail", value, "evidence_observation" if refs else "unknown", refs))
            refs = tuple(dict.fromkeys((
                *style.get("focus_evidence_ids", ()), *description_evidence, *item_evidence,
            )))
            seasons = tuple(style.get("seasons", ()) or ())
            normalized.append(TrendCandidate(
                name=str(style["name"]), qualification="trend_analysis_result",
                season=seasons[0] if len(seasons) == 1 else None,
                attributes=tuple(signals), evidence_ids=refs,
                description="; ".join(dict.fromkeys(description_texts)) or None,
                description_evidence_ids=tuple(dict.fromkeys(description_evidence)),
                source_style_id=str(style["id"]) if style.get("id") is not None else None,
            ))
        return tuple(normalized)

    trend = TrendResult(
        result=result_meta(trend_payload, "trend-analysis-agent"),
        candidates=normalize_trend_candidates(trend_payload),
    )
    def normalize_inventory_product(item: dict, source_index: int, evidence_ids: tuple[str, ...]) -> InventoryProduct:
        stock = item.get("inventory") or {}
        # Accept the stock-agent response surface, while retaining the complete
        # original object in input_evidence for fields outside the internal model.
        raw_sku_ids = item.get("sku_ids", ())
        sku_ids = tuple(str(value) for value in raw_sku_ids if value is not None) if isinstance(raw_sku_ids, (list, tuple)) else ()
        article_id = _stock_article_id(item)
        allowed = {
            "style_id": item.get("style_id", item.get("product_id")),
            "sku_ids": sku_ids,
            "article_id": article_id,
            "season": item.get("season"), "category": item.get("category"),
            "market": tuple(item.get("market", ())), "channel": tuple(item.get("channel", ())),
            "attributes": attribute_signals(item.get("attributes", ())),
            "quantity": stock.get("quantity", item.get("quantity")),
            "unit": stock.get("unit", item.get("unit")),
            "as_of": stock.get("as_of", item.get("as_of")),
            "inventory_definition": stock.get("definition", item.get("inventory_definition")),
            "evidence_ids": tuple(dict.fromkeys((*item.get("evidence_ids", ()), *evidence_ids))),
            "product_role": item.get("product_role"),
            "note": item.get("note", item.get("reason")),
            "note_origin": "unknown",
            "source_product_index": source_index,
            "image_url": (
                item.get("image", {}).get("url")
                if isinstance(item.get("image"), dict)
                and isinstance(item.get("image", {}).get("url"), str)
                else None
            ),
        }
        return InventoryProduct(**allowed)

    def normalize_inventory_trend_results(data: dict, product_values: list) -> tuple[tuple[InventoryTrendResult, ...], tuple[str, ...]]:
        raw_results = data.get("trend_results", ())
        if not isinstance(raw_results, (list, tuple)):
            return (), ("stock-agent trend_results가 배열이 아니어서 per-trend 연결을 해석하지 않았습니다.",)
        root_evidence = data.get("evidence", ())
        root_evidence = root_evidence if isinstance(root_evidence, (list, tuple)) else ()
        upstream_style_ids = {item.source_style_id for item in trend.candidates if item.source_style_id}
        links: list[InventoryTrendResult] = []
        issues: list[str] = []
        for trend_index, item in enumerate(raw_results):
            if not isinstance(item, dict):
                issues.append(f"stock-agent trend_results[{trend_index}]가 객체가 아니어서 매핑하지 않았습니다.")
                continue
            style = item.get("style") if isinstance(item.get("style"), dict) else {}
            style_id = str(style["id"]) if style.get("id") is not None else None
            style_name = str(style["name"]) if style.get("name") is not None else None
            raw_index = item.get("product_index")
            product_index = raw_index if isinstance(raw_index, int) and not isinstance(raw_index, bool) else None
            matched_product = None
            if product_index is None:
                mapping_status = "invalid_index"
                issues.append(f"stock-agent trend_results[{trend_index}]에 유효한 product_index가 없어 상품 연결을 확인할 수 없습니다.")
            elif product_index < 0 or product_index >= len(product_values):
                mapping_status = "out_of_range"
                issues.append(f"stock-agent trend_results[{trend_index}].product_index={product_index}가 products 범위를 벗어납니다.")
            elif not isinstance(product_values[product_index], dict):
                mapping_status = "no_product"
            else:
                mapping_status = "linked"
                matched_product = product_values[product_index]

            raw_declared_ids = item.get("evidence_ids", ())
            declared_ids = tuple(str(value) for value in raw_declared_ids if value) if isinstance(raw_declared_ids, (list, tuple)) else ()
            root_ids = tuple(dict.fromkeys(
                str(ref["evidence_id"])
                for ref in root_evidence
                if isinstance(ref, dict) and ref.get("evidence_id")
                and style_id and ref.get("style_id") == style_id
            ))
            evidence_ids = declared_ids or root_ids
            evidence_source = "trend_result" if declared_ids else "root_evidence_by_style" if root_ids else "none"
            trend_input_match = (
                "style_id_missing" if not style_id else
                "trend_input_empty" if not upstream_style_ids else
                "matched" if style_id in upstream_style_ids else "no_matching_trend"
            )
            if style_id and upstream_style_ids and trend_input_match == "no_matching_trend":
                issues.append(f"stock-agent style_id '{style_id}'가 전달된 trend_result의 style ID와 일치하지 않습니다. 기존 mapping key는 다를 수 있으므로 확인이 필요합니다.")
            if matched_product is None and evidence_ids:
                issues.append(f"stock-agent trend_results[{trend_index}] Evidence는 보존했지만 product_index의 상품은 연결되지 않았습니다.")
            if matched_product is not None and not evidence_ids:
                issues.append(f"stock-agent trend_results[{trend_index}] 상품 연결은 확인했지만 연결된 Evidence가 없습니다.")
            links.append(InventoryTrendResult(
                trend_result_index=trend_index,
                style_id=style_id,
                style_name=style_name,
                status=item.get("status") if isinstance(item.get("status"), str) else None,
                product_index=product_index,
                mapping_status=mapping_status,
                trend_input_match=trend_input_match,
                product_style_id=matched_product.get("style_id") if matched_product else None,
                rank=item.get("rank") if isinstance(item.get("rank"), int) and not isinstance(item.get("rank"), bool) else None,
                summary=item.get("summary") if isinstance(item.get("summary"), str) else None,
                evidence_ids=evidence_ids,
                evidence_reference_source=evidence_source,
                limitations=collect_limitations(item),
                errors=collect_list_values(item, {"errors"}),
                missing_data=collect_list_values(item, {"missing_data", "missing_fields", "missing_attributes"}),
            ))
        return tuple(links), tuple(dict.fromkeys(issues))

    raw_products = inventory_payload.get("products", ())
    if not isinstance(raw_products, (list, tuple)):
        raw_products = ()
        mapping_issues = ("stock-agent products가 배열이 아니어서 상품을 정규화하지 않았습니다. 원본 payload는 보존했습니다.",)
    else:
        mapping_issues = ()
    inventory_trend_results, link_issues = normalize_inventory_trend_results(inventory_payload, list(raw_products))
    mapping_issues = tuple(dict.fromkeys((*mapping_issues, *link_issues)))
    evidence_by_product: dict[int, list[str]] = {}
    for link in inventory_trend_results:
        if link.mapping_status == "linked" and link.product_index is not None:
            evidence_by_product.setdefault(link.product_index, []).extend(link.evidence_ids)
    normalized_products = tuple(
        normalize_inventory_product(item, index, tuple(dict.fromkeys(evidence_by_product.get(index, ()))))
        for index, item in enumerate(raw_products) if isinstance(item, dict)
    )

    inventory = InventoryProductResult(
        result=result_meta(inventory_payload, "stock-agent"),
        products=normalized_products,
        trend_results=inventory_trend_results,
        mapping_issues=mapping_issues,
    )
    sales = payload.get("sales_market_result")
    if sales is not None and not isinstance(sales, dict):
        raise ValueError("sales_market_result는 JSON 객체여야 합니다.")
    context = build_planning_context(PlanningInputs(request, trend, inventory, sales))
    planning_knowledge = retrieve_planning_knowledge(
        context, retriever=knowledge_retriever, policy=retrieval_policy,
    )
    context = replace(
        context,
        planning_knowledge=planning_knowledge,
        limitations=tuple(dict.fromkeys((*context.limitations, *planning_knowledge.limitations))),
    )
    # Text generation remains separately controlled; R2 retrieval is configured above.
    visual_references = None
    if (planning_knowledge_source.get("source") == "r2" and knowledge_retriever is not None
            and hasattr(knowledge_retriever, "find_hm_catalog_candidates")):
        from .sources.hm_visual_references import analyze_hm_visual_references
        visual_references = analyze_hm_visual_references(
            knowledge_retriever,
            category=request.category,
            api_key=settings.image_generation_api_key,
            max_references=max(1, min(int(os.getenv("VOGUE_HM_IMAGE_REFERENCE_LIMIT", "3")), 3)),
        )
    if visual_references and visual_references.get("limitations"):
        draft_data_limitations = visual_references["limitations"]
    else:
        draft_data_limitations = []
    context = replace(context, hm_visual_references=visual_references)
    if concept_generator is None:
        text_provider = None
        text_api_key = getattr(settings, "llm_api_key", None) or getattr(settings, "image_generation_api_key", None)
        if settings.text_generation_enabled and text_api_key:
            from .sources.openai_text_provider import OpenAIResponsesTextProvider
            text_provider = OpenAIResponsesTextProvider(
                text_api_key,
                model=os.getenv("VOGUE_TEXT_GENERATION_MODEL", "gpt-6-astra"),
            )
        generator = PromptedConceptGenerator(
            provider=text_provider,
            enabled=settings.text_generation_enabled,
        )
    else:
        generator = concept_generator
    draft, issues = generator.generate(context)
    generation_metadata = getattr(generator, "generation_metadata", {
        "engine": "rule_based", "status": "generated", "prompt_version": None, "model": None,
    })
    from dataclasses import asdict

    input_sources = {
        "planning_request": request_payload,
        "trend_result": trend_payload,
        "inventory_product_result": inventory_payload,
        "sales_market_result": sales,
    }
    normalized_results = {
        "planning_request": request.result,
        "trend_result": trend.result,
        "inventory_product_result": inventory.result,
    }
    input_evidence = {}
    for name, source in input_sources.items():
        if name in normalized_results:
            result_meta = normalized_results[name]
            metadata = {
                "producer_id": result_meta.agent_id,
                "producer_id_origin": result_meta.producer_id_origin,
                "source_schema_version": result_meta.source_schema_version,
                "schema_version_origin": result_meta.schema_version_origin,
                "internal_schema_version": result_meta.schema_version,
                "status_origin": result_meta.status_origin,
                "errors": list(result_meta.errors),
                "missing_data": list(result_meta.missing_data),
                "limitations": list(result_meta.limitations),
            }
        else:
            # Preserve Project_07's producer payload and provenance as supplied.
            metadata = {
                "producer_id": source.get("agent_id") if isinstance(source, dict) else None,
                "producer_id_origin": "payload" if isinstance(source, dict) and "agent_id" in source else "absent",
                "source_schema_version": source.get("schema_version") if isinstance(source, dict) else None,
                "schema_version_origin": "payload" if isinstance(source, dict) and "schema_version" in source else "absent",
                "status_origin": "payload" if isinstance(source, dict) and "status" in source else "absent",
            }
        input_evidence[name] = {
            "status": context.input_statuses[name],
            **metadata,
            "evidence_ids": evidence_ids_in(source) if source is not None else [],
            "payload": source,
        }

    draft_data = asdict(draft)
    draft_data["limitations"] = list(dict.fromkeys((
        *draft_data.get("limitations", ()), *planning_knowledge.limitations, *draft_data_limitations,
    )))
    draft_data["title"] = f"{draft.season or '시즌 미정'} {draft.category or '카테고리 미정'} 신제품 기획 초안"
    draft_data["review_status"] = "md_review_required"
    image_result = None
    outfit_result = None
    planning_board_result = None
    # The integration contract always requests both image stages. Runtime image
    # provider activation remains controlled by server settings.
    generate_image_requested = True
    if generate_image_requested:
        image_model = str(payload.get("image_model", "gpt-image-2.5-flare"))
        if image_model not in COMPARISON_IMAGE_MODELS:
            raise ValueError("지원하지 않는 이미지 모델입니다.")
        workflow = run_concept_to_image(
            context,
            draft=draft,
            provider=(OpenAIImageProvider(
                api_key=settings.image_generation_api_key or "",
                model=image_model,
            ) if settings.image_generation_api_key else None),
            image_generation_enabled=settings.image_generation_enabled,
            confirm_image_generation=True,
        )
        image_path = Path(workflow.image.image_path) if workflow.image.image_path else None
        image_name = image_path.name if image_path else None
        image_result = {
            "status": workflow.image.status,
            "model": workflow.image.model or image_model,
            "image_url": f"/api/generated-images/{image_name}" if image_name else None,
            "evidence_ids": list(workflow.image.request.evidence_ids),
            "evidence_uses": [asdict(item) for item in workflow.image.request.evidence_uses],
            "excluded_evidence": [asdict(item) for item in workflow.image.request.excluded_evidence],
            "limitations": list(workflow.image.limitations),
            "error_code": workflow.image.error_code,
            "review_only": workflow.image.review_only,
        }
        outfit_prompt = build_outfit_image_prompt(context)
        outfit_result = None
        if outfit_prompt and workflow.image.status == "generated" and image_path and image_path.is_file():
            try:
                selected_products = selected_outfit_inventory(context)
                references = [(image_path.read_bytes(), "image/png")]
                image_sources = []
                image_fetch_errors = []
                for product in selected_products[:3]:
                    if not product.image_url and not product.article_id:
                        image_fetch_errors.append("INVENTORY_IMAGE_MISSING")
                        image_sources.append({
                            "style_id": product.style_id,
                            "source_product_index": product.source_product_index,
                            "status": "missing",
                            "error_code": "INVENTORY_IMAGE_MISSING",
                        })
                        continue
                    try:
                        if product.image_url:
                            image_bytes, image_mime = fetch_inventory_image(product.image_url)
                        else:
                            if not _r2_image_provider_upload_allowed() and not _is_hm_kaggle_image(product):
                                image_fetch_errors.append("R2_IMAGE_PROVIDER_FORWARD_DISABLED")
                                image_sources.append({
                                    "style_id": product.style_id,
                                    "article_id": product.article_id,
                                    "source_product_index": product.source_product_index,
                                    "status": "not_forwarded",
                                    "error_code": "R2_IMAGE_PROVIDER_FORWARD_DISABLED",
                                })
                                continue
                            image_bytes, image_mime = load_r2_inventory_image(product.article_id)
                        references.append((image_bytes, image_mime))
                        image_sources.append({
                            "style_id": product.style_id,
                            "article_id": product.article_id,
                            "source_product_index": product.source_product_index,
                            "status": "loaded",
                            "mime_type": image_mime,
                            "source": "stock_agent_r2" if product.article_id and not product.image_url else "handoff_url",
                        })
                    except Exception as image_exc:
                        image_fetch_errors.append(getattr(image_exc, "code", "INVENTORY_IMAGE_UNAVAILABLE"))
                        image_sources.append({
                            "style_id": product.style_id,
                            "source_product_index": product.source_product_index,
                            "status": "unavailable",
                            "error_code": getattr(image_exc, "code", "INVENTORY_IMAGE_UNAVAILABLE"),
                        })
                outfit_generated = OpenAIImageEditProvider(
                    api_key=settings.image_generation_api_key or "",
                    model=image_model,
                ).generate_from_reference(
                    outfit_prompt,
                    tuple(references),
                    mime_type="image/png",
                )
                outfit_name = f"{image_path.stem}-outfit.png"
                outfit_path = GENERATED_IMAGE_ROOT / outfit_name
                outfit_path.parent.mkdir(parents=True, exist_ok=True)
                outfit_path.write_bytes(outfit_generated.image_bytes)
                outfit_result = {
                    "status": "generated", "model": outfit_generated.model,
                    "image_url": f"/api/generated-images/{outfit_name}",
                    "reference_image_url": image_result["image_url"],
                    "prompt": outfit_prompt,
                    "inventory_image_sources": image_sources,
                    "inventory_image_errors": list(dict.fromkeys(image_fetch_errors)),
                    "limitations": ["재고 아이템을 이용한 검토용 착장 시안이며 실제 재고 상태나 착장 선호를 보증하지 않습니다."],
                    "review_only": True,
                }
            except Exception as exc:
                outfit_result = {
                    "status": "failed", "model": image_model,
                    "error_code": getattr(exc, "code", "OUTFIT_IMAGE_GENERATION_FAILED"),
                    "limitations": ["착장 이미지 생성에 실패했으며 신제품 이미지는 유지됩니다."],
                    "review_only": True,
                }
        elif outfit_prompt:
            outfit_result = {
                "status": "blocked", "image_url": None,
                "limitations": ["신제품 이미지가 생성되지 않아 착장 이미지 생성을 건너뛰었습니다."],
                "review_only": True,
            }
        if workflow.image.status != "generated" or not image_path or not image_path.is_file():
            planning_board_result = {
                "status": "blocked",
                "image_url": None,
                "limitations": ("신제품 예시 이미지가 생성되지 않아 기획안 보드 생성을 건너뛰었습니다.",),
            }
        else:
            try:
                board = build_planning_board(context, draft)
                board_name = f"{image_path.stem}-planning-board.svg"
                board_path = GENERATED_IMAGE_ROOT / board_name
                board_path.parent.mkdir(parents=True, exist_ok=True)
                board_path.write_bytes(render_planning_board_svg(board, image_path))
                planning_board_result = {
                    "status": "generated",
                    "renderer": "deterministic-svg-v1",
                    "image_url": f"/api/generated-images/{board_name}",
                    "reference_image_url": image_result["image_url"],
                    "sections": [asdict(section) for section in board.sections],
                    "evidence_ids": list(board.evidence_ids),
                    "synthetic_context": board.synthetic_context,
                    "synthetic_dataset_ids": list(board.synthetic_dataset_ids),
                    "synthetic_notice": board.synthetic_notice,
                    "limitations": list(board.limitations),
                    "review_only": True,
                }
            except Exception as exc:
                error_code = getattr(exc, "code", "PLANNING_BOARD_GENERATION_FAILED")
                planning_board_result = {
                    "status": "failed",
                    "image_url": None,
                    "error_code": error_code,
                    "limitations": ["기획안 보드 생성에 실패했습니다. 신제품 이미지와 텍스트 초안은 유지됩니다."],
                    "review_only": True,
                }
    return {
        "status": "partial" if context.limitations or context.conflicts or context.input_statuses.get("sales_market_result") != "ok" else "ok",
        "request_id": context.request_id,
        "input_statuses": dict(context.input_statuses),
        "draft": draft_data,
        "generation_metadata": generation_metadata,
        "planning_knowledge_source": planning_knowledge_source,
        "planning_knowledge": _planning_knowledge_payload(planning_knowledge),
        "planning_analysis": _planning_analysis_payload(context, draft),
        "input_evidence": input_evidence,
        "stock_trend_results": [asdict(item) for item in inventory.trend_results],
        "image": image_result,
        "outfit_image": outfit_result,
        "hm_visual_references": visual_references,
        "planning_board": planning_board_result,
        "validation_issues": [asdict(issue) for issue in issues],
        "evidence_ids": list(context.evidence_ids),
        "limitations": list(context.limitations),
        "conflicts": [asdict(conflict) for conflict in context.conflicts],
        "questions_for_validation": list(context.questions_for_validation),
    }

def _synthetic_planning_rag_enabled() -> bool:
    """Planning knowledge retrieval is enabled by default and uses the R2 snapshot."""
    return os.getenv("VOGUE_SYNTHETIC_PLANNING_RAG_ENABLED", "true").strip().casefold() == "true"

def _planning_knowledge_payload(knowledge) -> dict:
    """Serialize permitted retrieval references without leaking date objects."""
    return {
        "by_purpose": [
            {
                "purpose": result.purpose,
                "status": result.status,
                "query_plan_version": result.query_plan_version,
                "query_trace": dict(result.query_trace),
                "limitations": list(result.limitations),
                "index_version": result.index_version,
                "retrieval_run_id": result.retrieval_run_id,
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "source_id": item.source_id,
                        "source_type": item.source_type,
                        "source_name": item.source_name,
                        "locator": item.locator,
                        "quote": item.quote,
                        "source_date": item.source_date.isoformat() if item.source_date else None,
                        "permission_status": item.permission_status,
                        "permitted_uses": list(item.permitted_uses),
                        "content_hash": item.content_hash,
                        "matched_by": item.matched_by,
                        "verified": item.verified,
                        "dataset_id": item.dataset_id,
                        "is_synthetic": item.is_synthetic,
                        "synthetic_notice": item.synthetic_notice,
                        "generator_version": item.generator_version,
                        "seed": item.seed,
                        "supports": list(item.supports),
                    }
                    for item in result.evidence
                ],
                "kg_paths": [
                    {
                        "nodes": list(path.nodes),
                        "relationships": list(path.relationships),
                        "taxonomy_version": path.taxonomy_version,
                        "evidence_ids": list(path.evidence_ids),
                    }
                    for path in result.kg_paths
                ],
            }
            for result in knowledge.by_purpose
        ],
        "evidence_ids": [item.evidence_id for item in knowledge.evidence],
        "synthetic": any(item.is_synthetic for item in knowledge.evidence),
        "synthetic_dataset_ids": sorted({item.dataset_id for item in knowledge.evidence if item.is_synthetic and item.dataset_id}),
        "synthetic_notice": next((item.synthetic_notice for item in knowledge.evidence if item.is_synthetic and item.synthetic_notice), None),
        "limitations": list(knowledge.limitations),
    }

def _planning_analysis_payload(context, draft) -> dict:
    """Separate retrieved observations from proposals and unresolved interpretation."""
    observations = []
    if context.planning_knowledge:
        seen = set()
        for result in context.planning_knowledge.by_purpose:
            if result.purpose == "evidence_lookup":
                continue
            for item in result.evidence:
                if item.evidence_id in seen:
                    continue
                seen.add(item.evidence_id)
                observations.append({
                    "claim_type": "synthetic_observation" if item.is_synthetic else "evidence_observation",
                    "purpose": result.purpose,
                    "text": item.quote or item.locator,
                    "supports": list(item.supports),
                    "evidence_ids": [item.evidence_id],
                    "source_id": item.source_id,
                    "source_name": item.source_name,
                    "source_type": item.source_type,
                    "source_locator": item.locator,
                    "content_hash": item.content_hash,
                    "permission_status": item.permission_status,
                    "is_synthetic": item.is_synthetic,
                    "dataset_id": item.dataset_id,
                    "synthetic_notice": item.synthetic_notice,
                    "matched_by": item.matched_by,
                })
    if context.sales_market_result and context.input_statuses.get("sales_market_result") in {"ok", "partial"}:
        def add_market_observations(value, path="sales_market_result"):
            if isinstance(value, dict):
                ids = value.get("evidence_ids", value.get("input_evidence_ids", ()))
                if isinstance(ids, str):
                    ids = [ids]
                for key, child in value.items():
                    if key in {"summary", "text", "finding", "observation", "interpretation", "label", "value"} and isinstance(child, (str, int, float)):
                        observations.append({
                            "claim_type": "producer_finding", "purpose": "sales_market",
                            "text": str(child), "evidence_ids": list(ids) if isinstance(ids, (list, tuple)) else [],
                            "source_id": "sales-market-agent", "source_locator": f"{path}.{key}",
                        })
                    elif key not in {"evidence", "evidence_id", "evidence_ids", "input_evidence_ids"}:
                        add_market_observations(child, f"{path}.{key}")
            elif isinstance(value, (list, tuple)):
                for index, child in enumerate(value[:50]):
                    add_market_observations(child, f"{path}[{index}]")
        add_market_observations(context.sales_market_result)
    visual_analysis = context.hm_visual_references or {}
    for reference in visual_analysis.get("references", []) if isinstance(visual_analysis, dict) else ():
        visual = reference.get("visual_observations") if isinstance(reference, dict) else None
        if isinstance(visual, dict):
            observations.append({
                "claim_type": "external_visual_reference",
                "purpose": "hm_visual_reference",
                "text": visual.get("visual_summary") or ", ".join(visual.get("visible_features", ())),
                "visible_colors": visual.get("visible_colors", []),
                "silhouette": visual.get("silhouette"),
                "visible_details": visual.get("visible_details", []),
                "reference_id": reference.get("reference_id"),
                "evidence_ids": [],
            })
    proposals = []
    for field_name, claim in (("opportunity", draft.opportunity), ("product_definition", draft.product_definition)):
        if claim.value:
            proposals.append({
                "claim_type": "design_proposal" if claim.kind == "proposal" else claim.kind,
                "field": field_name,
                "text": claim.value,
                "evidence_ids": list(claim.evidence_ids),
            })
    proposals.extend({
        "claim_type": "design_proposal" if attribute.kind == "proposal" else attribute.kind,
        "field": f"attributes.{attribute.name}",
        "text": str(attribute.value),
        "evidence_ids": list(attribute.evidence_ids),
    } for attribute in draft.attributes if attribute.value is not None)
    return {
        "input_facts": "외부 에이전트 관찰은 전달된 출처·범위와 함께 표시; 합성 R2 관찰과 구별",
        "observations": observations,
        "hm_visual_reference_analysis": visual_analysis,
        "interpretations": [],
        "proposals": proposals,
        "unresolved_questions": list(dict.fromkeys((*context.questions_for_validation, *draft.questions_for_validation))),
        "sales_market_details_interpreted": bool(context.sales_market_result and context.input_statuses.get("sales_market_result") in {"ok", "partial"}),
        "hm_visual_analysis_used": bool(visual_analysis.get("references")),
        "limitations": list(context.planning_knowledge.limitations) if context.planning_knowledge else [],
    }
