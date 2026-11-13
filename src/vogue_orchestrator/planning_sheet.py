"""기획서 PNG: the evidence-linked planning board rendered from a stored integrated result.

Format: new-product-agent ``docs/design/evidence-linked-planning-board.md`` and its model in
``workflows/planning_board.py`` (``PlanningBoard``/``BoardSection``/``BoardClaim``): a review-only
header, the ISSUE → INSIGHT → PROPOSAL strip, eight section cards (state label, producer, claim kind,
Evidence IDs, notes), the product visual and an evidence summary. The board's purpose — a review
artifact that keeps producer observations, interpretation, proposals and validation questions apart —
is also the 상품기획서 of ``docs/design/trend-batch-kg-rag-product-planning.md`` (근거와 불확실성을
포함한 상품기획서 및 검토용 시안).

Presentation only. The input is the ``integrated-result/1.0`` envelope exactly as P08 stored it. No
agent is called, no producer value is recomputed or rewritten, a missing value stays missing
(미제공 / 미확인, never 0), and long lists are cut with an explicit "+N" count instead of silently.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from .workflows.planning_board import BoardClaim, BoardSection, PlanningBoard, _claim, _state

SHEET_FORMAT = 'evidence-linked-planning-board/png-1'
REVIEW_LABEL = 'MD 검토용 초안 · 자동 승인 아님'
PROCESS = ('ISSUE', 'INSIGHT', 'PROPOSAL')
FOOTER_NOTE = '검토용 초안 · 근거 미연결 값은 확정 사실이 아닙니다 · 승인·발주·생산 가능성 확정이 아닙니다.'
# P08 writes this limitation itself when the deterministic fixture image provider is used.
FIXTURE_IMAGE_MARKER = 'VOGUE_WORKFLOW_IMAGE_PROVIDER=fixture'
# P06 writes this limitation into the Draft when its visuals come from the offline fixture provider.
DRAFT_FIXTURE_MARKER = 'VOGUE_DRAFT_IMAGE_PROVIDER=fixture'
# workflow-contract 0.2.0: the two P06 visuals of a Draft (draft.visuals), in display order.
DRAFT_VISUALS = (('product', '신상품 · Product'), ('outfit', '착장 · Outfit'))
MAX_EMBEDDED_IMAGE_BYTES = 10 * 1024 * 1024

STAGES = (('trend', '01', 'Trend'), ('stock', '02', 'Stock'), ('planning_draft', '03', 'Draft'),
          ('sales_market_review', '04', 'Review'), ('integration', '05', 'Integrate'), ('final_image', '06', 'Sheet'))
STATE_LABELS = {'available': '근거 확인', 'partial': '일부 근거/입력 확인 필요', 'no_results': '조회 결과 없음',
                'not_provided': '입력 미제공', 'unverified': '근거 미확인', 'blocked': '상세 해석 보류',
                'failed': '처리 실패'}
KIND_LABELS = {'observation': '관찰', 'evidence_observation': '관찰', 'interpretation': '해석', 'proposal': '제안',
               'user_requested': '요청', 'to_validate': '확인 필요', 'producer_finding': '에이전트 결과'}
CONTEXT_LABELS = {'season': '시즌', 'category': '카테고리', 'target_customer': '대상 고객', 'gender': '성별',
                  'colors': '색상', 'color': '색상', 'materials': '소재', 'price_band': '가격대'}
DECISION_LABELS = {'price': '가격', 'cost': '원가', 'quantity': '수량', 'purchase_order': '발주', 'adoption': '최종 채택'}
MD_DECISION_LABELS = {'adopt': '채택 · adopt', 'revise': '수정 · revise', 'hold': '보류 · hold'}
RUN_STATUS_LABELS = {'ok': '완료 · ok', 'partial': '부분 · partial', 'no_results': '결과 없음 · no_results',
                     'error': '오류 · error', 'generated': '생성됨', 'failed': '실패', 'skipped': '생략'}
METRIC_LABELS = {'current_company_sales': '현재 자사 판매'}
SECTION_LIMITS = {'concept': 5, 'trend': 8, 'inventory': 5, 'sales_market': 6,
                  'insight': 8, 'specifications': 4, 'references': 5, 'finance': 5}


class PlanningSheetError(Exception):
    """Fails closed with a problem code (the HTTP route maps it to 503/422)."""

    def __init__(self, code: str, http_status: int = 503):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


# --- reading the stored envelope ----------------------------------------------------------------

def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(item for item in _list(value) if isinstance(item, str) and item.strip())


def _unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if isinstance(value, str) and value.strip()))


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ''
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, (list, tuple)):
        return ', '.join(text for text in (_scalar_text(item) for item in value) if text)
    return ''


def _season_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    season = _mapping(value)
    return f"{season.get('year', '')} {season.get('season', '')}".strip() if season else ''


def _raw(result: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    raw = _mapping(_mapping(_mapping(result.get('sources')).get(key)).get('raw_response'))
    return raw or None


def _stage_map(result: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {stage['stage']: stage for stage in _list(result.get('stages'))
            if isinstance(stage, Mapping) and isinstance(stage.get('stage'), str)}


def _status(result: Mapping[str, Any], key: str, raw: Mapping[str, Any] | None) -> str:
    stage = _stage_map(result).get(key)
    if stage and isinstance(stage.get('status'), str):
        return stage['status']
    if raw and isinstance(raw.get('status'), str):
        return raw['status']
    return 'not_provided'


def _evidence_ids(refs: Any) -> tuple[str, ...]:
    return _unique(_mapping(ref).get('evidence_id') for ref in _list(refs))


def _missing_section(result, stage_key, section_id, title, producer, status, body=None) -> BoardSection:
    """A section without displayable content keeps the producer's status (error → failed, no_results → no_results)."""
    errors = [error for error in _list(result.get('errors')) if _mapping(error).get('stage') == stage_key]
    limitations = _strings(_mapping(body).get('limitations'))
    if status == 'error' or errors:
        notes = tuple(f"{error.get('code', 'ERROR')}: {error.get('message', '')}".strip(': ') for error in errors)
        return BoardSection(section_id, title, producer, status, None, 'failed', (), notes + limitations or ('단계 오류',))
    if status == 'no_results':
        return BoardSection(section_id, title, producer, status, None, 'no_results', (),
                            limitations or ('생산자가 결과 없음(no_results)을 반환했습니다.',))
    return BoardSection(section_id, title, producer, status, None, 'not_provided', (),
                        limitations or ('이 단계의 결과가 없습니다(실행되지 않았거나 이전 단계에서 중단).',))


def _concept(result: Mapping[str, Any]) -> BoardSection:
    request = _mapping(result.get('planning_request'))
    user_input = _mapping(result.get('user_input'))
    context = _mapping(request.get('context'))
    claims = []
    text = user_input.get('text') or request.get('query')
    if isinstance(text, str) and text.strip():
        claims.append(_claim(text.strip(), 'user_requested', source='user_input', source_ref='user_input.text'))
    ordered = [key for key in ('season', 'category', 'target_customer') if key in context]
    for key in [*ordered, *(key for key in context if key not in ordered)]:
        value = context[key]
        shown = _scalar_text(value)
        if shown:
            claims.append(_claim(f'{CONTEXT_LABELS.get(key, key)}: {shown}', 'user_requested',
                                 source='planning_request', source_ref=f'planning_request.context.{key}'))
    images = _list(user_input.get('images'))
    if images:
        claims.append(_claim(f'레퍼런스 이미지 {len(images)}건 (Draft·통합 참고용)', 'user_requested',
                             source='user_input', source_ref='user_input.images'))
    notes = tuple(f'{label} 미지정' for key, label in (('season', '시즌'), ('category', '카테고리')) if not context.get(key))
    status = 'ok' if request else 'not_provided'
    return BoardSection('concept', '기획 의도 / Concept', 'vogue-orchestrator', status,
                        request.get('schema_version') if isinstance(request.get('schema_version'), str) else None,
                        _state(status, has_claims=bool(claims)), tuple(claims), notes)


