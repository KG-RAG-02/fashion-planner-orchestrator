"""Deterministic, contract-neutral concept draft generation.

The initial implementation uses transparent rules rather than an LLM. It
organizes supplied facts and proposes a review scaffold; it does not estimate
market demand, rank products, or make feasibility decisions.
"""

from __future__ import annotations

from typing import Protocol

from .concept import (
    ClaimKind,
    ProductConceptDraft,
    ProposedAttribute,
    SourcedClaim,
    ValidationIssue,
    validate_concept_draft,
)
from .context import AttributeSignal, ProductPlanningContext, TrendCandidate
from .analysis import analyze_opportunities, dimension_mismatches
from .styling import display_inventory_label


class ConceptGenerator(Protocol):
    """Plug-in boundary for deterministic or later evidence-grounded LLM generation."""

    def generate(
        self,
        context: ProductPlanningContext,
    ) -> tuple[ProductConceptDraft, tuple[ValidationIssue, ...]]: ...


def _input_usable(context: ProductPlanningContext, input_name: str) -> bool:
    return context.input_statuses.get(input_name) in {"ok", "partial"}


def _attribute_kind(signal: AttributeSignal) -> ClaimKind:
    if signal.origin == "user_requested":
        return "user_requested"
    if signal.origin == "evidence_observation" and signal.evidence_ids:
        return "evidence_observation"
    if signal.origin == "agent_proposal":
        return "proposal"
    if signal.value is None:
        return "unknown"
    return "to_validate"


def _usable_trend_candidates(
    context: ProductPlanningContext,
) -> tuple[TrendCandidate, ...]:
    """Return named, evidenced trends that match the normalized request."""
    if not (
        _input_usable(context, "trend_result")
        and _input_usable(context, "planning_request")
    ):
        return ()
    return tuple(
        candidate
        for candidate in context.trends.candidates
        if candidate.name.strip()
        and candidate.evidence_ids
        and not dimension_mismatches(context.request, candidate)
    )


def _trend_design_hypothesis(candidate: TrendCandidate) -> str | None:
    """Translate the supplied phrase into a clearly labeled review hypothesis.

    This deliberately supports only the explicit example phrase. Other trend
    names remain intact without guessing at their semantics.
    """
    normalized = candidate.name.strip().casefold().replace(" ", "")
    if "절제된" not in normalized or "맥시멀" not in normalized:
        return None
    observed_patterns = [
        str(signal.value)
        for signal in candidate.attributes
        if signal.value is not None
        and signal.name.strip().casefold() in {"pattern", "print", "패턴", "프린트"}
        and signal.evidence_ids
    ]
    if observed_patterns:
        pattern_text = ", ".join(dict.fromkeys(observed_patterns))
        return (
            "디자인 방향 가설: 절제된 베이스에 근거로 전달된 "
            f"{pattern_text}를 강조 요소로 제한적으로 배치하는 방향을 검토합니다. "
            "배치 비율과 실제 적용은 MD 확인이 필요합니다."
        )
    return (
        "디자인 방향 가설: 절제된 베이스와 강조 요소의 균형을 검토합니다. "
        "구체적인 강조 요소는 입력 근거에 없어 MD 확인이 필요합니다."
    )


def _korean_object_particle(value: str) -> str:
    """Return 을/를 for a Korean name, defaulting safely for other scripts."""
    if not value:
        return "을"
    codepoint = ord(value[-1])
    if 0xAC00 <= codepoint <= 0xD7A3:
        return "을" if (codepoint - 0xAC00) % 28 else "를"
    return "을"


