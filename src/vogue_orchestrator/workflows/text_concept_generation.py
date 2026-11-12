"""Prompted concept generation with strict local validation and rule fallback.

The HTTP layer can inject the opt-in OpenAI Responses adapter. Offline tests
use fake providers; disabled or failed model calls retain the rule baseline.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from ..domain.concept import (
    ProductConceptDraft,
    ProposedAttribute,
    SourcedClaim,
    ValidationIssue,
    validate_concept_draft,
)
from ..domain.context import ProductPlanningContext
from ..domain.generator import ConceptGenerator, RuleBasedConceptGenerator
from ..domain.prompt_safety import serialize_untrusted_prompt_data
from ..domain.prompt_registry import load_prompt, prompt_version_id, render_prompt


PROMPT_VERSION = prompt_version_id("text_planning")
MAX_MODEL_OUTPUT_CHARS = 64_000
CLAIM_KINDS = {"user_requested", "evidence_observation", "proposal", "hypothesis", "unknown"}
PLANNING_SYSTEM_PROMPT = load_prompt("text_planning", "system")


class ConceptOutputValidationError(ValueError):
    """Safe validation failure carrying only a stable issue code."""

    def __init__(self, issue_code: str):
        super().__init__("structured concept output failed local validation")
        self.code = f"TEXT_OUTPUT_VALIDATION_{issue_code}"


class StructuredTextProvider(Protocol):
    """Provider seam. Implementations must expose no tools or side-effect APIs."""

    model_name: str

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> str | dict[str, Any]:
        """Return one JSON object; implementations must not log prompts or secrets."""


@dataclass(frozen=True)
class PlanningPrompt:
    version: str
    system_prompt: str
    user_prompt: str
    allowed_evidence_ids: frozenset[str]


def _usable(context: ProductPlanningContext, slot: str) -> bool:
    return context.input_statuses.get(slot) in {"ok", "partial"}


def _allowed_references(context: ProductPlanningContext) -> list[dict[str, object]]:
    allowed: list[dict[str, object]] = []
    if context.planning_knowledge is None:
        return allowed
    for result in context.planning_knowledge.by_purpose:
        if result.purpose not in {"planning_reference", "precedent", "taxonomy", "catalog_comparison", "assortment_relation"}:
            continue
        for item in result.evidence:
            if not item.verified:
                continue
            if item.permission_status == "authorized":
                pass
            elif not (item.is_synthetic and item.permission_status == "synthetic_only" and item.dataset_id):
                continue
            if "product_planning" not in item.permitted_uses:
                continue
            allowed.append({
                "purpose": result.purpose,
                "evidence_id": item.evidence_id,
                "source_id": item.source_id,
                "source_name": item.source_name,
                "quote": item.quote,
                "locator": item.locator,
                "source_date": item.source_date.isoformat() if item.source_date else None,
                "permission_status": item.permission_status,
                "permitted_uses": list(item.permitted_uses),
                "supports": list(item.supports),
                "dataset_id": item.dataset_id,
                "is_synthetic": item.is_synthetic,
                "synthetic_notice": item.synthetic_notice,
            })
    return allowed


def build_planning_prompt(context: ProductPlanningContext) -> PlanningPrompt:
    """Build separate trusted instructions and bounded JSON data for one request."""
    request_usable = _usable(context, "planning_request")
    trend_usable = _usable(context, "trend_result") and request_usable
    inventory_usable = _usable(context, "inventory_product_result")
    request = context.request
    trend_candidates = []
    if trend_usable:
        trend_candidates = [{
            "name": item.name,
            "qualification": item.qualification,
            "season": item.season,
            "market": list(item.market),
            "channel": list(item.channel),
            "category": item.category,
            "description": item.description,
            "evidence_ids": list(item.evidence_ids),
            "description_evidence_ids": list(item.description_evidence_ids),
            "attributes": [
                {"name": attr.name, "value": attr.value, "origin": attr.origin,
                 "evidence_ids": list(attr.evidence_ids)}
                for attr in item.attributes
            ],
        } for item in context.trends.candidates]
    products = []
    if inventory_usable:
        products = [{
            "style_id": item.style_id,
            "category": item.category,
            "season": item.season,
            "quantity": item.quantity,
            "unit": item.unit,
            "inventory_definition": item.inventory_definition,
            "as_of": item.as_of,
            "evidence_ids": list(item.evidence_ids),
            "attributes": [
                {"name": attr.name, "value": attr.value, "origin": attr.origin,
                 "evidence_ids": list(attr.evidence_ids)}
                for attr in item.attributes
            ],
        } for item in context.inventory.products]

    references = _allowed_references(context)
    sales_status = context.input_statuses.get("sales_market_result", "not_provided")
    sales_result = context.sales_market_result if sales_status in {"ok", "partial"} else None
    visual_references = None
    if context.hm_visual_references:
        # Keep only visual observations. Never send catalog metadata, URLs, or image bytes.
        visual_references = {
            "data_nature": "external_visual_reference_not_company_policy_or_sales_evidence",
            "status": context.hm_visual_references.get("status"),
            "references": [{
                "reference_id": item.get("reference_id"),
                "visual_summary": (item.get("visual_observations") or {}).get("visual_summary"),
                "visible_features": (item.get("visual_observations") or {}).get("visible_features", []),
                "visible_colors": (item.get("visual_observations") or {}).get("visible_colors", []),
                "silhouette": (item.get("visual_observations") or {}).get("silhouette"),
                "visible_details": (item.get("visual_observations") or {}).get("visible_details", []),
                "not_assessable": (item.get("visual_observations") or {}).get("not_assessable", []),
            } for item in context.hm_visual_references.get("references", [])
              if isinstance(item, dict) and isinstance(item.get("visual_observations"), dict)],
        }
    prompt_data = {
        "request_id": context.request_id,
        "input_statuses": dict(context.input_statuses),
        "request": ({
            "query": request.original_query,
            "season": request.season,
            "market": list(request.market),
            "channel": list(request.channel),
            "category": request.category,
            "product_role": request.product_role,
            "target_customer": request.target_customer,
            "constraints": list(request.constraints),
            "missing_fields": list(request.missing_fields),
            "requested_attributes": [
                {"name": item.name, "value": item.value, "origin": item.origin,
                 "evidence_ids": list(item.evidence_ids)}
                for item in request.requested_attributes
            ],
            "evidence_ids": list(request.result.evidence_ids),
        } if request_usable else None),
        "trend_candidates": trend_candidates,
        "trend_limitations": list(context.trends.result.limitations) if trend_usable else [],
        "inventory_products": products,
        "inventory_limitations": list(context.inventory.result.limitations) if inventory_usable else [],
        "sales_market_result": sales_result,
        "hm_visual_reference_analysis": visual_references,
        "retrieved_planning_references": references,
        "conflicts": [{"field": item.field, "values": dict(item.values), "message": item.message}
                      for item in context.conflicts],
        "limitations": list(context.limitations),
        "questions_for_validation": list(context.questions_for_validation),
    }
    serialized_data = serialize_untrusted_prompt_data(prompt_data)
    try:
        bounded_prompt_data = json.loads(serialized_data)
    except json.JSONDecodeError:
        bounded_prompt_data = {}

    evidence_ids: set[str] = set()

    def collect_ids(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"evidence_id", "evidence_ids", "description_evidence_ids"}:
                    if isinstance(child, str):
                        evidence_ids.add(child)
                    elif isinstance(child, list):
                        evidence_ids.update(item for item in child if isinstance(item, str))
                else:
                    collect_ids(child)
        elif isinstance(value, list):
            for child in value:
                collect_ids(child)

    collect_ids(bounded_prompt_data)
    user_prompt = render_prompt(
        "text_planning", "user", {"UNTRUSTED_PLANNING_INPUT_JSON": serialized_data},
    )
    return PlanningPrompt(PROMPT_VERSION, PLANNING_SYSTEM_PROMPT, user_prompt, frozenset(evidence_ids))


def _parse_json_object(value: str | dict[str, Any]) -> dict[str, Any]:
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result

    def reject_constant(_value):
        raise ValueError("invalid number")

    if isinstance(value, str):
        if len(value) > MAX_MODEL_OUTPUT_CHARS:
            raise ValueError("oversized output")
        try:
            value = json.loads(value, object_pairs_hook=unique_object, parse_constant=reject_constant)
        except (json.JSONDecodeError, TypeError, ValueError):
            raise ValueError("invalid output") from None
    if not isinstance(value, dict):
        raise ValueError("invalid output")
    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError):
        raise ValueError("invalid output") from None
    if len(encoded) > MAX_MODEL_OUTPUT_CHARS:
        raise ValueError("oversized output")
    return value


def _claim(value: object, *, path: str, allowed_ids: frozenset[str]) -> SourcedClaim:
    if not isinstance(value, dict) or set(value) != {"value", "kind", "evidence_ids"}:
        raise ValueError(f"invalid {path}")
    claim_value = value["value"]
    kind = value["kind"]
    ids = value["evidence_ids"]
    if claim_value is not None and (not isinstance(claim_value, str) or len(claim_value) > 3_000):
        raise ValueError(f"invalid {path}")
    if kind not in CLAIM_KINDS or not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        raise ValueError(f"invalid {path}")
    if any(item not in allowed_ids for item in ids):
        raise ValueError(f"invalid {path}")
    return SourcedClaim(claim_value, kind, tuple(dict.fromkeys(ids)))


def parse_concept_output(
    value: str | dict[str, Any],
    context: ProductPlanningContext,
    allowed_evidence_ids: frozenset[str],
) -> ProductConceptDraft:
    """Validate strict model JSON and map it to the existing internal draft."""
    data = _parse_json_object(value)
    required = {
        "request_id", "opportunity", "target_customer", "product_definition", "attributes",
        "relation_to_own_products", "differentiation", "risks", "questions_for_validation", "limitations",
    }
    if set(data) != required or data.get("request_id") != context.request_id:
        raise ValueError("invalid output")
    opportunity = _claim(data["opportunity"], path="opportunity", allowed_ids=allowed_evidence_ids)
    target_customer = _claim(data["target_customer"], path="target_customer", allowed_ids=allowed_evidence_ids)
    product_definition = _claim(data["product_definition"], path="product_definition", allowed_ids=allowed_evidence_ids)
    direct_user_values = {
        value for value in (
            context.request.original_query, context.request.season, context.request.category,
            context.request.target_customer, *context.request.market, *context.request.channel,
            *context.request.constraints,
        ) if value
    }
    for claim in (opportunity, target_customer, product_definition):
        if claim.kind == "user_requested" and claim.value not in direct_user_values:
            raise ValueError("invalid output")

    raw_attributes = data["attributes"]
    if not isinstance(raw_attributes, list) or len(raw_attributes) > 50:
        raise ValueError("invalid output")
    attributes: list[ProposedAttribute] = []
    for item in raw_attributes:
        if not isinstance(item, dict) or set(item) != {"name", "value", "kind", "evidence_ids"}:
            raise ValueError("invalid output")
        name, attr_value, kind, ids = item["name"], item["value"], item["kind"], item["evidence_ids"]
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("invalid output")
        if attr_value is not None and not isinstance(attr_value, (str, int, float, bool)):
            raise ValueError("invalid output")
        if isinstance(attr_value, float) and not math.isfinite(attr_value):
            raise ValueError("invalid output")
        if isinstance(attr_value, str) and len(attr_value) > 1_000:
            raise ValueError("invalid output")
        if kind not in CLAIM_KINDS or not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            raise ValueError("invalid output")
        if any(item_id not in allowed_evidence_ids for item_id in ids):
            raise ValueError("invalid output")
        attributes.append(ProposedAttribute(name, attr_value, kind, tuple(dict.fromkeys(ids))))

    def claims(key: str) -> tuple[SourcedClaim, ...]:
        values = data[key]
        if not isinstance(values, list) or len(values) > 30:
            raise ValueError("invalid output")
        return tuple(_claim(item, path=key, allowed_ids=allowed_evidence_ids) for item in values)

    def text_list(key: str, max_count: int = 30) -> tuple[str, ...]:
        values = data[key]
        if not isinstance(values, list) or len(values) > max_count:
            raise ValueError("invalid output")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 1_000 for item in values):
            raise ValueError("invalid output")
        return tuple(dict.fromkeys(values))

    planning_references = tuple(
        {
            "evidence_id": item["evidence_id"],
            "source_id": item["source_id"],
            "source_name": item["source_name"],
            "locator": item["locator"],
            "quote": item["quote"],
            "permission_status": item["permission_status"],
            "permitted_uses": tuple(item["permitted_uses"]),
            "supports": tuple(item["supports"]),
            "dataset_id": item["dataset_id"],
            "is_synthetic": item["is_synthetic"],
            "synthetic_notice": item["synthetic_notice"],
        }
        for item in _allowed_references(context)
    )
    request = context.request
    planning_usable = _usable(context, "planning_request")
    draft = ProductConceptDraft(
        request_id=context.request_id,
        season=request.season if planning_usable else None,
        category=request.category if planning_usable else None,
        market=request.market if planning_usable else (),
        channel=request.channel if planning_usable else (),
        request_constraints=request.constraints if planning_usable else (),
        field_origins=dict(request.field_origins) if planning_usable else {},
        input_statuses=dict(context.input_statuses),
        input_conflicts=context.conflicts,
        evidence_ids=tuple(sorted(allowed_evidence_ids)),
        limitations=tuple(dict.fromkeys((*context.limitations, *text_list("limitations")))),
        opportunity=opportunity,
        target_customer=target_customer,
        product_definition=product_definition,
        attributes=tuple(attributes),
        relation_to_own_products=claims("relation_to_own_products"),
        differentiation=claims("differentiation"),
        risks=claims("risks"),
        questions_for_validation=tuple(dict.fromkeys((
            *context.questions_for_validation,
            *text_list("questions_for_validation"),
        ))),
        sales_market_result=context.sales_market_result,
        planning_references=planning_references,
    )
    issues = validate_concept_draft(context, draft)
    if issues:
        raise ConceptOutputValidationError(issues[0].code)
    return draft


@dataclass
class PromptedConceptGenerator:
    """Opt-in structured generation adapter; deterministic fallback by default."""

    provider: StructuredTextProvider | None = None
    enabled: bool = False
    fallback: ConceptGenerator | None = None
    generation_status: str = field(default="not_requested", init=False)
    generation_error_code: str | None = field(default=None, init=False)

    @property
    def generation_metadata(self) -> dict[str, str | None]:
        attempted = self.enabled
        return {
            "engine": "structured_text" if self.generation_status == "generated" else "rule_based",
            "status": self.generation_status,
            "prompt_version": PROMPT_VERSION if attempted else None,
            "model": getattr(self.provider, "model_name", None) if attempted else None,
            "error_code": self.generation_error_code,
        }

    def generate(self, context: ProductPlanningContext) -> tuple[ProductConceptDraft, tuple[ValidationIssue, ...]]:
        fallback = self.fallback or RuleBasedConceptGenerator()
        if not self.enabled:
            self.generation_status = "disabled"
            self.generation_error_code = None
            return fallback.generate(context)
        if self.provider is None:
            self.generation_status = "unavailable"
            self.generation_error_code = "TEXT_API_NOT_CONFIGURED"
            safe_draft, base_issues = fallback.generate(context)
            safe_draft = replace(
                safe_draft,
                limitations=tuple(dict.fromkeys((
                    *safe_draft.limitations,
                    "텍스트 생성 기능은 요청되었지만 승인된 모델 제공자가 없어 규칙 기반 초안을 사용했습니다.",
                ))),
            )
            issue = ValidationIssue(
                path="generation",
                code="TEXT_GENERATION_UNAVAILABLE",
                message="승인된 텍스트 생성 제공자가 구성되지 않아 규칙 기반 초안을 반환했습니다.",
            )
            return safe_draft, (*base_issues, issue)
        prompt = build_planning_prompt(context)
        try:
            raw = self.provider.generate_json(
                system_prompt=prompt.system_prompt,
                user_prompt=prompt.user_prompt,
            )
            draft = parse_concept_output(raw, context, prompt.allowed_evidence_ids)
            self.generation_status = "generated"
            self.generation_error_code = None
            return draft, ()
        except Exception as exc:
            self.generation_status = "fallback"
            self.generation_error_code = getattr(exc, "code", "TEXT_OUTPUT_INVALID")
            safe_draft, base_issues = fallback.generate(context)
            safe_draft = replace(
                safe_draft,
                limitations=tuple(dict.fromkeys((
                    *safe_draft.limitations,
                    "텍스트 생성 응답을 검증하지 못해 결정적 규칙 기반 초안을 사용했습니다.",
                ))),
            )
            issue = ValidationIssue(
                path="generation",
                code="TEXT_GENERATION_FALLBACK",
                message="텍스트 생성 결과를 검증하지 못해 규칙 기반 초안으로 대체했습니다.",
            )
            return safe_draft, (*base_issues, issue)