def _trend(result: Mapping[str, Any]) -> BoardSection:
    raw = _raw(result, 'trend')
    status = _status(result, 'trend', raw)
    body = _mapping(raw.get('result')) if raw else {}
    if not body:
        return _missing_section(result, 'trend', 'trend', '트렌드 분석 / Trend', 'trend-analysis-agent', status,
                                _mapping(raw.get('result')) if raw else None)
    agent = _mapping(raw.get('producer')).get('agent_id') or body.get('agent_id') or 'trend-analysis-agent'
    names, definitions, descriptions, items = [], [], [], []
    for index, trend in enumerate(_list(body.get('trends'))):
        style = _mapping(_mapping(trend).get('style'))
        trend_items = [_mapping(item) for item in _list(_mapping(trend).get('items'))]
        described = [_mapping(entry) for entry in _list(style.get('description'))]
        ids = (*_strings(style.get('focus_evidence_ids')),
               *(i for entry in described for i in _strings(entry.get('evidence_ids'))),
               *(i for item in trend_items for i in _strings(item.get('evidence_ids'))))
        seasons = ' · '.join(text for text in (_season_text(s) for s in _list(style.get('seasons'))) if text)
        name = style.get('name') if isinstance(style.get('name'), str) and style.get('name') else '이름 미제공'
        names.append(_claim(f'트렌드: {name}' + (f' ({seasons})' if seasons else ''), 'observation' if ids else 'to_validate',
                            ids, source=agent, source_ref=f'trends[{index}].style'))
        if isinstance(style.get('definition'), str) and style['definition'].strip():
            definitions.append(_claim(f"{name} 정의: {style['definition'].strip()}", 'to_validate', (),
                                      source=agent, source_ref=f'trends[{index}].style.definition'))
        for position, entry in enumerate(described):
            if isinstance(entry.get('text'), str) and entry['text'].strip():
                entry_ids = _strings(entry.get('evidence_ids'))
                descriptions.append(_claim(entry['text'].strip(), 'observation' if entry_ids else 'to_validate', entry_ids,
                                           source=agent, source_ref=f'trends[{index}].style.description[{position}]'))
        for position, item in enumerate(trend_items):
            if not isinstance(item.get('name'), str) or not item['name'].strip():
                continue
            details = [f'{label} {_scalar_text(item.get(key))}' for key, label in
                       (('category_minor', '분류'), ('colors', '색'), ('materials', '소재'), ('fit', '핏'), ('patterns', '패턴'))
                       if _scalar_text(item.get(key))]
            item_ids = _strings(item.get('evidence_ids'))
            items.append(_claim(' · '.join([f"아이템: {item['name'].strip()}", *details]),
                                'observation' if item_ids else 'to_validate', item_ids,
                                source=agent, source_ref=f'trends[{index}].items[{position}]'))
    claims = (*names, *definitions, *descriptions, *items)
    schema = _mapping(raw.get('producer')).get('schema_version')
    return BoardSection('trend', '트렌드 분석 / Trend', agent, status, schema if isinstance(schema, str) else None,
                        _state(status, has_claims=bool(claims), has_unverified=any(not c.evidence_ids for c in claims)),
                        claims, _strings(body.get('limitations')))


def _unit_text(unit: Any) -> str:
    if unit == 'pieces':
        return '개'
    return f' {unit}' if isinstance(unit, str) and unit else ''


def _product_text(product: Mapping[str, Any]) -> str:
    attributes = _mapping(product.get('attributes'))
    inventory = _mapping(product.get('inventory'))
    head = ' '.join(text for text in (_scalar_text(product.get('style_id')), _scalar_text(product.get('category'))) if text)
    parts = [head or '상품 식별자 미제공']
    for key, label in (('color', '색'), ('material', '소재'), ('fit', '핏')):
        if _scalar_text(attributes.get(key)):
            parts.append(f'{label} {_scalar_text(attributes.get(key))}')
    quantity = inventory.get('quantity')
    if isinstance(quantity, int) and not isinstance(quantity, bool):
        stock = f'재고 {quantity:,}{_unit_text(inventory.get("unit"))}'
        extra = [f'기준 {inventory["as_of"]}' if isinstance(inventory.get('as_of'), str) else '',
                 f'정의 {inventory["definition"]}' if isinstance(inventory.get('definition'), str) else '',
                 inventory['data_nature'] if isinstance(inventory.get('data_nature'), str) else '']
        extra = [text for text in extra if text]
        parts.append(stock + (f" ({', '.join(extra)})" if extra else ''))
    else:
        parts.append('재고 수량 미확인')
    return ' · '.join(parts)


IMAGE_MEDIA = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}


def _product_image(product: Mapping[str, Any]) -> str | None:
    image = _mapping(product.get('image'))
    if image.get('media_type') in IMAGE_MEDIA and image.get('encoding') == 'base64' and isinstance(image.get('data'), str):
        return image['data']
    return None


def _inventory(result: Mapping[str, Any]) -> tuple[BoardSection, tuple[str, ...]]:
    raw = _raw(result, 'stock')
    status = _status(result, 'stock', raw)
    body = _mapping(raw.get('result')) if raw else {}
    if not body:
        return _missing_section(result, 'stock', 'inventory', '재고 분석 / Inventory', 'stock-agent', status,
                                _mapping(raw.get('result')) if raw else None), ()
    agent = _mapping(raw.get('producer')).get('agent_id') or body.get('agent_id') or 'stock-agent'
    products = _list(body.get('products'))
    claims, thumbnails, notes = [], [], []
    for index, slot in enumerate(_list(body.get('trend_results'))):
        slot = _mapping(slot)
        ids = _strings(slot.get('evidence_ids'))
        style_name = _mapping(slot.get('style')).get('name') or f'트렌드 {index + 1}'
        if isinstance(slot.get('summary'), str) and slot['summary'].strip():
            claims.append(_claim(f"{style_name}: {slot['summary'].strip()}", 'producer_finding' if ids else 'to_validate', ids,
                                 source=agent, source_ref=f'trend_results[{index}].summary'))
        product_index = slot.get('product_index')
        product = products[product_index] if isinstance(product_index, int) and 0 <= product_index < len(products) else None
        if isinstance(product, Mapping):
            claims.append(_claim(_product_text(product), 'observation' if ids else 'to_validate', ids,
                                 source=agent, source_ref=f'products[{product_index}]'))
            image = _product_image(product)
            if image:
                thumbnails.append(image)
        else:
            claims.append(_claim(f'{style_name}: 매칭 상품 없음 ({slot.get("status") or "상태 미제공"})', 'to_validate', (),
                                 source=agent, source_ref=f'trend_results[{index}]'))
        notes.extend(_strings(slot.get('limitations')))
    basis = _mapping(body.get('season_basis')).get('basis')
    if isinstance(basis, str) and basis.strip():
        notes.insert(0, f'시즌 기준: {basis.strip()}')
    schema = _mapping(raw.get('producer')).get('schema_version')
    section = BoardSection('inventory', '재고 분석 / Inventory', agent, status, schema if isinstance(schema, str) else None,
                           _state(status, has_claims=bool(claims), has_unverified=any(not c.evidence_ids for c in claims)),
                           tuple(claims), _unique(notes))
    return section, tuple(thumbnails)


def _period_bounds(period: Any) -> tuple[str, str] | None:
    if isinstance(period, str) and period:
        start, _, end = period.partition('/')
        return start, end or start
    if isinstance(period, Mapping) and isinstance(period.get('start'), str):
        return period['start'], period.get('end') if isinstance(period.get('end'), str) else period['start']
    return None


def _period_text(period: Any) -> str:
    bounds = _period_bounds(period)
    if not bounds:
        return '기간 미제공'
    return bounds[0] if bounds[0] == bounds[1] else f'{bounds[0]}–{bounds[1]}'


def _value_text(metric: Mapping[str, Any]) -> str:
    value = metric.get('value')
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        number = f'{value:,}'
    elif isinstance(value, float):
        number = f'{value:,.6g}' if abs(value) < 1e6 else f'{value:,.0f}'
    else:
        number = str(value)
    if isinstance(metric.get('currency'), str) and metric['currency']:
        return f"{number} {metric['currency']}"
    return number + _unit_text(metric.get('unit'))


def _nature(observation: Any) -> str:
    observation = _mapping(observation)
    nature = observation.get('data_nature')
    if observation.get('synthetic') is True or nature == 'synthetic_fixture':
        return 'synthetic'
    if nature == 'historical_external':
        return 'historical'
    if nature == 'current_internal':
        return 'current'
    return nature if isinstance(nature, str) and nature else 'unknown'