def _proposed_attributes(context: ProductPlanningContext) -> tuple[ProposedAttribute, ...]:
    attributes: list[ProposedAttribute] = []
    positions: dict[tuple[str, str], int] = {}

    def add(signal: AttributeSignal, *, trend_signal: bool = False) -> None:
        if signal.value is None:
            return
        kind = _attribute_kind(signal)
        if trend_signal:
            kind = "evidence_observation" if signal.evidence_ids else "to_validate"
        attribute = ProposedAttribute(
            name=signal.name.strip(),
            value=signal.value,
            kind=kind,
            evidence_ids=signal.evidence_ids,
        )
        key = (attribute.name.casefold(), str(attribute.value))
        existing_index = positions.get(key)
        if existing_index is None:
            positions[key] = len(attributes)
            attributes.append(attribute)
            return

        existing = attributes[existing_index]
        evidence_ids = tuple(dict.fromkeys((*existing.evidence_ids, *attribute.evidence_ids)))
        # Preserve the strongest explicit provenance when the same value is
        # present in both the user request and an upstream trend result.
        priority = {
            "user_requested": 4,
            "evidence_observation": 3,
            "proposal": 2,
            "to_validate": 1,
            "unknown": 0,
            "interpretation": 0,
            "hypothesis": 0,
        }
        selected = attribute if priority[attribute.kind] > priority[existing.kind] else existing
        attributes[existing_index] = ProposedAttribute(
            name=selected.name,
            value=selected.value,
            kind=selected.kind,
            evidence_ids=evidence_ids,
        )

    # Keep the planning order: trend evidence, request scope, then own-product context.
    candidates = (
        context.trends.candidates
        if _input_usable(context, "trend_result")
        and _input_usable(context, "planning_request")
        else ()
    )
    for candidate in candidates:
        if dimension_mismatches(context.request, candidate):
            continue
        for signal in candidate.attributes:
            add(signal, trend_signal=True)
    if _input_usable(context, "planning_request"):
        for signal in context.request.requested_attributes:
            add(signal)
    if _input_usable(context, "inventory_product_result"):
        paired_indexes = {
            relation.inventory_product_index
            for relation in context.styling_relationships
        }
        for index, product in enumerate(context.inventory.products):
            # Companion-product attributes belong to the outfit context, not
            # to the requested new product's attribute list.
            if index in paired_indexes:
                continue
            for signal in product.attributes:
                add(signal, trend_signal=True)
    return tuple(attributes)


def _planning_reference_records(context: ProductPlanningContext) -> tuple[dict[str, object], ...]:
    """Expose approved retrieval results separately from product assertions."""
    if context.planning_knowledge is None:
        return ()
    records: dict[str, dict[str, object]] = {}
    for result in context.planning_knowledge.by_purpose:
        if result.purpose == "evidence_lookup":
            continue
        for item in result.evidence:
            records.setdefault(item.evidence_id, {
                "purpose": result.purpose,
                "evidence_id": item.evidence_id,
                "source_id": item.source_id,
                "source_type": item.source_type,
                "source_name": item.source_name,
                "locator": item.locator,
                "quote": item.quote,
                "source_date": item.source_date.isoformat() if item.source_date else None,
                "permission_status": item.permission_status,
                "permitted_uses": item.permitted_uses,
                "content_hash": item.content_hash,
                "matched_by": item.matched_by,
                "dataset_id": item.dataset_id,
                "is_synthetic": item.is_synthetic,
                "synthetic_notice": item.synthetic_notice,
                "generator_version": item.generator_version,
                "seed": item.seed,
                "supports": item.supports,
            })
    return tuple(records.values())


