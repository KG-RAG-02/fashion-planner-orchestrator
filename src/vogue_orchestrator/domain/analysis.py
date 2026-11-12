"""Conservative, evidence-only comparisons for internal concept planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .concept import OpportunityFinding, SourcedClaim
from .context import (
    AttributeSignal,
    InventoryProduct,
    PlanningRequest,
    ProductPlanningContext,
    TrendCandidate,
)
from .styling import display_inventory_label


@dataclass(frozen=True)
class OpportunityAnalysis:
    """Internal comparison results; not a market-gap or demand assessment."""

    findings: tuple[OpportunityFinding, ...] = ()
    questions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


def _key(value: object) -> str:
    return str(value).strip().casefold()


def dimension_mismatches(
    request: PlanningRequest,
    item: TrendCandidate | InventoryProduct,
) -> tuple[str, ...]:
    """Return populated request dimensions that contradict one input item."""

    mismatches: list[str] = []
    for field_name in ("season", "category"):
        requested = getattr(request, field_name)
        observed = getattr(item, field_name)
        if requested and observed and _key(requested) != _key(observed):
            mismatches.append(field_name)

    for field_name in ("market", "channel"):
        requested_values = {_key(value) for value in getattr(request, field_name)}
        observed_values = {_key(value) for value in getattr(item, field_name)}
        if requested_values and observed_values and requested_values.isdisjoint(observed_values):
            mismatches.append(field_name)
    return tuple(mismatches)


def _evidence_attributes(signals: Iterable[AttributeSignal]) -> tuple[AttributeSignal, ...]:
    return tuple(
        signal
        for signal in signals
        if signal.name.strip() and signal.value is not None and signal.evidence_ids
    )


def _requested_values(request: PlanningRequest) -> dict[str, set[str]]:
    values: dict[str, set[str]] = {}
    for signal in request.requested_attributes:
        if signal.name.strip() and signal.value is not None and signal.origin == "user_requested":
            values.setdefault(_key(signal.name), set()).add(_key(signal.value))
    return values


def analyze_opportunities(context: ProductPlanningContext) -> OpportunityAnalysis:
    """Compare trend and own-catalog evidence without inferring a market gap.

    Only evidence-backed values on dimension-compatible inputs are compared.
    Missing catalog attributes are not treated as absent products because the
    shared contract has not defined query coverage or catalog completeness.
    """

    findings: list[OpportunityFinding] = []
    questions: list[str] = []
    limitations: list[str] = []
    trend_status = context.input_statuses.get("trend_result")
    inventory_status = context.input_statuses.get("inventory_product_result")
    request_status = context.input_statuses.get("planning_request")

    if request_status not in {"ok", "partial"}:
        limitations.append(
            f"PlanningRequest input status is '{request_status}'; request-dependent comparisons were not analyzed."
        )
    if trend_status in {"error", "no_results"}:
        limitations.append(f"TrendResult input status is '{trend_status}'; no trend claims were analyzed.")
    if inventory_status in {"error", "no_results"}:
        limitations.append(
            f"InventoryProductResult input status is '{inventory_status}'; "
            "no own-catalog comparisons were analyzed."
        )

    request_usable = request_status in {"ok", "partial"}
    candidates = (
        context.trends.candidates
        if request_usable and trend_status in {"ok", "partial"}
        else ()
    )
    products = (
        context.inventory.products
        if request_usable
        and context.inventory is not None
        and inventory_status in {"ok", "partial"}
        else ()
    )
    eligible_candidates: list[TrendCandidate] = []
    eligible_products: list[InventoryProduct] = []
    trend_findings: list[OpportunityFinding] = []

    # Establish the evidence-backed trend direction first, then check it
    # against the user's requested scope without silently overriding either.
    requested_values = _requested_values(context.request)
    for candidate in candidates:
        mismatches = dimension_mismatches(context.request, candidate)
        if mismatches:
            questions.append(
                f"트렌드 후보 '{candidate.name or '(이름 없음)'}'의 "
                f"{', '.join(mismatches)}가 요청 조건과 달라 기획 근거로 사용하지 않았습니다."
            )
            continue
        if not candidate.name.strip() or not candidate.evidence_ids:
            questions.append("이름 또는 Evidence가 부족한 트렌드 후보는 비교에서 제외했습니다.")
            continue
        eligible_candidates.append(candidate)
        for attribute in _evidence_attributes(candidate.attributes):
            trend_findings.append(
                OpportunityFinding(
                    statement=SourcedClaim(
                        value=(
                            f"근거 연결 트렌드 후보 '{candidate.name}'에서 "
                            f"{attribute.name}={attribute.value}가 관찰되었습니다."
                        ),
                        kind="evidence_observation",
                        evidence_ids=attribute.evidence_ids,
                    ),
                    trend_name=candidate.name,
                    attribute_name=attribute.name,
                )
            )

            requested = requested_values.get(_key(attribute.name), set())
            if requested and _key(attribute.value) not in requested:
                questions.append(
                    f"사용자 요청의 {attribute.name} 값과 트렌드 후보 "
                    f"'{candidate.name}'의 근거 속성 값이 다릅니다. "
                    "두 입력을 임의로 조정하지 않았으니 적용 방향을 확인해 주세요."
                )
    findings.extend(trend_findings)

    # Own-product and stock evidence is considered after trend evidence.
    relation_by_index = {
        relation.inventory_product_index: relation
        for relation in context.styling_relationships
    }
    for product_index, product in enumerate(products):
        relation = relation_by_index.get(product_index)
        mismatches = dimension_mismatches(context.request, product)
        if relation:
            mismatches = tuple(field for field in mismatches if field != "category")
        if mismatches:
            questions.append(
                f"자사 상품 '{product.style_id or '(Style ID 없음)'}'의 "
                f"{', '.join(mismatches)}가 요청 조건과 달라 비교에서 제외했습니다."
            )
            continue
        if relation:
            target_label = _role_label(relation.requested_role)
            inventory_label = _role_label(relation.inventory_role)
            item_label = display_inventory_label(product)
            note_text = (
                f" 재고 입력 메모는 ‘{product.note}’ (출처: {product.note_origin}; "
                "수량·기준일·Evidence 미확인)입니다."
                if product.note else ""
            )
            relationship_question = (
                "분류 규칙상 함께 코디할 수 있는 역할로 연결했습니다. 실제 착용 적합성과 MD 상품 방향을 확인해 주세요."
            )
            relationship_limitations = (
                f"규칙 {relation.rule_version}; 역할 분류 출처: {relation.request_role_source}, {relation.inventory_role_source}.",
                "코디 관계는 규칙 기반 제안이며 실제 스타일 선호, 재고 소진, 매출 효과를 입증하지 않습니다.",
            )
            findings.append(OpportunityFinding(
                statement=SourcedClaim(
                    value=(
                        f"요청 상품 역할 ‘{target_label}’과 재고 상품 역할 ‘{inventory_label}’ "
                        f"({item_label})을 코디 맥락으로 연결했습니다.{note_text} "
                        "이는 재고 사실과 구분되는 규칙 기반 기획 제안이며 재고 소진이나 수요 효과를 뜻하지 않습니다."
                    ),
                    kind="interpretation",
                ),
                style_id=product.style_id,
                attribute_name="styling_complement",
                questions=(relationship_question,),
                limitations=relationship_limitations,
            ))
            questions.append(relationship_question)
            limitations.extend(relationship_limitations)
            continue
        if not product.style_id or not product.style_id.strip() or not product.evidence_ids:
            questions.append("Style ID 또는 Evidence가 부족한 자사 상품은 비교에서 제외했습니다.")
            continue
        eligible_products.append(product)

    compared = 0
    for candidate in eligible_candidates:
        trend_attributes = _evidence_attributes(candidate.attributes)
        for product in eligible_products:
            for trend_attribute in trend_attributes:
                for own_attribute in _evidence_attributes(product.attributes):
                    if _key(trend_attribute.name) != _key(own_attribute.name):
                        continue
                    compared += 1
                    trend_value = _key(trend_attribute.value)
                    own_value = _key(own_attribute.value)
                    if trend_value == own_value:
                        statement = (
                            f"근거가 연결된 트렌드 후보 '{candidate.name}'와 "
                            f"자사 상품 {product.style_id}에서 "
                            f"{trend_attribute.name} 값 '{trend_attribute.value}'가 모두 관찰됩니다."
                        )
                    else:
                        statement = (
                            f"근거가 연결된 트렌드 후보 '{candidate.name}'의 "
                            f"{trend_attribute.name} 값 '{trend_attribute.value}'와 "
                            f"자사 상품 {product.style_id}의 확인 값 "
                            f"'{own_attribute.value}'가 다릅니다."
                        )
                    comparison_questions = [
                        "이 속성 관계가 신규 상품 기회인지 MD가 검토해야 합니다."
                    ]
                    requested = requested_values.get(_key(trend_attribute.name), set())
                    if requested and trend_value not in requested:
                        comparison_questions.append(
                            f"트렌드 속성 값이 사용자 요청과 다르므로 '{trend_attribute.name}' 조건을 확인해야 합니다."
                        )
                    questions.extend(comparison_questions)
                    findings.append(
                        OpportunityFinding(
                            statement=SourcedClaim(
                                value=statement,
                                kind="evidence_observation",
                                evidence_ids=tuple(
                                    dict.fromkeys(
                                        (*trend_attribute.evidence_ids, *own_attribute.evidence_ids)
                                    )
                                ),
                            ),
                            trend_name=candidate.name,
                            style_id=product.style_id,
                            attribute_name=trend_attribute.name,
                            questions=tuple(comparison_questions),
                            limitations=(
                                "속성 비교는 전달된 자료의 관찰이며 고객 수요나 상품 차별성의 증거가 아닙니다.",
                            ),
                        )
                    )

    if compared == 0:
        questions.append(
            "비교 가능한 트렌드·자사 상품 속성 근거가 없어 기획 기회나 상품 공백을 판단하지 않았습니다."
        )
        limitations.append(
            "상품 카탈로그 범위와 속성 완전성이 계약에 정의되지 않아, 자료에 없는 속성을 실제 상품 부재로 해석할 수 없습니다."
        )

    unique_questions = tuple(dict.fromkeys(question for question in questions if question.strip()))
    unique_limitations = tuple(dict.fromkeys(limitation for limitation in limitations if limitation.strip()))
    return OpportunityAnalysis(
        findings=tuple(findings),
        questions=unique_questions,
        limitations=unique_limitations,
    )


def _role_label(role: str) -> str:
    return {"upper_body": "상의", "lower_body": "하의"}.get(role, role)