def _sales(result: Mapping[str, Any]) -> BoardSection:
    raw = _raw(result, 'sales_market_review')
    status = _status(result, 'sales_market_review', raw)
    body = _mapping(raw.get('result')) if raw else {}
    if not body:
        return _missing_section(result, 'sales_market_review', 'sales_market', '판매·시장 / Sales & Market', 'sales-market-agent',
                                status, _mapping(raw.get('result')) if raw else None)
    agent = _mapping(raw.get('producer')).get('agent_id') or body.get('agent_id') or 'sales-market-agent'
    review = _mapping(body.get('review'))
    evidence = {item['evidence_id']: item for item in (_mapping(entry) for entry in _list(review.get('evidence')))
                if isinstance(item.get('evidence_id'), str)}
    claims = []
    kinds = {'fact': 'observation', 'interpretation': 'interpretation', 'proposal': 'proposal'}
    for index, finding in enumerate(_list(review.get('findings'))):
        finding = _mapping(finding)
        if isinstance(finding.get('text'), str) and finding['text'].strip():
            claims.append(_claim(finding['text'].strip(), kinds.get(finding.get('kind'), 'producer_finding'),
                                 _evidence_ids(finding.get('evidence_refs')), source=agent, source_ref=f'review.findings[{index}]'))
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for metric in _list(review.get('metrics')):
        metric = _mapping(metric)
        name = metric.get('name') or metric.get('metric_id') or '지표'
        groups.setdefault(str(name), []).append(metric)
    for name, metrics in groups.items():
        label = METRIC_LABELS.get(name, name)
        present = [metric for metric in metrics if metric.get('value') is not None]
        missing = [metric for metric in metrics if metric.get('value') is None]
        if present:
            bounds = sorted(bound for bound in (_period_bounds(metric.get('period')) for metric in present) if bound)
            span = f'{bounds[0][0]} – {bounds[-1][1]}' if bounds else '기간 미제공'
            units = sorted({(metric.get('currency') or metric.get('unit') or '단위 미제공') for metric in present
                            if isinstance(metric.get('currency') or metric.get('unit') or '단위 미제공', str)})
            natures = sorted({_nature(metric.get('observation')) for metric in present})
            ids = _unique(i for metric in present for i in _evidence_ids(metric.get('evidence_refs')))
            claims.append(_claim(f"{label}: 값 {len(present):,}건 · 기간 {span} · 단위 {', '.join(units)} · {'/'.join(natures)}",
                                 'producer_finding' if ids else 'to_validate', ids, source=agent, source_ref=f'review.metrics[{name}]'))
            # Row values differ by dimensions that live only in the evidence (e.g. channel, article taxonomy),
            # so the sheet quotes the producer's metric definition instead of listing bare values side by side.
            cited = [evidence.get(i) for i in ids if isinstance(evidence.get(i), Mapping)]
            definitions = _unique(item.get('metric_definition') for item in cited)
            channels = _unique(str(item['channel']) for item in cited if isinstance(item.get('channel'), (str, int))
                               and not isinstance(item.get('channel'), bool))
            for definition in definitions[:2]:
                definition_ids = _unique(item.get('evidence_id') for item in cited if item.get('metric_definition') == definition)
                claims.append(_claim(f"{label} 정의" + (f" (원본 채널 ID {', '.join(channels)}별 행)" if channels else '') + f': {definition}',
                                     'producer_finding', definition_ids[:3], source=agent, source_ref='review.evidence[].metric_definition'))
        for metric in missing:
            reason = metric.get('missing_reason') if isinstance(metric.get('missing_reason'), str) else '원천 없음'
            claims.append(_claim(f'{label}: 미확인 — {reason}', 'to_validate', _evidence_ids(metric.get('evidence_refs')),
                                 source=agent, source_ref=f'review.metrics[{name}]'))
    schema = _mapping(raw.get('producer')).get('schema_version')
    return BoardSection('sales_market', '판매·시장 / Sales & Market', agent, status,
                        schema if isinstance(schema, str) else None,
                        _state(status, has_claims=bool(claims), has_unverified=any(not c.evidence_ids for c in claims)),
                        tuple(claims), _unique((*_strings(review.get('limitations')), *_strings(body.get('limitations')))))


def _draft_parts(result: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, Mapping[str, Any], str]:
    raw = _raw(result, 'planning_draft')
    body = _mapping(raw.get('result')) if raw else {}
    return raw, body, _status(result, 'planning_draft', raw)


def _insight(result: Mapping[str, Any]) -> BoardSection:
    raw, body, status = _draft_parts(result)
    draft = _mapping(body.get('draft'))
    if not draft:
        return _missing_section(result, 'planning_draft', 'insight', '기획 결론 / Insight → Proposal', 'new-product-agent', status,
                                body)
    agent = _mapping(raw.get('producer')).get('agent_id') or body.get('agent_id') or 'new-product-agent'
    kinds = {'fact': 'observation', 'interpretation': 'interpretation', 'proposal': 'proposal'}
    claims = tuple(
        _claim(claim['text'].strip(), kinds.get(claim.get('kind'), 'to_validate'), _evidence_ids(claim.get('evidence_refs')),
               source=agent, source_ref=f'draft.claims[{index}]')
        for index, claim in enumerate(_mapping(item) for item in _list(draft.get('claims')))
        if isinstance(claim.get('text'), str) and claim['text'].strip())
    schema = _mapping(raw.get('producer')).get('schema_version')
    return BoardSection('insight', '기획 결론 / Insight → Proposal', agent, status, schema if isinstance(schema, str) else None,
                        _state(status, has_claims=bool(claims), has_unverified=any(not c.evidence_ids for c in claims)),
                        claims, _strings(draft.get('open_questions')))


def _specifications(result: Mapping[str, Any]) -> BoardSection:
    raw, body, _ = _draft_parts(result)
    draft = _mapping(body.get('draft'))
    agent = _mapping(raw.get('producer')).get('agent_id') if raw else None
    # 0.2.0: P06's concept (draft.concept) carries the design spec; earlier Drafts have no spec field.
    concept = _mapping(draft.get('concept'))
    base = 'draft.concept.attributes' if concept else 'draft.attributes'
    attributes = [_mapping(item) for item in _list(concept.get('attributes') if concept else draft.get('attributes'))]
    claims = []
    for key, label in (('product_definition', '상품 정의'), ('target_customer', '대상 고객')):
        item = _mapping(concept.get(key))
        if isinstance(item.get('value'), str) and item['value'].strip():
            claims.append(_claim(f"{label}: {item['value'].strip()}", item.get('kind') if item.get('kind') in KIND_LABELS else 'proposal',
                                 _strings(item.get('evidence_ids')), source=agent, source_ref=f'draft.concept.{key}'))
    claims += [
        _claim(f"{item['name']}={_scalar_text(item.get('value'))}", item.get('kind') if item.get('kind') in KIND_LABELS else 'proposal',
               _strings(item.get('evidence_ids')) or _evidence_ids(item.get('evidence_refs')),
               source=agent, source_ref=f'{base}[{index}]')
        for index, item in enumerate(attributes) if isinstance(item.get('name'), str) and _scalar_text(item.get('value'))]
    claims = tuple(claims)
    if claims:
        return BoardSection('specifications', '분석 기반 상품 사양', agent or 'new-product-agent', 'ok', None,
                            _state('ok', has_claims=True, has_unverified=any(not c.evidence_ids for c in claims)), claims,
                            ('Evidence 없는 속성은 제안 또는 검증 필요로 표시',))
    note = ('기획 초안(planning-draft/1.0)에 상품 사양 필드가 없습니다.' if draft
            else '기획 초안이 없어 상품 사양을 표시할 수 없습니다.')
    return BoardSection('specifications', '분석 기반 상품 사양', agent or 'new-product-agent', 'not_provided', None,
                        'not_provided', (), (note,))


def _host(url: Any) -> str:
    try:
        parts = urlsplit(url) if isinstance(url, str) else None
    except ValueError:
        return ''
    return parts.hostname or '' if parts and parts.scheme in ('http', 'https') else ''


def _references(result: Mapping[str, Any]) -> BoardSection:
    raw = _raw(result, 'trend')
    body = _mapping(raw.get('result')) if raw else {}
    documents: dict[tuple[str, str], list[str]] = {}
    labels: dict[tuple[str, str], str] = {}
    for evidence in (_mapping(item) for item in _list(body.get('evidence'))):
        title = evidence.get('title') if isinstance(evidence.get('title'), str) else ''
        url = evidence.get('url') if isinstance(evidence.get('url'), str) else ''
        if not title and not url:
            continue
        key = (title, url)
        documents.setdefault(key, [])
        if isinstance(evidence.get('evidence_id'), str):
            documents[key].append(evidence['evidence_id'])
        published = evidence.get('published_at') if isinstance(evidence.get('published_at'), str) else ''
        labels[key] = ' · '.join(text for text in (title or '제목 미제공', published, _host(url)) if text)
    claims = tuple(_claim(labels[key], 'observation' if ids else 'to_validate', ids, source='trend-analysis-agent',
                          source_ref='result.evidence') for key, ids in documents.items())
    notes = ('Trend 응답이 인용한 원문 문서입니다. 별도 참고자료 검색은 실행하지 않았습니다.',)
    if not claims:
        return BoardSection('references', '참고자료', 'trend-analysis-agent', 'no_results', None, 'no_results', (),
                            ('인용 문서 없음',) + notes)
    return BoardSection('references', '참고자료', 'trend-analysis-agent', 'ok', None, 'available', claims, notes)