def _inventory_relationships(context: ProductPlanningContext) -> tuple[SourcedClaim, ...]:
    relationships: list[SourcedClaim] = []
    products = (
        context.inventory.products
        if _input_usable(context, "inventory_product_result")
        else ()
    )
    for product in products:
        if not product.evidence_ids:
            continue
        product_label = (
            product.style_id.strip()
            if product.style_id and product.style_id.strip()
            else display_inventory_label(product)
        )
        value = f"재고 상품 {product_label}이 상품·재고 조회 결과에 포함되어 있습니다."
        if product.quantity is not None:
            value += (
                f" 재고 스냅샷: {product.quantity} {product.unit or '(단위 미상)'}"
                f"; 정의: {product.inventory_definition or '미확인'}"
            )
        if product.as_of:
            value += f"; 기준 시점: {product.as_of}"
        if product.quantity is not None:
            value += ". 이 값은 조회 시점의 재고이며 수요나 판매 추세를 뜻하지 않습니다."
        else:
            value += "."
        relationships.append(
            SourcedClaim(
                value=value,
                kind="evidence_observation",
                evidence_ids=product.evidence_ids,
            )
        )
        for attribute in product.attributes:
            if attribute.value is None or not attribute.evidence_ids:
                continue
            relationships.append(
                SourcedClaim(
                    value=(
                        f"재고 상품 {product_label}의 확인된 속성: "
                        f"{attribute.name}={attribute.value}."
                    ),
                    kind="evidence_observation",
                    evidence_ids=attribute.evidence_ids,
                )
            )
    for relation in context.styling_relationships:
        if relation.inventory_product_index >= len(products):
            continue
        product = products[relation.inventory_product_index]
        target_role = "상의" if relation.requested_role == "upper_body" else "하의"
        inventory_role = "상의" if relation.inventory_role == "upper_body" else "하의"
        inventory_label = display_inventory_label(product)
        note = (
            f" 사용자 입력 메모는 ‘{product.note}’입니다 (출처: {product.note_origin}; 미확인 정보)."
            if product.note else ""
        )
        relationships.append(SourcedClaim(
            value=(
            f"코디 기획 제안: 요청 대상 {target_role}와 재고 맥락 {inventory_role} "
                f"({inventory_label})을 함께 검토합니다.{note} "
                "이 관계는 재고 수량, 재고 소진, 수요 또는 매출 효과의 근거가 아닙니다."
            ),
            kind="interpretation",
        ))
        for signal in product.attributes:
            if signal.value is None:
                continue
            relationships.append(SourcedClaim(
                value=(
                    f"코디 맥락의 재고 상품 속성: {signal.name}={signal.value}. "
                    "이는 요청 신상품의 속성이 아닙니다."
                ),
                kind="evidence_observation" if signal.evidence_ids else "to_validate",
                evidence_ids=signal.evidence_ids,
            ))
    return tuple(relationships)


def _product_definition(context: ProductPlanningContext) -> SourcedClaim:
    request_usable = _input_usable(context, "planning_request")
    parts = [
        value
        for value in (
            context.request.season if request_usable else None,
            context.request.category if request_usable else None,
        )
        if value
    ]
    role = context.request.product_role
    if not role and context.styling_relationships:
        role = context.styling_relationships[0].requested_role
    if request_usable and role:
        role_label = {
            "upper_body": "상의",
            "lower_body": "하의",
        }.get(role.strip().casefold(), role)
        parts.append(role_label)
    prefix = " ".join(parts) if parts else "신상품"
    requested_attributes = [
        f"{signal.name.strip()}={signal.value}"
        for signal in (context.request.requested_attributes if request_usable else ())
        if signal.value is not None
        and signal.name.strip()
        and signal.origin == "user_requested"
    ]
    evidenced_trends = _usable_trend_candidates(context)
    fragments = [f"{prefix} 콘셉트 검토 초안"]
    if evidenced_trends:
        fragments.append(
            "입력된 트렌드 방향: "
            + ", ".join(
                dict.fromkeys(
                    f"{candidate.name} ({candidate.qualification})"
                    for candidate in evidenced_trends
                )
            )
        )
        observed_trend_attributes = tuple(
            dict.fromkeys(
                f"{signal.name.strip()}={signal.value}"
                for candidate in evidenced_trends
                for signal in candidate.attributes
                if signal.value is not None and signal.evidence_ids
            )
        )
        if observed_trend_attributes:
            fragments.append(
                "트렌드 근거 관찰 속성: " + ", ".join(observed_trend_attributes)
            )
        descriptions = tuple(dict.fromkeys(
            candidate.description.strip()
            for candidate in evidenced_trends
            if candidate.description and candidate.description.strip()
            and candidate.description_evidence_ids
        ))
        if descriptions:
            fragments.append("트렌드 분석 설명(근거 연결): " + " / ".join(descriptions))
        for candidate in evidenced_trends:
            hypothesis = _trend_design_hypothesis(candidate)
            if hypothesis:
                fragments.append(hypothesis)
    if requested_attributes:
        fragments.append("요청 속성: " + ", ".join(dict.fromkeys(requested_attributes)))
    return SourcedClaim(
        value=". ".join(fragment.rstrip(".") for fragment in fragments) + ".",
        kind="proposal",
        evidence_ids=tuple(
            dict.fromkeys(
                evidence_id
                for candidate in evidenced_trends
                for evidence_id in (
                    *candidate.evidence_ids,
                    *candidate.description_evidence_ids,
                    *(evidence_id for signal in candidate.attributes for evidence_id in signal.evidence_ids),
                )
            )
        ),
    )


