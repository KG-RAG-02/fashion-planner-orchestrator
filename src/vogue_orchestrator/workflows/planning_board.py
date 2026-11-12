"""Evidence-linked board data and deterministic SVG rendering."""

from __future__ import annotations

import base64
import html
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..domain.context import ProductPlanningContext
from ..domain.concept import ProductConceptDraft


BoardState = Literal[
    "available", "partial", "no_results", "not_provided", "unverified",
    "blocked", "failed",
]


@dataclass(frozen=True)
class BoardClaim:
    text: str
    kind: str
    evidence_ids: tuple[str, ...] = ()
    source: str | None = None
    source_ref: str | None = None
    status: str = "unverified"


@dataclass(frozen=True)
class BoardSection:
    section_id: str
    title: str
    producer: str
    input_status: str
    schema_version: str | None
    state: BoardState
    claims: tuple[BoardClaim, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanningBoard:
    title: str
    season: str
    category: str
    review_label: str
    process: tuple[str, str, str]
    sections: tuple[BoardSection, ...]
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    synthetic_context: bool = False
    synthetic_dataset_ids: tuple[str, ...] = ()
    synthetic_notice: str | None = None


def _state(status: str, *, has_claims: bool = False, has_unverified: bool = False) -> BoardState:
    if status == "not_provided":
        return "not_provided"
    if status == "no_results":
        return "no_results"
    if status == "error":
        return "failed"
    if status == "partial" or has_unverified:
        return "partial"
    return "available" if has_claims else "unverified"


def _claim(text: object, kind: str, ids=(), *, source=None, source_ref=None) -> BoardClaim:
    clean_ids = tuple(dict.fromkeys(str(item) for item in ids if isinstance(item, str) and item.strip()))
    return BoardClaim(
        text=str(text), kind=kind, evidence_ids=clean_ids,
        source=str(source) if source else None,
        source_ref=str(source_ref) if source_ref else None,
        status="available" if clean_ids else "unverified",
    )


def build_planning_board(context: ProductPlanningContext, draft: ProductConceptDraft) -> PlanningBoard:
    """Create a compact, provenance-aware board model without inventing evidence."""
    request_status = context.input_statuses.get("planning_request", "not_provided")
    request_claims = []
    if context.request.original_query:
        request_claims.append(_claim(
            context.request.original_query, "user_requested", context.request.result.evidence_ids,
            source=context.request.result.agent_id, source_ref="planning_request.query",
        ))
    for field_name, label, value in (
        ("season", "시즌", context.request.season),
        ("category", "카테고리", context.request.category),
        ("target_customer", "대상 고객", context.request.target_customer),
    ):
        if value:
            request_claims.append(_claim(
                f"{label}: {value}", "user_requested", context.request.field_evidence_ids.get(field_name, ()),
                source=context.request.result.agent_id, source_ref=f"planning_request.{field_name}",
            ))
    request_claims.extend(
        _claim(f"요청 속성 {item.name}={item.value}", "user_requested", item.evidence_ids,
               source=context.request.result.agent_id, source_ref="planning_request.requested_attributes")
        for item in context.request.requested_attributes if item.value is not None
    )

    trend_status = context.input_statuses.get("trend_result", "not_provided")
    trend_claims = []
    unverified_trends = False
    for candidate in context.trends.candidates:
        candidate_kind = "observation" if candidate.evidence_ids else "to_validate"
        if not candidate.evidence_ids:
            unverified_trends = True
        trend_claims.append(_claim(
            f"트렌드: {candidate.name}", candidate_kind, candidate.evidence_ids,
            source=context.trends.result.agent_id, source_ref=f"trend_candidates.{candidate.source_style_id or candidate.name}",
        ))
        for item in candidate.attributes:
            if item.value is None:
                continue
            ids = item.evidence_ids
            if not ids:
                unverified_trends = True
            trend_claims.append(_claim(
                f"관찰 속성 {item.name}={item.value}", "observation" if ids else "to_validate", ids,
                source=context.trends.result.agent_id, source_ref=f"{candidate.name}.attributes.{item.name}",
            ))
        if candidate.description:
            trend_claims.append(_claim(
                candidate.description, "observation" if candidate.description_evidence_ids else "to_validate",
                candidate.description_evidence_ids, source=context.trends.result.agent_id,
                source_ref=f"{candidate.name}.description",
            ))

    inventory_status = context.input_statuses.get("inventory_product_result", "not_provided")
    inventory_claims = []
    unverified_inventory = False
    for product in context.inventory.products:
        label = product.style_id or product.category or "상품명 미제공"
        if product.quantity is not None:
            quantity = f"{label} 재고: {product.quantity} {product.unit or '단위 미상'}"
            if product.as_of:
                quantity += f" · 기준 {product.as_of}"
            if product.inventory_definition:
                quantity += f" · 정의 {product.inventory_definition}"
            inventory_claims.append(_claim(
                quantity, "observation" if product.evidence_ids else "to_validate", product.evidence_ids,
                source=context.inventory.result.agent_id, source_ref=f"products[{product.source_product_index}].inventory",
            ))
            unverified_inventory |= not bool(product.evidence_ids)
        for attribute in product.attributes:
            if attribute.value is None:
                continue
            inventory_claims.append(_claim(
                f"{label} 속성 {attribute.name}={attribute.value}",
                "observation" if attribute.evidence_ids else "to_validate", attribute.evidence_ids,
                source=context.inventory.result.agent_id,
                source_ref=f"products[{product.source_product_index}].attributes.{attribute.name}",
            ))
            unverified_inventory |= not bool(attribute.evidence_ids)
    for relation in context.styling_relationships:
        inventory_claims.append(_claim(
            f"역할 규칙상 코디 후보: 재고 상품 {relation.inventory_product_index + 1}",
            "interpretation", relation.inventory_evidence_ids,
            source="Project_06 role pairing rule", source_ref=relation.rule_version,
        ))

    sales_status = context.input_statuses.get("sales_market_result", "not_provided")
    sales_payload = context.sales_market_result or {}
    sales_id_values: list[str] = []
    def collect_sales_ids(value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "evidence_id" and isinstance(child, str):
                    sales_id_values.append(child)
                elif key in {"evidence_ids", "input_evidence_ids"}:
                    if isinstance(child, str):
                        sales_id_values.append(child)
                    elif isinstance(child, (list, tuple)):
                        sales_id_values.extend(str(item) for item in child if isinstance(item, str))
                else:
                    collect_sales_ids(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect_sales_ids(child)
    collect_sales_ids(sales_payload)
    sales_ids = tuple(dict.fromkeys(sales_id_values))
    sales_claims = []
    if sales_status == "not_provided":
        sales_state: BoardState = "not_provided"
        sales_notes = ("판매·시장 입력 미제공",)
    elif sales_status == "no_results":
        sales_state = "no_results"
        sales_notes = ("판매·시장 조회 결과 없음",)
    elif sales_status == "error":
        sales_state = "failed"
        sales_notes = ("판매·시장 입력 오류",)
    else:
        sales_state = _state(sales_status, has_claims=bool(sales_payload))
        sales_notes = ("판매·시장 에이전트가 전달한 분석 결과를 기획 맥락으로 사용했습니다. 원자료 재집계는 하지 않았습니다.",)
        def sales_findings(value, path="sales_market_result"):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"summary", "text", "finding", "observation", "interpretation", "label", "value"}:
                        if isinstance(child, (str, int, float)) and str(child).strip():
                            evidence_ids = value.get("evidence_ids", value.get("input_evidence_ids", ()))
                            if isinstance(evidence_ids, str):
                                evidence_ids = (evidence_ids,)
                            sales_claims.append(_claim(str(child), "producer_finding", evidence_ids,
                                source="sales-market-agent", source_ref=f"{path}.{key}"))
                    elif key not in {"evidence", "evidence_id", "evidence_ids", "input_evidence_ids"}:
                        sales_findings(child, f"{path}.{key}")
            elif isinstance(value, (list, tuple)):
                for index, child in enumerate(value[:50]):
                    sales_findings(child, f"{path}[{index}]")
        sales_findings(sales_payload)
        if sales_ids and not sales_claims:
            sales_claims.append(_claim("전달된 Evidence ID", "to_validate", sales_ids,
                                       source="sales-market-agent", source_ref="sales_market_result"))

    reference_claims = []
    knowledge = context.planning_knowledge
    if knowledge:
        for result in knowledge.by_purpose:
            if result.purpose not in {"planning_reference", "precedent", "taxonomy", "catalog_comparison", "assortment_relation"}:
                continue
            for ref in result.evidence:
                reference_claims.append(_claim(
                    f"{'[합성 데이터 관찰] ' if ref.is_synthetic else ''}{ref.source_name or ref.source_id}: {ref.quote or ref.locator or '인용 미제공'}",
                    "observation", (ref.evidence_id,), source=ref.source_name or ref.source_id,
                    source_ref=ref.locator,
                ))

    spec_claims = [
        _claim(f"{item.name}={item.value}", item.kind, item.evidence_ids,
               source="ProductConceptDraft", source_ref=f"attributes.{item.name}")
        for item in draft.attributes if item.value is not None
    ]
    proposal_claims = []
    for item in (draft.opportunity, draft.product_definition):
        if item.value:
            proposal_claims.append(_claim(item.value, item.kind, item.evidence_ids,
                                          source="Project_06 planning", source_ref="draft"))
    proposal_claims.extend(
        _claim(item.statement.value or "", item.statement.kind, item.statement.evidence_ids,
               source="Project_06 analysis", source_ref=item.trend_name or item.style_id)
        for item in draft.opportunity_findings if item.statement.value
    )

    sections = (
        BoardSection("concept", "기획 의도 / Concept", context.request.result.agent_id, request_status,
                     context.request.result.schema_version, _state(request_status, has_claims=bool(request_claims)),
                     tuple(request_claims), tuple(context.request.missing_fields)),
        BoardSection("trend", "트렌드 분석 / Trend", context.trends.result.agent_id, trend_status,
                     context.trends.result.schema_version, _state(trend_status, has_claims=bool(trend_claims), has_unverified=unverified_trends),
                     tuple(trend_claims), context.trends.result.limitations),
        BoardSection("inventory", "재고 분석 / Inventory", context.inventory.result.agent_id, inventory_status,
                     context.inventory.result.schema_version, _state(inventory_status, has_claims=bool(inventory_claims), has_unverified=unverified_inventory),
                     tuple(inventory_claims), context.inventory.result.limitations),
        BoardSection("sales_market", "판매·시장 / Sales & Market", "sales-market-agent", sales_status,
                     sales_payload.get("schema_version") if sales_payload else None, sales_state,
                     tuple(sales_claims), sales_notes),
        BoardSection("insight", "기획 결론 / Insight → Proposal", "Project_06", "ok", None,
                     _state("ok", has_claims=bool(proposal_claims), has_unverified=any(not c.evidence_ids for c in proposal_claims)),
                     tuple(proposal_claims), draft.questions_for_validation),
        BoardSection("specifications", "분석 기반 상품 사양", "Project_06", "ok", None,
                     _state("ok", has_claims=bool(spec_claims), has_unverified=any(not c.evidence_ids for c in spec_claims)),
                     tuple(spec_claims), ("Evidence 없는 속성은 제안 또는 검증 필요로 표시",)),
        BoardSection("references", "참고자료", "authorized retrieval", "ok" if reference_claims else "no_results", None,
                     "available" if reference_claims else "no_results", tuple(reference_claims),
                     () if reference_claims else ("허가된 참고자료 검색 결과 없음",)),
        BoardSection("finance", "원가·판매 추정", "Project_06", "not_provided", None,
                     "not_provided", (), ("원가 입력 또는 승인된 산식 미제공",)),
    )
    evidence_ids = tuple(dict.fromkeys(
        evidence_id for section in sections for claim in section.claims for evidence_id in claim.evidence_ids
    ))
    synthetic_refs = tuple(item for item in knowledge.evidence if item.is_synthetic) if knowledge else ()
    synthetic_dataset_ids = tuple(sorted({item.dataset_id for item in synthetic_refs if item.dataset_id}))
    synthetic_notice = next((item.synthetic_notice for item in synthetic_refs if item.synthetic_notice), None)
    return PlanningBoard(
        title=f"{context.request.season or '시즌 미정'} {context.request.category or '카테고리 미정'} 신상품 기획안",
        season=context.request.season or "시즌 미정",
        category=context.request.category or "카테고리 미정",
        review_label="MD 검토용 초안 · 자동 승인 아님",
        process=("ISSUE", "INSIGHT", "PROPOSAL"), sections=sections,
        evidence_ids=evidence_ids,
        limitations=tuple(dict.fromkeys((*context.limitations, *draft.limitations))),
        synthetic_context=bool(synthetic_refs),
        synthetic_dataset_ids=synthetic_dataset_ids,
        synthetic_notice=synthetic_notice,
    )


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words or [text]:
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def render_planning_board_svg(board: PlanningBoard, product_image_path: Path) -> bytes:
    """Render exact text and evidence labels as SVG; embed only the supplied product visual."""
    width, height = 1600, 1120
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f0f1ec"/>',
        '<rect x="18" y="14" width="1564" height="78" rx="10" fill="#102c21"/>',
        '<text x="44" y="43" fill="#c8dc63" font-size="15" font-family="sans-serif">'
        + html.escape(f"{board.season} / MERCHANDISING PROPOSAL") + '</text>',
        '<text x="44" y="74" fill="#fff" font-size="27" font-weight="700" font-family="sans-serif">'
        + html.escape(board.title) + '</text>',
        '<rect x="1288" y="27" width="265" height="40" rx="5" fill="#e5eadb"/>',
        '<text x="1320" y="52" fill="#102c21" font-size="13" font-weight="700" font-family="sans-serif">MD REVIEW DRAFT</text>',
        '<text x="1300" y="84" fill="#dce5db" font-size="11" font-family="sans-serif">'
        + html.escape(board.review_label) + '</text>',
        *([f'<rect x="1010" y="58" width="225" height="22" rx="4" fill="#fff0bd"/>',
           f'<text x="1021" y="73" fill="#684b18" font-size="11" font-weight="700" font-family="sans-serif">합성 데이터 참고</text>']
          if board.synthetic_context else []),
        '<rect x="28" y="105" width="1544" height="42" rx="7" fill="#dce2d8"/>',
    ]
    step_text = "   →   ".join(board.process)
    parts.append(f'<text x="58" y="132" fill="#123328" font-size="17" font-weight="700" font-family="sans-serif">{html.escape(step_text)}</text>')

    columns = {"concept": 28, "trend": 28, "inventory": 28, "sales_market": 28,
               "insight": 540, "specifications": 540, "references": 540, "finance": 540}
    row_y = {"concept": 164, "trend": 360, "inventory": 635, "sales_market": 835,
             "insight": 164, "specifications": 420, "references": 700, "finance": 900}
    card_w, image_x, image_w = 490, 1052, 520
    card_h = {"concept": 180, "trend": 260, "inventory": 185, "sales_market": 230,
              "insight": 240, "specifications": 260, "references": 185, "finance": 120}
    state_labels = {"available": "근거 확인", "partial": "일부 근거/입력 확인 필요", "no_results": "조회 결과 없음",
                    "not_provided": "입력 미제공", "unverified": "근거 미확인", "blocked": "상세 해석 보류", "failed": "처리 실패"}
    for section in board.sections:
        x, y, h = columns[section.section_id], row_y[section.section_id], card_h[section.section_id]
        parts.append(f'<rect x="{x}" y="{y}" width="{card_w}" height="{h}" rx="9" fill="#fff" stroke="#c8cec7" stroke-width="2"/>')
        parts.append(f'<rect x="{x+12}" y="{y+12}" width="38" height="25" rx="4" fill="#164635"/>')
        parts.append(f'<text x="{x+21}" y="{y+30}" fill="#fff" font-size="12" font-weight="700" font-family="sans-serif">{section.section_id[:3].upper()}</text>')
        parts.append(f'<text x="{x+60}" y="{y+30}" fill="#17231e" font-size="17" font-weight="700" font-family="sans-serif">{html.escape(section.title)}</text>')
        parts.append(f'<text x="{x+14}" y="{y+55}" fill="#547064" font-size="11" font-family="sans-serif">{html.escape(state_labels[section.state])} · {html.escape(section.producer)} · {html.escape(section.input_status)}</text>')
        cy = y + 77
        lines_budget = max(2, (h - 94) // 18)
        visible_claims = section.claims[:max(1, lines_budget // 2)]
        if not visible_claims:
            visible_claims = (_claim(section.notes[0] if section.notes else state_labels[section.state], "to_validate"),)
        for claim in visible_claims:
            label = {"observation": "관찰", "interpretation": "해석", "proposal": "제안",
                     "user_requested": "요청", "to_validate": "확인 필요", "evidence_observation": "관찰"}.get(claim.kind, claim.kind)
            body = f"[{label}] {claim.text}"
            wrapped = _wrap(body, 55)
            for line in wrapped[:2]:
                if cy > y + h - 26:
                    break
                parts.append(f'<text x="{x+16}" y="{cy}" fill="#182620" font-size="12" font-family="sans-serif">{html.escape(line)}</text>')
                cy += 16
            if claim.evidence_ids:
                refs = "Evidence: " + ", ".join(claim.evidence_ids)
            else:
                refs = "Evidence 미연결"
            if cy <= y + h - 16:
                parts.append(f'<text x="{x+26}" y="{cy}" fill="#527367" font-size="10" font-family="sans-serif">{html.escape(refs)}</text>')
                cy += 17
        if section.notes and visible_claims and section.claims:
            note = section.notes[0]
            if cy <= y + h - 12:
                parts.append(f'<text x="{x+16}" y="{cy}" fill="#956a32" font-size="10" font-family="sans-serif">{html.escape(_wrap(note, 66)[0])}</text>')
        omitted = max(0, len(section.claims) - len(visible_claims))
        if omitted and cy <= y + h - 12:
            parts.append(f'<text x="{x+16}" y="{y+h-12}" fill="#956a32" font-size="10" font-family="sans-serif">'
                         f'+{omitted}개 항목은 응답의 구조화된 sections에 포함</text>')

    parts.append(f'<rect x="{image_x}" y="164" width="{image_w}" height="670" rx="9" fill="#fff" stroke="#c8cec7" stroke-width="2"/>')
    parts.append(f'<text x="{image_x+20}" y="195" fill="#17231e" font-size="18" font-weight="700" font-family="sans-serif">신제품 이미지 / Product Visual</text>')
    try:
        encoded = base64.b64encode(product_image_path.read_bytes()).decode("ascii")
        parts.append(f'<image x="{image_x+25}" y="{210}" width="{image_w-50}" height="590" preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,{encoded}"/>')
    except OSError:
        parts.append(f'<text x="{image_x+24}" y="250" fill="#8b423b" font-size="14" font-family="sans-serif">상품 이미지 읽기 실패</text>')
    parts.append(f'<rect x="{image_x}" y="850" width="{image_w}" height="230" rx="9" fill="#e6eee5" stroke="#c8cec7" stroke-width="2"/>')
    parts.append(f'<text x="{image_x+18}" y="880" fill="#17231e" font-size="17" font-weight="700" font-family="sans-serif">근거 요약 / Review Notes</text>')
    summary = f"입력 Evidence {len(board.evidence_ids)}건 · 미확정 분석은 검토 필요로 표시"
    parts.append(f'<text x="{image_x+18}" y="910" fill="#30463a" font-size="12" font-family="sans-serif">{html.escape(summary)}</text>')
    all_ids = ", ".join(board.evidence_ids) or "연결된 Evidence ID 없음"
    for index, line in enumerate(_wrap(all_ids, 70)[:4]):
        parts.append(f'<text x="{image_x+18}" y="{940+index*17}" fill="#47665a" font-size="10" font-family="sans-serif">{html.escape(line)}</text>')
    parts.append('<text x="30" y="1100" fill="#506257" font-size="10" font-family="sans-serif">검토용 초안 · 근거 미연결 값은 확정 사실이 아닙니다.</text>')
    parts.append('</svg>')
    return "\n".join(parts).encode("utf-8")