def _finance(result: Mapping[str, Any]) -> BoardSection:
    _, body, _ = _draft_parts(result)
    reserved = _strings(_mapping(body.get('draft')).get('md_decision_required')) or \
        _strings(_mapping(result.get('md_review')).get('reserved_decisions'))
    claims = tuple(_claim(f'{DECISION_LABELS.get(key, key)}: MD 판단 필요', 'to_validate', (), source='md_review',
                          source_ref='draft.md_decision_required') for key in reserved)
    return BoardSection('finance', '원가·판매 추정', 'new-product-agent', 'not_provided', None, 'not_provided', claims,
                        ('원가 입력 또는 승인된 산식 미제공',))


# --- sheet model ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class DraftVisual:
    """One P06 visual (draft.visuals.product | outfit, workflow-contract 0.2.0)."""
    key: str
    label: str
    status: str | None
    reason: str | None
    artifact: Mapping[str, Any] | None
    model: str | None
    prompt_version: str | None


@dataclass(frozen=True)
class SheetVisual:
    status: str | None
    reason: str | None
    provider_calls: int | None
    artifact: Mapping[str, Any] | None
    fixture: bool
    message: str | None
    # 0.2.0: the P06 visuals shown in the visual panel; empty for earlier runs (then the final image is shown).
    draft_visuals: tuple[DraftVisual, ...] = ()
    draft_fixture: bool = False
    image_generation_calls: int | None = None


@dataclass(frozen=True)
class PlanningSheet:
    board: PlanningBoard
    request_id: str
    run_status: str | None
    contract_version: str | None
    schema_version: str | None
    stages: tuple[tuple[str, str, str, str | None], ...]  # (key, number, label, status)
    visual: SheetVisual
    thumbnails: tuple[str, ...]
    md_status: str | None
    md_choices: tuple[str, ...]
    md_decisions: tuple[Mapping[str, Any], ...]
    natures: tuple[str, ...]
    started_at: str | None
    completed_at: str | None
    brief: tuple[tuple[str, str], ...] = ()


def _source_natures(result: Mapping[str, Any]) -> tuple[tuple[str, ...], bool]:
    labels, synthetic = [], False
    for key, label in (('trend', 'Trend'), ('stock', 'Stock'), ('planning_draft', 'Draft'),
                       ('sales_market_review', 'Review')):
        source = _mapping(_mapping(result.get('sources')).get(key))
        if not source:
            continue
        observation = _mapping(source.get('observation'))
        nature = _nature(observation)
        values = {nature}
        if key == 'sales_market_review':
            review = _mapping(_mapping(_mapping(source.get('raw_response')).get('result')).get('review'))
            values |= {_nature(_mapping(metric).get('observation')) for metric in _list(review.get('metrics'))}
        values.discard('unknown') if len(values) > 1 else None
        synthetic |= bool(values & {'synthetic', 'sample'}) or observation.get('synthetic') is True
        as_of = observation.get('as_of')
        labels.append(f"{label} · {'/'.join(sorted(values))}" + (f' · 기준 {as_of}' if isinstance(as_of, str) else ''))
    return tuple(labels), synthetic


def build_sheet(envelope: Mapping[str, Any], *, decisions: Sequence[Mapping[str, Any]] = ()) -> PlanningSheet:
    """Map a stored integrated-result/1.0 envelope onto the planning-board model (presentation only)."""
    envelope = _mapping(envelope)
    result = _mapping(envelope.get('result'))
    if not result:
        raise PlanningSheetError('PLANNING_SHEET_INPUT_INVALID', 422)
    inventory, thumbnails = _inventory(result)
    sections = (_concept(result), _trend(result), inventory, _sales(result),
                _insight(result), _specifications(result), _references(result), _finance(result))
    _, draft_body, _ = _draft_parts(result)
    draft = _mapping(draft_body.get('draft'))
    context = _mapping(_mapping(result.get('planning_request')).get('context'))
    season = _scalar_text(draft.get('season')) or _scalar_text(context.get('season')) or '시즌 미정'
    category = _scalar_text(draft.get('category')) or _scalar_text(context.get('category')) or '카테고리 미정'
    title = draft.get('title') if isinstance(draft.get('title'), str) and draft['title'].strip() else f'{season} {category} 신상품 기획안'
    limitations = _unique(_strings(result.get('limitations')))
    natures, synthetic = _source_natures(result)
    image = _mapping(result.get('final_image'))
    fixture = any(FIXTURE_IMAGE_MARKER in text for text in limitations)
    image_errors = [_mapping(error) for error in _list(result.get('errors')) if _mapping(error).get('stage') == 'final_image']
    visuals = _mapping(draft.get('visuals'))
    draft_visuals = tuple(
        DraftVisual(key=key, label=label,
                    status=_mapping(visuals.get(key)).get('status') if isinstance(_mapping(visuals.get(key)).get('status'), str) else None,
                    reason=_mapping(visuals.get(key)).get('reason') if isinstance(_mapping(visuals.get(key)).get('reason'), str) else None,
                    artifact=_mapping(visuals.get(key)).get('artifact') if isinstance(_mapping(visuals.get(key)).get('artifact'), Mapping) else None,
                    model=_mapping(visuals.get(key)).get('model') if isinstance(_mapping(visuals.get(key)).get('model'), str) else None,
                    prompt_version=_mapping(visuals.get(key)).get('prompt_version')
                    if isinstance(_mapping(visuals.get(key)).get('prompt_version'), str) else None)
        for key, label in DRAFT_VISUALS) if visuals else ()
    calls = draft_body.get('image_generation_calls')
    visual = SheetVisual(
        status=image.get('status') if isinstance(image.get('status'), str) else None,
        reason=image.get('reason') if isinstance(image.get('reason'), str) else None,
        provider_calls=image.get('provider_calls') if isinstance(image.get('provider_calls'), int) else None,
        artifact=image.get('artifact') if isinstance(image.get('artifact'), Mapping) else None,
        fixture=fixture,
        message=image_errors[0].get('message') if image_errors and isinstance(image_errors[0].get('message'), str) else None,
        draft_visuals=draft_visuals,
        draft_fixture=any(DRAFT_FIXTURE_MARKER in text for text in _strings(draft.get('limitations'))),
        image_generation_calls=calls if isinstance(calls, int) and not isinstance(calls, bool) else None)
    stage_map = _stage_map(result)
    stages = []
    for key, number, label in STAGES:
        status = stage_map.get(key, {}).get('status')
        if key == 'final_image' and visual.status:
            status = visual.status
        stages.append((key, number, label, status if isinstance(status, str) else None))
    traced = [stage for stage in _list(result.get('stages')) if isinstance(stage, Mapping)]
    started = min((s['started_at'] for s in traced if isinstance(s.get('started_at'), str)), default=None)
    completed = max((s['completed_at'] for s in traced if isinstance(s.get('completed_at'), str)), default=None)
    md_review = _mapping(result.get('md_review'))
    evidence_ids = _unique(i for section in sections for claim in section.claims for i in claim.evidence_ids)
    board = PlanningBoard(title=title.strip(), season=season, category=category, review_label=REVIEW_LABEL, process=PROCESS,
                          sections=sections, evidence_ids=evidence_ids, limitations=limitations,
                          synthetic_context=synthetic or fixture)
    request_id = result.get('request_id') or envelope.get('request_id')
    brief = _brief_rows(draft_body, draft, md_review, limitations, visual, category, season)
    return PlanningSheet(
        board=board, request_id=request_id if isinstance(request_id, str) else '—',
        run_status=(result.get('status') or envelope.get('status')) if isinstance(result.get('status') or envelope.get('status'), str) else None,
        contract_version=envelope.get('contract_version') if isinstance(envelope.get('contract_version'), str) else None,
        schema_version=result.get('schema_version') if isinstance(result.get('schema_version'), str) else None,
        stages=tuple(stages), visual=visual, thumbnails=thumbnails,
        md_status=md_review.get('status') if isinstance(md_review.get('status'), str) else None,
        md_choices=_strings(md_review.get('choices')), md_decisions=tuple(_mapping(item) for item in decisions),
        natures=natures, started_at=started, completed_at=completed, brief=brief)