def _validation_questions(
    context: ProductPlanningContext,
    analysis_questions: tuple[str, ...] = (),
) -> tuple[str, ...]:
    questions = [*context.questions_for_validation, *analysis_questions]
    for input_name, status in context.input_statuses.items():
        if input_name == "sales_market_result" and status == "not_provided":
            questions.append("Project_07 판매·시장 결과가 제공되지 않아 판매·시장 주장은 초안에 반영하지 않았습니다.")
            continue
        if status in {"error", "no_results"}:
            questions.append(
                f"{input_name} 상태가 '{status}'이므로 해당 입력을 사용하지 않았습니다. 재조회 또는 조건 확인이 필요합니다."
            )
    if not context.request.season:
        questions.append("기획 시즌을 확인해 주세요.")
    if not context.request.category:
        questions.append("상품 카테고리를 확인해 주세요.")
    request_usable = _input_usable(context, "planning_request")
    request_values = {
        "season": context.request.season if request_usable else None,
        "category": context.request.category if request_usable else None,
        "market": context.request.market if request_usable else (),
        "channel": context.request.channel if request_usable else (),
    }
    for field_name, value in request_values.items():
        if value and context.request.field_origins.get(field_name, "unknown") == "unknown":
            questions.append(f"{field_name} 값의 출처(user/inferred)를 확인해 주세요.")
    if any(
        signal.value is not None and signal.origin == "unknown"
        for signal in (context.request.requested_attributes if request_usable else ())
    ):
        questions.append("출처가 확인되지 않은 요청 속성이 있어 MD 확인이 필요합니다.")
    questions.append("대상 고객과 착용 상황을 MD가 확인해 주세요.")
    questions.append("기존 자사 상품과의 차별화 방향은 상품기획 검토가 필요합니다.")
    if not _input_usable(context, "trend_result") or not context.trends.candidates:
        questions.append("근거가 연결된 트렌드 후보를 확인할 수 있는지 검토해 주세요.")
    elif not any(
        candidate.evidence_ids and candidate.name.strip()
        for candidate in (
            context.trends.candidates
            if _input_usable(context, "trend_result") and request_usable
            else ()
        )
    ):
        questions.append("트렌드 후보를 뒷받침하는 Evidence 참조를 확인해 주세요.")
    if not _input_usable(context, "inventory_product_result") or not context.inventory.products:
        questions.append("자사 상품 및 재고 자료의 범위와 조회 상태를 확인해 주세요.")
    if not any(
        product.style_id and product.evidence_ids
        for product in (
            context.inventory.products
            if _input_usable(context, "inventory_product_result")
            else ()
        )
    ):
        questions.append("자사 상품 관계를 뒷받침할 Evidence 연결을 확인해 주세요.")
    return tuple(dict.fromkeys(question for question in questions if question.strip()))