def _brief_rows(body, draft, md_review, limitations, visual, category, season) -> tuple[tuple[str, str], ...]:
    """CandidateBrief fields of the 상품기획서 (trend-batch-kg-rag-product-planning.md) and where the sheet shows them.
    A field the Draft does not carry is reported as 미제공, never filled in."""
    identity = _mapping(body.get('draft_identity'))
    draft_id = identity.get('draft_id') if isinstance(identity.get('draft_id'), str) else None
    revision = identity.get('revision')
    reserved = _strings(draft.get('md_decision_required')) or _strings(md_review.get('reserved_decisions'))
    questions = _strings(draft.get('open_questions'))
    assumptions = _strings(draft.get('assumptions'))
    score = draft.get('score')
    image = RUN_STATUS_LABELS.get(visual.status or '', visual.status or '상태 미제공')
    visual_row = ('시안', ' · '.join(text for text in (image, visual.reason or '') if text))
    if visual.draft_visuals:  # 0.2.0: the visuals are P06's; stage 06 is this sheet itself
        visual_row = ('시안 (P06)', ' · '.join(
            f"{item.label.split(' · ')[0]} {RUN_STATUS_LABELS.get(item.status or '', item.status or '미제공')}"
            + (f' ({item.reason})' if item.status != 'generated' and item.reason else '') for item in visual.draft_visuals))
    return (
        ('후보 ID', (draft_id + (f' · rev {revision}' if isinstance(revision, int) else '')) if draft_id else '미제공'),
        ('후보 유형', _scalar_text(draft.get('candidate_type')) or '미제공 (초안에 없음)'),
        ('대상', f'{category} · {season}'),
        ('점수', _scalar_text(score) if _scalar_text(score) else '미제공 (초안에 점수 없음, 임의 산출 안 함)'),
        ('가정', f'{len(assumptions)}건' if assumptions else '미제공'),
        ('미확정', f"MD 판단 {len(reserved)}건: {', '.join(DECISION_LABELS.get(key, key) for key in reserved)}" if reserved else '없음'),
        ('리스크·한계', f'{len(limitations):,}건 → 하단 Limitations'),
        ('검증 질문', f'{len(questions)}건 → Insight 카드 ※' if questions else '없음'),
        visual_row,
    )


# --- fonts ---------------------------------------------------------------------------------------

_REGULAR = (('/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf', 0),
            ('/System/Library/Fonts/AppleSDGothicNeo.ttc', 0),
            ('/Library/Fonts/NanumBarunGothic.ttf', 0))
_BOLD = (('/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf', 0),
         ('/System/Library/Fonts/AppleSDGothicNeo.ttc', 6),
         ('/Library/Fonts/NanumBarunGothicBold.ttf', 0))
_DISPLAY = Path(__file__).resolve().parents[2] / 'web' / 'fonts' / 'jost' / 'Jost-Variable.woff2'


def _pick(candidates, override):
    if override:
        return (override, 0) if Path(override).is_file() else None
    return next(((path, index) for path, index in candidates if Path(path).is_file()), None)


class Fonts:
    """Korean text: NanumBarunGothic (container, fonts-nanum) or Apple SD Gothic Neo (macOS).
    ASCII-only display text: the UI's self-hosted Jost when Pillow can open it."""

    def __init__(self, environ: Mapping[str, str] | None = None):
        from PIL import ImageFont
        environ = os.environ if environ is None else environ
        self._truetype = ImageFont.truetype
        self.regular = _pick(_REGULAR, environ.get('VOGUE_PLANNING_SHEET_FONT'))
        self.bold = _pick(_BOLD, environ.get('VOGUE_PLANNING_SHEET_FONT_BOLD')) or self.regular
        if not self.regular:
            raise PlanningSheetError('PLANNING_SHEET_FONT_MISSING')
        self.display = None
        if _DISPLAY.is_file():
            try:
                probe = self._truetype(str(_DISPLAY), 12)
                probe.set_variation_by_name('Medium')
                self.display = str(_DISPLAY)
            except (OSError, ValueError, AttributeError):
                self.display = None
        self._cache: dict[tuple[str, int], Any] = {}

    def get(self, role: str, size: int):
        key = (role, size)
        if key not in self._cache:
            if role.startswith('display') and self.display:
                font = self._truetype(self.display, size)
                font.set_variation_by_name('SemiBold' if role == 'display_bold' else 'Medium')
            else:
                path, index = self.bold if role in ('bold', 'display_bold') else self.regular
                font = self._truetype(path, size, index=index)
            self._cache[key] = font
        return self._cache[key]


# --- rendering -----------------------------------------------------------------------------------

C = {
    'ground': '#EDE8E1', 'surface': '#F6F3EE', 'raised': '#FBF9F6', 'line': '#D8D1C6', 'line_soft': '#E2DBD1',
    'placeholder': '#DDD5CA', 'ink': '#2A2926', 'sub': '#5F5A52', 'sage': '#4F6150', 'sage_tint': '#DCE3D6',
    'ochre': '#8A5A1F', 'ochre_tint': '#F1E4CF', 'slate': '#4D5662', 'slate_tint': '#E3E5E8', 'brick': '#9B3B2E',
    'brick_tint': '#F3DEDA', 'plum': '#4A3F63', 'plum_tint': '#E6E1EE',
}
KIND_STYLE = {'observation': ('sage_tint', 'sage'), 'evidence_observation': ('sage_tint', 'sage'),
              'producer_finding': ('slate_tint', 'slate'), 'interpretation': ('line_soft', 'sub'),
              'proposal': ('ink', 'raised'), 'user_requested': ('plum_tint', 'plum'), 'to_validate': ('ochre_tint', 'ochre')}
STATE_COLOR = {'available': 'sage', 'partial': 'ochre', 'no_results': 'slate', 'not_provided': 'sub', 'unverified': 'ochre',
               'blocked': 'slate', 'failed': 'brick'}
STAGE_COLOR = {'ok': 'sage', 'generated': 'sage', 'partial': 'ochre', 'no_results': 'slate', 'error': 'brick',
               'failed': 'brick', 'skipped': 'sub'}

W = 1600          # design width (the board's SVG width); every coordinate below is in design units
SCALE = 2         # device pixels per design unit
COL = ((28, 490), (540, 490), (1052, 520))
COLUMN_SECTIONS = (('concept', 'trend', 'inventory', 'sales_market'), ('insight', 'specifications', 'references', 'finance'))
GAP = 16
PILL_W = 70


class _Canvas:
    def __init__(self, fonts: Fonts):
        from PIL import Image, ImageDraw
        self.fonts = fonts
        self.Image = Image
        self.measure = ImageDraw.Draw(Image.new('RGB', (8, 8)))
        self.ops: list[tuple] = []

    # sizes are in design units; fonts are cached per device size
    def font(self, role: str, size: float):
        return self.fonts.get(role, max(6, round(size * SCALE)))

    def role(self, text: str, role: str) -> str:
        return role if text.isascii() and self.fonts.display else {'display': 'regular', 'display_bold': 'bold'}.get(role, role)

    def width(self, text: str, role: str, size: float) -> float:
        return self.measure.textlength(text, font=self.font(self.role(text, role), size)) / SCALE

    def wrap(self, text: str, role: str, size: float, width: float, max_lines: int) -> list[str]:
        words = ' '.join(text.split()).split(' ')
        lines: list[str] = []
        current = ''
        for word in words:
            candidate = f'{current} {word}' if current else word
            if self.width(candidate, role, size) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ''
            while word and self.width(word, role, size) > width:
                low, high = 1, len(word)
                while low < high:
                    middle = (low + high + 1) // 2
                    low, high = (middle, high) if self.width(word[:middle], role, size) <= width else (low, middle - 1)
                lines.append(word[:low])
                word = word[low:]
            current = word
            if len(lines) > max_lines:
                break
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            last = lines[-1]
            while last and self.width(last + '…', role, size) > width:
                last = last[:-1]
            lines[-1] = last + '…'
        return lines

    def text(self, x, y, text, role='regular', size=12, color='ink'):
        self.ops.append(('text', x, y, text, self.role(text, role), size, C.get(color, color)))

    def rect(self, x, y, w, h, fill, radius=0, outline=None):
        self.ops.append(('rect', x, y, w, h, C.get(fill, fill), radius, C.get(outline, outline) if outline else None))

    def image(self, x, y, w, h, data: bytes, mode: str):
        self.ops.append(('image', x, y, w, h, data, mode))

    def render(self, height: float, metadata: Mapping[str, str]) -> bytes:
        from PIL import ImageDraw, ImageOps, PngImagePlugin
        canvas = self.Image.new('RGB', (W * SCALE, round(height * SCALE)), C['ground'])
        draw = ImageDraw.Draw(canvas)
        s = lambda value: round(value * SCALE)  # noqa: E731
        for op in self.ops:
            if op[0] == 'rect':
                _, x, y, w, h, fill, radius, outline = op
                draw.rounded_rectangle((s(x), s(y), s(x + w) - 1, s(y + h) - 1), radius=s(radius), fill=fill,
                                       outline=outline, width=SCALE if outline else 0)
            elif op[0] == 'text':
                _, x, y, text, role, size, color = op
                draw.text((s(x), s(y)), text, font=self.fonts.get(role, max(6, round(size * SCALE))), fill=color)
            elif op[0] == 'image':
                _, x, y, w, h, data, mode = op
                try:
                    with self.Image.open(io.BytesIO(data)) as source:
                        source.load()
                        picture = source.convert('RGBA')
                except Exception:  # undecodable bytes: draw the failure, never crash the sheet
                    draw.text((s(x) + 8, s(y) + 8), '이미지 읽기 실패', font=self.fonts.get('regular', s(12)), fill=C['brick'])
                    continue
                box = (s(w), s(h))
                fitted = ImageOps.fit(picture, box) if mode == 'cover' else ImageOps.contain(picture, box)
                left = s(x) + (box[0] - fitted.width) // 2
                top = s(y) + (box[1] - fitted.height) // 2
                canvas.paste(fitted, (left, top), fitted)
        info = PngImagePlugin.PngInfo()
        for key, value in metadata.items():
            info.add_text(key, value)
        out = io.BytesIO()
        canvas.save(out, format='PNG', pnginfo=info, optimize=True)
        return out.getvalue()


def _time_span(started: str | None, completed: str | None) -> str:
    """Trim stored ISO timestamps for display (no time-zone conversion)."""
    def trim(value: str | None) -> str:
        if not value:
            return '—'
        match = re.fullmatch(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?', value)
        if not match:
            return value
        zone = match.group(3) or ''
        return f"{match.group(1)} {match.group(2)}{' UTC' if zone in ('Z', '+00:00') else (' ' + zone if zone else '')}"
    start, end = trim(started), trim(completed)
    if start[:10] == end[:10] and start != '—':
        end = end[11:]
    return f'{start} → {end}'


def _ids_line(c: _Canvas, ids: Sequence[str], width: float) -> str:
    if not ids:
        return 'Evidence 미연결'
    shown: list[str] = []
    for index, evidence_id in enumerate(ids):
        rest = len(ids) - index - 1
        candidate = 'Evidence: ' + ', '.join([*shown, evidence_id]) + (f' 외 {rest:,}건' if rest else '')
        if c.width(candidate, 'regular', 10) > width and shown:
            return 'Evidence: ' + ', '.join(shown) + f' 외 {len(ids) - len(shown):,}건'
        shown.append(evidence_id)
    text = 'Evidence: ' + ', '.join(shown)
    return text if c.width(text, 'regular', 10) <= width else c.wrap(text, 'regular', 10, width, 1)[0]


def _card(c: _Canvas, section: BoardSection, x: float, y: float, w: float, thumbs: Sequence[bytes] = (), draw: bool = True) -> float:
    """Lay out one section card; returns its height. With draw=False only measures."""
    ops_before = len(c.ops)
    pad, text_x = 14, x + 16 + PILL_W + 6
    text_w = w - 32 - PILL_W - 6
    cy = y + 66
    limit = SECTION_LIMITS.get(section.section_id, 5)
    visible = section.claims[:limit]
    if draw:
        c.rect(x + 12, y + 12, 40, 25, 'ink', 6)
        code = section.section_id[:3].upper()
        c.text(x + 32 - c.width(code, 'display_bold', 12) / 2, y + 16, code, 'display_bold', 12, 'raised')
        c.text(x + 62, y + 13, section.title, 'bold', 17, 'ink')
        color = STATE_COLOR.get(section.state, 'sub')
        c.rect(x + 16, y + 47, 8, 8, color, 4)
        meta = ' · '.join(text for text in (STATE_LABELS.get(section.state, section.state), section.producer,
                                             section.input_status, section.schema_version or '') if text)
        c.text(x + 30, y + 42, c.wrap(meta, 'regular', 11, w - 46, 1)[0], 'regular', 11, 'sub')
    if not visible:
        message = section.notes[0] if section.notes else STATE_LABELS.get(section.state, section.state)
        for line in c.wrap(message, 'regular', 12, w - 32, 3):
            if draw:
                c.text(x + 16, cy, line, 'regular', 12, 'sub')
            cy += 17
        notes = section.notes[1:3]
    else:
        for claim in visible:
            label = KIND_LABELS.get(claim.kind, claim.kind)
            fill, ink = KIND_STYLE.get(claim.kind, ('line_soft', 'sub'))
            lines = c.wrap(claim.text, 'regular', 12, text_w, 2)
            if draw:
                c.rect(x + 16, cy + 1, PILL_W, 18, fill, 9)
                c.text(x + 16 + (PILL_W - c.width(label, 'bold', 10)) / 2, cy + 3, label, 'bold', 10, ink)
                for index, line in enumerate(lines):
                    c.text(text_x, cy + index * 17, line, 'regular', 12, 'ink')
            cy += max(1, len(lines)) * 17
            refs = _ids_line(c, claim.evidence_ids, text_w)
            if draw:
                c.text(text_x, cy + 1, refs, 'regular', 10, 'sage' if claim.evidence_ids else 'ochre')
            cy += 15 + 6
        notes = section.notes[:2]
    for note in notes:
        for line in c.wrap('※ ' + note, 'regular', 10.5, w - 32, 2):
            if draw:
                c.text(x + 16, cy, line, 'regular', 10.5, 'ochre')
            cy += 15
        cy += 2
    omitted = len(section.claims) - len(visible)
    hidden_notes = max(0, len(section.notes) - (len(notes) + (1 if not visible and section.notes else 0)))
    if omitted or hidden_notes:
        tail = ' · '.join(text for text in (f'항목 +{omitted:,}' if omitted else '', f'메모 +{hidden_notes:,}' if hidden_notes else '') if text)
        if draw:
            c.text(x + 16, cy + 2, f'{tail} — 전체는 실행 기록(JSON)에 있습니다', 'regular', 10, 'sub')
        cy += 18
    if thumbs:
        cy += 4
        for index, data in enumerate(thumbs[:4]):
            if draw:
                c.rect(x + 16 + index * 80, cy, 72, 72, 'raised', 8, 'line')
                c.image(x + 18 + index * 80, cy + 2, 68, 68, data, 'contain')
        cy += 72 + 6
    height = max(cy - y + pad - 6, 96)
    if draw:
        # card background goes under its content
        c.ops.insert(ops_before, ('rect', x, y, w, height, C['surface'], 12, C['line']))
    return height


def _decode_thumbs(encoded: Sequence[str]) -> list[bytes]:
    out = []
    for value in encoded[:4]:
        try:
            data = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            continue
        if 0 < len(data) <= MAX_EMBEDDED_IMAGE_BYTES:
            out.append(data)
    return out


def _draw_draft_visuals(c, visual: SheetVisual, vx: float, y: float, vw: float, visual_h: float,
                        images: Mapping[str, bytes | None], unreadable: Sequence[str]) -> None:
    """The two P06 visuals side by side (workflow-contract 0.2.0). Stage 06 is this sheet, so it is never embedded."""
    c.rect(vx, y, vw, visual_h, 'surface', 12, 'line')
    c.text(vx + 20, y + 16, '신제품 시안 / P06 Product · Outfit', 'bold', 18, 'ink')
    box_x, box_y, box_w, box_h = vx + 20, y + 52, vw - 40, 520
    gap = 12
    panel_w = (box_w - gap) / len(visual.draft_visuals)
    for index, item in enumerate(visual.draft_visuals):
        px = box_x + index * (panel_w + gap)
        status_text = RUN_STATUS_LABELS.get(item.status or '', item.status or '미제공')
        c.text(px + 2, box_y, item.label, 'bold', 12, 'ink')
        c.text(px + panel_w - c.width(status_text, 'regular', 11) - 2, box_y + 1, status_text, 'regular', 11,
               'brick' if item.status == 'failed' else 'sub')
        iy, ih = box_y + 22, box_h - 22
        data = images.get(item.key)
        if data:
            c.rect(px, iy, panel_w, ih, 'raised', 10)
            c.image(px + 4, iy + 4, panel_w - 8, ih - 8, data, 'contain')
            continue
        c.rect(px, iy, panel_w, ih, 'placeholder', 10)
        headline = '이미지를 읽지 못했습니다' if item.key in unreadable else \
            {'failed': '생성 실패', 'skipped': '생략'}.get(item.status or '', '이미지 없음')
        c.text(px + (panel_w - c.width(headline, 'bold', 16)) / 2, iy + ih / 2 - 24, headline, 'bold', 16, 'sub')
        for line_no, line in enumerate(c.wrap(item.reason or '', 'regular', 10.5, panel_w - 24, 4)):
            c.text(px + (panel_w - c.width(line, 'regular', 10.5)) / 2, iy + ih / 2 + 2 + line_no * 14, line, 'regular', 10.5, 'sub')
    caption = ['P06 시안']
    if visual.image_generation_calls is not None:
        caption.append(f'이미지 호출 {visual.image_generation_calls}회')
    models = list(dict.fromkeys(item.model for item in visual.draft_visuals if item.model))
    if models:
        caption.append('model ' + ', '.join(models))
    if visual.draft_fixture:
        caption.append('로컬 fixture 이미지 — 생성된 컨셉 아님')
    caption.append('06 기획안 = 이 문서 (P08 합성, 이미지 모델 호출 없음)')
    failed = any(item.status == 'failed' for item in visual.draft_visuals)
    for index, line in enumerate(c.wrap(' · '.join(caption), 'regular', 11, vw - 40, 2)):
        c.text(vx + 20, y + 584 + index * 15, line, 'regular', 11, 'brick' if failed else 'sub')


def render_png(sheet: PlanningSheet, *, image_bytes: bytes | None = None, image_unreadable: bool = False,
               fonts: Fonts | None = None, draft_images: Mapping[str, bytes | None] | None = None,
               draft_unreadable: Sequence[str] = ()) -> bytes:
    c = _Canvas(fonts or Fonts())
    board = sheet.board
    # header band
    c.rect(18, 14, 1564, 82, 'ink', 14)
    c.text(44, 26, f'{board.season} / MERCHANDISING PROPOSAL', 'display', 15, 'sage_tint')
    c.text(44, 50, c.wrap(board.title, 'bold', 27, 900, 1)[0], 'bold', 27, 'raised')
    c.rect(1300, 28, 255, 34, 'sage_tint', 17)
    label = 'MD REVIEW DRAFT'
    c.text(1300 + (255 - c.width(label, 'display_bold', 13)) / 2, 35, label, 'display_bold', 13, 'ink')
    c.text(1300 + (255 - c.width(board.review_label, 'regular', 11)) / 2, 70, board.review_label, 'regular', 11, 'line')
    if board.synthetic_context:
        badge = '합성·샘플 데이터 포함'
        bw = c.width(badge, 'bold', 11) + 24
        c.rect(1280 - bw, 33, bw, 24, 'ochre_tint', 12)
        c.text(1280 - bw + 12, 38, badge, 'bold', 11, 'ochre')
    # process strip with the six stage traces
    c.rect(28, 108, 1544, 44, 'sage_tint', 10)
    px = 52
    for index, step in enumerate(board.process):
        c.text(px, 118, step, 'display_bold', 17, 'ink')
        px += c.width(step, 'display_bold', 17) + 16
        if index < len(board.process) - 1:
            c.text(px, 118, '→', 'bold', 17, 'sage')
            px += c.width('→', 'bold', 17) + 16
    sx = 1556
    for key, number, stage_label, status in reversed(sheet.stages):
        status_text = RUN_STATUS_LABELS.get(status or '', status or '미실행').split(' · ')[0]
        chip = f'{number} {stage_label}'
        chip_w = c.width(chip, 'display_bold', 11) + c.width(status_text, 'regular', 10.5) + 30
        sx -= chip_w
        c.rect(sx, 118, chip_w, 24, 'raised', 12, 'line')
        c.rect(sx + 9, 127, 7, 7, STAGE_COLOR.get(status or '', 'line'), 4)
        c.text(sx + 21, 122, chip, 'display_bold', 11, 'ink')
        c.text(sx + 24 + c.width(chip, 'display_bold', 11), 123, status_text, 'regular', 10.5, 'sub')
        sx -= 6
    # section columns
    top = 168
    sections = {section.section_id: section for section in board.sections}
    thumbs = _decode_thumbs(sheet.thumbnails)
    bottoms = []
    for (x, w), ids in zip(COL[:2], COLUMN_SECTIONS):
        y = top
        for section_id in ids:
            section = sections[section_id]
            y += _card(c, section, x, y, w, thumbs if section_id == 'inventory' else ()) + GAP
        bottoms.append(y)
    # visual column
    vx, vw = COL[2]
    y = top
    visual_h = 640
    if sheet.visual.draft_visuals:  # 0.2.0: P06's product and outfit images
        _draw_draft_visuals(c, sheet.visual, vx, y, vw, visual_h, draft_images or {}, draft_unreadable)
    else:  # earlier runs: the final image
        c.rect(vx, y, vw, visual_h, 'surface', 12, 'line')
        c.text(vx + 20, y + 16, '신제품 이미지 / Product Visual', 'bold', 18, 'ink')
        box_x, box_y, box_w, box_h = vx + 20, y + 52, vw - 40, 520
        visual = sheet.visual
        if image_bytes:
            c.rect(box_x, box_y, box_w, box_h, 'raised', 10)
            c.image(box_x + 6, box_y + 6, box_w - 12, box_h - 12, image_bytes, 'contain')
        else:
            c.rect(box_x, box_y, box_w, box_h, 'placeholder', 10)
            headline = '시안 이미지를 읽지 못했습니다' if image_unreadable else \
                {'failed': '시안 이미지 생성 실패', 'skipped': '시안 이미지 생략'}.get(visual.status or '', '시안 이미지 없음')
            c.text(box_x + (box_w - c.width(headline, 'bold', 20)) / 2, box_y + box_h / 2 - 30, headline, 'bold', 20, 'sub')
            detail = ' · '.join(text for text in (visual.reason or '', f'provider_calls {visual.provider_calls}'
                                                  if visual.provider_calls is not None else '') if text)
            if detail:
                c.text(box_x + (box_w - c.width(detail, 'regular', 12)) / 2, box_y + box_h / 2 + 4, detail, 'regular', 12, 'sub')
            if visual.message:
                for index, line in enumerate(c.wrap(visual.message, 'regular', 11, box_w - 60, 2)):
                    c.text(box_x + (box_w - c.width(line, 'regular', 11)) / 2, box_y + box_h / 2 + 26 + index * 15, line, 'regular', 11, 'sub')
        status_text = RUN_STATUS_LABELS.get(visual.status or '', visual.status or '상태 미제공')
        caption = [f'최종 이미지: {status_text}']
        if visual.provider_calls is not None:
            caption.append(f'provider_calls {visual.provider_calls}')
        if visual.fixture:
            caption.append('로컬 fixture 이미지 — 생성된 컨셉 아님')
        artifact = _mapping(visual.artifact)
        if isinstance(artifact.get('sha256'), str):
            caption.append(f"sha256 {artifact['sha256'][:12]}")
        for index, line in enumerate(c.wrap(' · '.join(caption), 'regular', 11, vw - 40, 2)):
            c.text(vx + 20, y + 584 + index * 15, line, 'regular', 11, 'brick' if visual.status == 'failed' else 'sub')
    y += visual_h + GAP
    # review notes
    notes_top = y
    c.ops.append(('marker',))
    marker = len(c.ops)
    cy = y + 16
    c.text(vx + 18, cy, '근거 요약 / Review Notes', 'bold', 17, 'ink')
    cy += 32
    c.text(vx + 18, cy, '기획서 항목 (CandidateBrief)', 'bold', 11, 'sub')
    cy += 17
    for name, value in sheet.brief:
        c.text(vx + 18, cy, name, 'bold', 11, 'sub')
        for line in c.wrap(value, 'regular', 11, vw - 128, 2):
            c.text(vx + 110, cy, line, 'regular', 11, 'brick' if value.startswith('실패') else 'ink')
            cy += 15
        cy += 2
    cy += 6
    c.rect(vx + 18, cy, vw - 36, 1, 'line')
    cy += 12
    c.text(vx + 18, cy, f'입력 Evidence {len(board.evidence_ids):,}건 · 미확정 분석은 검토 필요로 표시', 'regular', 12, 'ink')
    cy += 20
    ids = ', '.join(board.evidence_ids[:24]) + (f' 외 {len(board.evidence_ids) - 24:,}건' if len(board.evidence_ids) > 24 else '')
    for line in c.wrap(ids or '연결된 Evidence ID 없음', 'regular', 10, vw - 36, 5):
        c.text(vx + 18, cy, line, 'regular', 10, 'sage')
        cy += 14
    cy += 10
    c.rect(vx + 18, cy, vw - 36, 1, 'line')
    cy += 12
    latest = sheet.md_decisions[-1] if sheet.md_decisions else None
    if latest:
        decision = MD_DECISION_LABELS.get(str(latest.get('decision')), str(latest.get('decision')))
        decided = _time_span(latest.get('decided_at') if isinstance(latest.get('decided_at'), str) else None, None).split(' → ')[0]
        md_text = f"MD 판단: {decision} · {decided} · 기록 {len(sheet.md_decisions)}건"
    else:
        md_text = f"MD 판단: {'대기' if sheet.md_status == 'pending' else (sheet.md_status or '미기록')}" + (
            f" ({' · '.join(sheet.md_choices)} 중 선택)" if sheet.md_choices else '')
    c.text(vx + 18, cy, md_text, 'bold', 12, 'ink')
    cy += 19
    if latest and isinstance(latest.get('note'), str) and latest['note'].strip():
        for line in c.wrap('메모: ' + latest['note'].strip(), 'regular', 11, vw - 36, 2):
            c.text(vx + 18, cy, line, 'regular', 11, 'sub')
            cy += 15
    rows = [('실행 ID', sheet.request_id), ('실행 상태', RUN_STATUS_LABELS.get(sheet.run_status or '', sheet.run_status or '—')),
            ('계약', ' · '.join(text for text in (sheet.contract_version, sheet.schema_version) if text) or '—'),
            ('단계 시각', _time_span(sheet.started_at, sheet.completed_at))]
    for name, value in rows:
        c.text(vx + 18, cy, name, 'bold', 11, 'sub')
        for line in c.wrap(value, 'regular', 11, vw - 110, 2):
            c.text(vx + 92, cy, line, 'regular', 11, 'ink')
            cy += 15
        cy += 2
    cy += 4
    c.text(vx + 18, cy, '데이터 성격', 'bold', 11, 'sub')
    cy += 17
    for nature in sheet.natures:
        tone = 'ochre' if ('synthetic' in nature or 'sample' in nature) else 'sub'
        for line in c.wrap(nature, 'regular', 10.5, vw - 46, 1):
            c.rect(vx + 18, cy + 4, 6, 6, tone, 3)
            c.text(vx + 30, cy, line, 'regular', 10.5, tone)
            cy += 15
    notes_h = cy - notes_top + 14
    c.ops.pop(marker - 1)
    c.ops.insert(marker - 1, ('rect', vx, notes_top, vw, notes_h, C['sage_tint'], 12, C['line']))
    bottoms.append(notes_top + notes_h + GAP)
    # limitations band (full width, two columns)
    y = max(bottoms) + 2
    band_top = y
    c.ops.append(('marker',))
    marker = len(c.ops)
    c.text(46, y + 16, '한계 · 확인 필요 / Limitations', 'bold', 15, 'ink')
    c.text(46 + c.width('한계 · 확인 필요 / Limitations', 'bold', 15) + 12, y + 19,
           f'실행 기록의 한계 {len(board.limitations):,}건(중복 제거) · 생산자 문구 그대로', 'regular', 11, 'sub')
    y += 44
    shown = list(board.limitations[:16])
    half = (len(shown) + 1) // 2
    column_y = []
    for column, items in enumerate((shown[:half], shown[half:])):
        ly = y
        for item in items:
            for index, line in enumerate(c.wrap(item, 'regular', 11, 700, 2)):
                c.text(64 + column * 770, ly, line, 'regular', 11, 'ink')
                if index == 0:
                    c.rect(50 + column * 770, ly + 6, 5, 5, 'ochre', 3)
                ly += 15
            ly += 4
        column_y.append(ly)
    y = max(column_y or [y])
    if len(board.limitations) > len(shown):
        c.text(64, y + 2, f'+{len(board.limitations) - len(shown):,}건은 실행 기록(JSON)의 limitations에 있습니다', 'regular', 10.5, 'sub')
        y += 18
    band_h = y - band_top + 12
    c.ops.pop(marker - 1)
    c.ops.insert(marker - 1, ('rect', 28, band_top, 1544, band_h, C['surface'], 12, C['line']))
    y = band_top + band_h + 14
    # footer
    c.text(30, y, FOOTER_NOTE, 'regular', 10.5, 'sub')
    right = f'VOGUE — Atelier · {SHEET_FORMAT} · {sheet.request_id}'
    c.text(1570 - c.width(right, 'regular', 10.5), y, right, 'regular', 10.5, 'sub')
    height = y + 30
    c.ops = [op for op in c.ops if op[0] != 'marker']
    metadata = {'Title': f'{board.title} — 상품기획서 (검토용)', 'Software': 'vogue-orchestrator planning_sheet',
                'vogue:format': SHEET_FORMAT, 'vogue:request_id': sheet.request_id,
                'vogue:contract_version': sheet.contract_version or '', 'vogue:schema_version': sheet.schema_version or ''}
    return c.render(height, metadata)


def planning_sheet_png(envelope: Mapping[str, Any], *, load_artifact: Callable[[Mapping[str, Any]], bytes] | None = None,
                       decisions: Sequence[Mapping[str, Any]] = (), fonts: Fonts | None = None) -> bytes:
    """PNG bytes for a run. ``load_artifact`` returns verified bytes for an image reference: the P06 visuals
    (workflow-contract 0.2.0) or, for earlier runs, the final image."""
    sheet = build_sheet(envelope, decisions=decisions)
    image_bytes, unreadable = None, False
    if sheet.visual.draft_visuals:
        # 0.2.0: embed P06's verified visuals; the final image of such a run is this sheet and is never embedded.
        images, missing = {}, []
        for item in sheet.visual.draft_visuals:
            if item.status != 'generated' or not item.artifact or item.artifact.get('media_type') not in IMAGE_MEDIA:
                continue
            try:
                data = load_artifact(dict(item.artifact)) if load_artifact else None
            except Exception:  # an unreadable artifact is reported on the sheet; the sheet still renders
                data = None
            if not isinstance(data, bytes) or len(data) > MAX_EMBEDDED_IMAGE_BYTES:
                data = None
                missing.append(item.key)
            images[item.key] = data
        return render_png(sheet, fonts=fonts, draft_images=images, draft_unreadable=tuple(missing))
    artifact = sheet.visual.artifact
    if artifact and sheet.visual.status == 'generated' and artifact.get('media_type') in IMAGE_MEDIA:
        try:
            image_bytes = load_artifact(dict(artifact)) if load_artifact else None
        except Exception:  # an unreadable artifact is reported on the sheet; the sheet still renders
            image_bytes = None
        if image_bytes is not None and (not isinstance(image_bytes, bytes) or len(image_bytes) > MAX_EMBEDDED_IMAGE_BYTES):
            image_bytes = None
        unreadable = image_bytes is None
    return render_png(sheet, image_bytes=image_bytes, image_unreadable=unreadable, fonts=fonts)


# --- command line (read-only) --------------------------------------------------------------------

SHA256 = re.compile(r'[0-9a-f]{64}')


def _read_only_store(store_dir: Path, request_id: str):
    database = store_dir / 'checkpoints.sqlite3'
    if not database.is_file():
        raise SystemExit(f'store not found: {database}')
    connection = sqlite3.connect(f'file:{database}?mode=ro&immutable=1', uri=True)
    try:
        rows = dict(connection.execute('SELECT attempt, value FROM runs WHERE rid=?', (request_id,)).fetchall())
        decisions = connection.execute(
            'SELECT sequence, integrated_response_sha256, decision, note, decided_at FROM md_decisions WHERE rid=? ORDER BY sequence',
            (request_id,)).fetchall() if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='md_decisions'").fetchone() else []
    finally:
        connection.close()
    value = rows.get('current') or rows.get('initial')
    if not value:
        raise SystemExit(f'no finished run {request_id!r} in {database}')
    envelope = json.loads(value)['envelope']

    def load(artifact: Mapping[str, Any]) -> bytes:
        sha = artifact.get('sha256')
        if not isinstance(sha, str) or not SHA256.fullmatch(sha):
            raise ValueError('artifact sha256 invalid')
        data = (store_dir / 'blobs' / sha).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha or len(data) != artifact.get('size_bytes'):
            raise ValueError('artifact bytes do not match the reference')
        return data

    history = [dict(request_id=request_id, sequence=s, integrated_response_sha256=h, decision=d, note=n, decided_at=t)
               for s, h, d, n, t in decisions]
    return envelope, load, history


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Render the planning sheet (기획서) PNG of a stored VOGUE run.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', type=Path, help='integrated-result envelope JSON (GET /api/workflow/runs/{id} body)')
    source.add_argument('--request-id', help='read the run from a P08 store directory (opened read-only)')
    parser.add_argument('--store-dir', type=Path, default=Path(os.environ.get('VOGUE_WORKFLOW_STORE_DIR', '.local/workflow')))
    parser.add_argument('--image', type=Path, help='final image bytes to embed when reading --input (optional)')
    parser.add_argument('--output', required=True, help="PNG path, or '-' for stdout")
    args = parser.parse_args(argv)
    if args.input:
        envelope = json.loads(args.input.read_text(encoding='utf-8'))
        envelope = envelope.get('envelope', envelope)
        image = args.image.read_bytes() if args.image else None
        load = (lambda artifact: image) if image else None  # noqa: E731
        decisions: list = []
    else:
        envelope, load, decisions = _read_only_store(args.store_dir, args.request_id)
    png = planning_sheet_png(envelope, load_artifact=load, decisions=decisions)
    if args.output == '-':
        sys.stdout.buffer.write(png)
    else:
        Path(args.output).write_bytes(png)
        print(f'{args.output} ({len(png):,} bytes)', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