def generate_concept_draft(
    context: ProductPlanningContext,
) -> tuple[ProductConceptDraft, tuple[ValidationIssue, ...]]:
    """Build a review-only concept scaffold from already normalized inputs.

    The function deliberately does not infer customer demand, product
    differentiation, price, cost, production feasibility, or sales potential.
    The returned issues must be resolved or presented as limitations before a
    future wire adapter emits a shared ``ProductConcept`` response.
    """

    analysis = analyze_opportunities(context)
    analysis_evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for finding in analysis.findings
            for evidence_id in finding.statement.evidence_ids
        )
    )
    evidenced_trends = _usable_trend_candidates(context)
    trend_names = tuple(dict.fromkeys(candidate.name for candidate in evidenced_trends))
    trend_summary = (
        "입력 트렌드명 "
        + ", ".join(f"'{name}'" for name in trend_names)
        + "을 기획 검토 방향으로 반영했습니다."
        if trend_names
        else "근거 연결 트렌드명을 사용할 수 없습니다."
    )
    opportunity_summary = (
        (
            f"{trend_summary} 트렌드·자사 상품 및 재고 관계 {len(analysis.findings)}건을 "
            "검토 대상으로 구성했습니다. 이는 상품 채택 결론이 아니며 MD 검토가 필요합니다."
        )
        if analysis.findings
        else (
            f"{trend_summary} 트렌드명과 관찰 속성을 기획 콘셉트 가설로 정리했으며 "
            "MD 검토가 필요합니다. 이는 미래 수요나 상품 채택 결론이 아닙니다."
        )
        if evidenced_trends
        else "요청 범위에 적용할 수 있는 입력 근거가 부족해 구체적인 기획 기회를 판단하지 않았습니다."
    )
    draft = ProductConceptDraft(
        request_id=context.request_id,
        season=context.request.season if _input_usable(context, "planning_request") else None,
        category=context.request.category if _input_usable(context, "planning_request") else None,
        market=context.request.market if _input_usable(context, "planning_request") else (),
        channel=context.request.channel if _input_usable(context, "planning_request") else (),
        request_constraints=(
            context.request.constraints
            if _input_usable(context, "planning_request")
            else ()
        ),
        field_origins={
            **(
                context.request.field_origins
                if _input_usable(context, "planning_request")
                else {}
            ),
            **{
                field_name: (
                    context.request.field_origins.get(field_name, "unknown")
                    if _input_usable(context, "planning_request")
                    else "unknown"
                )
                for field_name in ("season", "category", "market", "channel")
            },
        },
        input_statuses=context.input_statuses,
        input_conflicts=context.conflicts,
        evidence_ids=context.evidence_ids,
        limitations=tuple(dict.fromkeys((*context.limitations, *analysis.limitations))),
        opportunity=SourcedClaim(
            value=opportunity_summary,
            kind="proposal" if analysis.findings or evidenced_trends else "to_validate",
            evidence_ids=tuple(
                dict.fromkeys(
                    (
                        *analysis_evidence_ids,
                        *(
                            evidence_id
                            for candidate in evidenced_trends
                            for evidence_id in candidate.evidence_ids
                        ),
                    )
                )
            ),
        ),
        target_customer=(
            SourcedClaim(
                value=context.request.target_customer,
                kind=(
                    "user_requested"
                    if context.request.field_origins.get("target_customer") == "user"
                    else "to_validate"
                ),
            )
            if context.request.target_customer and _input_usable(context, "planning_request")
            else SourcedClaim(value=None, kind="unknown")
        ),
        product_definition=_product_definition(context),
        attributes=_proposed_attributes(context),
        opportunity_findings=analysis.findings,
        relation_to_own_products=_inventory_relationships(context),
        differentiation=(),
        risks=tuple(
            SourcedClaim(value=limitation, kind="to_validate")
            for limitation in tuple(dict.fromkeys((*context.limitations, *analysis.limitations)))
        ),
        questions_for_validation=_validation_questions(context, analysis.questions),
        sales_market_result=context.sales_market_result,
        planning_references=_planning_reference_records(context),
    )
    return draft, validate_concept_draft(context, draft)


class RuleBasedConceptGenerator:
    """Current provider-free implementation of the concept generation port."""

    def generate(
        self,
        context: ProductPlanningContext,
    ) -> tuple[ProductConceptDraft, tuple[ValidationIssue, ...]]:
        return generate_concept_draft(context)
