"""Deterministic Planning Sheet PNG rendered from the integrated workflow result.

Presentation only. The input is the integrated-result envelope exactly as orchestrator stored it. No
agent is called, no producer value is recomputed or rewritten, a missing value stays missing
(미제공 / 미확인, never 0), and long lists are cut with an explicit "+N" count instead of silently.

Layout: a one-product proposal poster. A header (title, season, category, run date) over three columns —
Analysis (the focus Trend style, see focus_trend: description, colour swatches, material texture chips, article
photos; the stock product matched to it: image, colour, material, fit, quantity, match reason; the LLM SWOT draft),
Design (novelty's product definition, LOOK visuals, specification, planning points) and Marketing (target,
sales & market, price, LLM promotion ideas, MD review points). Agent
metadata (Evidence IDs, stage statuses, run trace) stays in the stored run and the UI, not on the poster.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BoardClaim:
    text: str
    kind: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BoardSection:
    """One producer's part of the run as the poster reads it: its displayable claims and its notes
    (limitations, or why there is nothing to show)."""
    section_id: str
    claims: tuple[BoardClaim, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanningBoard:
    title: str
    season: str
    category: str
    review_label: str
    sections: tuple[BoardSection, ...]
    synthetic_context: bool = False


def _claim(text: object, kind: str, ids=()) -> BoardClaim:
    evidence_ids = tuple(dict.fromkeys(str(item) for item in ids if isinstance(item, str) and item.strip()))
    return BoardClaim(str(text), kind, evidence_ids)

SHEET_FORMAT = 'planning-sheet'
REVIEW_LABEL = 'MD 검토용 초안 · 자동 승인 아님'
FOOTER_NOTE = '검토용 초안 · 근거 미연결 값은 확정 사실이 아닙니다 · 승인·발주·생산 가능성 확정이 아닙니다.'
# orchestrator writes this limitation itself when the deterministic fixture image provider is used.
FIXTURE_IMAGE_MARKER = 'ORCHESTRATOR_WORKFLOW_IMAGE_PROVIDER=fixture'
# novelty writes this limitation into the Draft when its visuals come from the offline fixture provider.
DRAFT_FIXTURE_MARKER = 'NOVELTY_DRAFT_IMAGE_PROVIDER=fixture'
# workflow-contract 0.2.0: the two novelty visuals of a Draft (draft.visuals), in display order.
DRAFT_VISUALS = (('product', '신상품 · Product'), ('outfit', '착장 · Outfit'))
MAX_EMBEDDED_IMAGE_BYTES = 10 * 1024 * 1024

KIND_LABELS = {'observation': '관찰', 'evidence_observation': '관찰', 'interpretation': '해석', 'proposal': '제안',
               'user_requested': '요청', 'to_validate': '확인 필요', 'producer_finding': '에이전트 결과'}
DECISION_LABELS = {'price': '가격', 'cost': '원가', 'quantity': '수량', 'purchase_order': '발주', 'adoption': '최종 채택'}
METRIC_LABELS = {'current_company_sales': '현재 자사 판매'}


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


def _missing_section(result, stage_key, section_id, status, body=None) -> BoardSection:
    """A section without displayable content says why: the stage error, no_results, or not run."""
    errors = [error for error in _list(result.get('errors')) if _mapping(error).get('stage') == stage_key]
    limitations = _strings(_mapping(body).get('limitations'))
    if status == 'error' or errors:
        notes = tuple(f"{error.get('code', 'ERROR')}: {error.get('message', '')}".strip(': ') for error in errors)
        return BoardSection(section_id, (), notes + limitations or ('단계 오류',))
    if status == 'no_results':
        return BoardSection(section_id, (), limitations or ('생산자가 결과 없음(no_results)을 반환했습니다.',))
    return BoardSection(section_id, (), limitations or ('이 단계의 결과가 없습니다(실행되지 않았거나 이전 단계에서 중단).',))


def _trend(result: Mapping[str, Any]) -> BoardSection:
    """The Trend notes the poster shows when there is no Trend style to draw."""
    raw = _raw(result, 'trend')
    body = _mapping(raw.get('result')) if raw else {}
    if not body:
        return _missing_section(result, 'trend', 'trend', _status(result, 'trend', raw), body)
    return BoardSection('trend', (), _strings(body.get('limitations')))


def _unit_text(unit: Any) -> str:
    if unit == 'pieces':
        return '개'
    return f' {unit}' if isinstance(unit, str) and unit else ''


IMAGE_MEDIA = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}


def _product_image(product: Mapping[str, Any]) -> str | None:
    image = _mapping(product.get('image'))
    if image.get('media_type') in IMAGE_MEDIA and image.get('encoding') == 'base64' and isinstance(image.get('data'), str):
        return image['data']
    return None


def _inventory(result: Mapping[str, Any]) -> BoardSection:
    """The stock notes the poster shows when no product is matched to the Trend style."""
    raw = _raw(result, 'stock')
    body = _mapping(raw.get('result')) if raw else {}
    if not body:
        return _missing_section(result, 'stock', 'inventory', _status(result, 'stock', raw), body)
    notes = [note for slot in _list(body.get('trend_results')) for note in _strings(_mapping(slot).get('limitations'))]
    basis = _mapping(body.get('season_basis')).get('basis')
    if isinstance(basis, str) and basis.strip():
        notes.insert(0, f'시즌 기준: {basis.strip()}')
    return BoardSection('inventory', (), _unique(notes))


def _period_bounds(period: Any) -> tuple[str, str] | None:
    if isinstance(period, str) and period:
        start, _, end = period.partition('/')
        return start, end or start
    if isinstance(period, Mapping) and isinstance(period.get('start'), str):
        return period['start'], period.get('end') if isinstance(period.get('end'), str) else period['start']
    return None


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
        return _missing_section(result, 'sales_market_review', 'sales_market', status, body)
    review = _mapping(body.get('review'))
    # research's current 0.3 proposal response is its domain review directly, not the
    # earlier orchestrator full-Draft wrapper with review.findings/metrics.
    if not review and body.get('schema_version') == '1.0.0':
        review = body
    evidence = {item['evidence_id']: item for item in (_mapping(entry) for entry in _list(review.get('evidence')))
                if isinstance(item.get('evidence_id'), str)}
    claims = []
    kinds = {'fact': 'observation', 'interpretation': 'interpretation', 'proposal': 'proposal'}
    for finding in _list(review.get('findings')):
        finding = _mapping(finding)
        if isinstance(finding.get('text'), str) and finding['text'].strip():
            claims.append(_claim(finding['text'].strip(), kinds.get(finding.get('kind'), 'producer_finding'),
                                 _evidence_ids(finding.get('evidence_refs'))))
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
                                 'producer_finding' if ids else 'to_validate', ids))
            # Row values differ by dimensions that live only in the evidence (e.g. channel, article taxonomy),
            # so the sheet quotes the producer's metric definition instead of listing bare values side by side.
            cited = [evidence.get(i) for i in ids if isinstance(evidence.get(i), Mapping)]
            definitions = _unique(item.get('metric_definition') for item in cited)
            channels = _unique(str(item['channel']) for item in cited if isinstance(item.get('channel'), (str, int))
                               and not isinstance(item.get('channel'), bool))
            for definition in definitions[:2]:
                definition_ids = _unique(item.get('evidence_id') for item in cited if item.get('metric_definition') == definition)
                claims.append(_claim(f"{label} 정의" + (f" (원본 채널 ID {', '.join(channels)}별 행)" if channels else '') + f': {definition}',
                                     'producer_finding', definition_ids[:3]))
        for metric in missing:
            reason = metric.get('missing_reason') if isinstance(metric.get('missing_reason'), str) else '원천 없음'
            claims.append(_claim(f'{label}: 미확인 — {reason}', 'to_validate', _evidence_ids(metric.get('evidence_refs'))))
    # research 0.3 returns these native research summaries directly; it does not use the older review.findings/metrics
    # shape above. Summarize the actual values so the Marketing panel contains the evidence research analyzed.
    if body.get('schema_version') == '1.0.0':
        for row in (_mapping(item) for item in _list(body.get('category_price_comparison'))):
            sample_count = row.get('sample_count')
            period = _period_bounds(row.get('period'))
            stats = _mapping(row.get('scaled_price_statistics')) or _mapping(row.get('price_statistics'))
            unit = stats.get('currency') or stats.get('unit')
            price_bits = []
            if isinstance(sample_count, int) and not isinstance(sample_count, bool):
                price_bits.append(f'거래 표본 {sample_count:,}건')
            for key, label in (('median', '중앙값'), ('mean', '평균')):
                value = stats.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    formatted = f'{value:,.2f}'.rstrip('0').rstrip('.') if not float(value).is_integer() else f'{int(value):,}'
                    price_bits.append(f'{label} {formatted}{f" {unit}" if isinstance(unit, str) and unit else ""}')
                    if key == 'median':
                        break
            if period:
                price_bits.append(f'기간 {period[0]}–{period[1]}' if period[0] != period[1] else f'기간 {period[0]}')
            if price_bits:
                suffix = ' · ×590 연구 환산' if stats.get('scale_factor') == 590 else ''
                ids = tuple(value for value in _list(row.get('evidence_ids')) if isinstance(value, str))
                claims.append(_claim('H&M 과거 거래가 참고: ' + ' · '.join(price_bits) + suffix,
                                     'producer_finding', ids))

        for row in (_mapping(item) for item in _list(body.get('material_cost_analysis'))):
            sample_count = row.get('sample_count')
            per_meter_stats = _mapping(row.get('price_per_meter_krw_statistics'))
            stats = per_meter_stats
            if not stats:
                stats = _mapping(row.get('listed_price_statistics'))
            bits = []
            if isinstance(sample_count, int) and not isinstance(sample_count, bool):
                bits.append(f'공개 원단 목록 {sample_count:,}건')
            median = stats.get('median')
            unit = (stats.get('unit') or stats.get('currency') or
                    ('KRW/m' if per_meter_stats else row.get('original_unit') or row.get('currency')))
            if isinstance(median, (int, float)) and not isinstance(median, bool):
                shown = f'{median:,.2f}'.rstrip('0').rstrip('.') if not float(median).is_integer() else f'{int(median):,}'
                bits.append(f'중앙값 {shown}{f" {unit}" if isinstance(unit, str) and unit else ""}')
            material = row.get('material_query') or row.get('source_category')
            if isinstance(material, str) and material.strip():
                bits.insert(0, f'{material.strip()} 참고')
            if bits:
                ids = tuple(value for value in _list(row.get('evidence_ids')) if isinstance(value, str))
                claims.append(_claim('원단 공개 목록: ' + ' · '.join(bits), 'producer_finding', ids))

        for row in (_mapping(item) for item in _list(body.get('customer_segment_analysis'))):
            cohort = _mapping(row.get('target_age_cohort'))
            denominator = row.get('unique_customer_denominator')
            period = _period_bounds(row.get('period'))
            bits = []
            definition = _scalar_text(cohort.get('definition'))
            if definition:
                bits.append(f'최대 연령 코호트 {definition}')
            if isinstance(denominator, int) and not isinstance(denominator, bool):
                bits.append(f'연령 확인 고객 {denominator:,}명 기준')
            if period:
                bits.append(f'기간 {period[0]}–{period[1]}' if period[0] != period[1] else f'기간 {period[0]}')
            if bits:
                ids = tuple(value for value in _list(row.get('evidence_ids')) if isinstance(value, str))
                claims.append(_claim('H&M 과거 고객 연령 집계: ' + ' · '.join(bits), 'producer_finding', ids))

        for missing in _list(body.get('missing_data')):
            missing = _mapping(missing)
            label = missing.get('field') or missing.get('name') or missing.get('reason')
            if isinstance(label, str) and label.strip():
                reason = missing.get('reason')
                message = missing.get('message')
                detail = message.strip() if isinstance(message, str) and message.strip() else (
                    reason.strip() if isinstance(reason, str) and reason.strip() else None
                )
                text = '미확인: ' + label.strip() + (f' — {detail}' if detail else '')
                claims.append(_claim(text, 'to_validate', ()))

    limitations = _unique((*_strings(review.get('limitations')), *_strings(body.get('limitations'))))
    return BoardSection('sales_market', tuple(claims), limitations)


def _draft_parts(result: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, Mapping[str, Any], str]:
    raw = _raw(result, 'planning_draft')
    body = _mapping(raw.get('result')) if raw else {}
    return raw, body, _status(result, 'planning_draft', raw)


def _insight(result: Mapping[str, Any]) -> BoardSection:
    _, body, status = _draft_parts(result)
    draft = _mapping(body.get('draft'))
    if not draft:
        return _missing_section(result, 'planning_draft', 'insight', status, body)
    kinds = {'fact': 'observation', 'interpretation': 'interpretation', 'proposal': 'proposal'}
    claims = tuple(
        _claim(claim['text'].strip(), kinds.get(claim.get('kind'), 'to_validate'), _evidence_ids(claim.get('evidence_refs')))
        for claim in (_mapping(item) for item in _list(draft.get('claims')))
        if isinstance(claim.get('text'), str) and claim['text'].strip())
    if body.get('schema_version') == '1.0.0' and not claims:
        for key in ('opportunity', 'product_definition'):
            item = _mapping(draft.get(key))
            value = item.get('value')
            if isinstance(value, str) and value.strip():
                claims += (_claim(value.strip(), 'proposal', _strings(item.get('evidence_ids'))),)
    return BoardSection('insight', claims, _strings(draft.get('open_questions')))


def _host(url: Any) -> str:
    """The host of an http(s) URL, for the photo source line; '' for anything else."""
    try:
        parts = urlsplit(url) if isinstance(url, str) else None
    except ValueError:
        return ''
    return parts.hostname or '' if parts and parts.scheme in ('http', 'https') else ''


def _finance(result: Mapping[str, Any]) -> BoardSection:
    _, body, _ = _draft_parts(result)
    reserved = _strings(_mapping(body.get('draft')).get('md_decision_required')) or \
        _strings(_mapping(result.get('md_review')).get('reserved_decisions'))
    claims = tuple(_claim(f'{DECISION_LABELS.get(key, key)}: MD 판단 필요', 'to_validate', ()) for key in reserved)
    return BoardSection('finance', claims, ('원가 입력 또는 승인된 산식 미제공',))


# --- sheet model ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class DraftVisual:
    """One novelty visual (draft.visuals.product | outfit, workflow-contract 0.2.0)."""
    key: str
    label: str
    status: str | None
    artifact: Mapping[str, Any] | None
    model: str | None


@dataclass(frozen=True)
class SheetVisual:
    status: str | None
    artifact: Mapping[str, Any] | None
    # The novelty visuals shown in the visual panel; empty when the draft carries none (then one image panel
    # shows the final image, or a placeholder saying why there is none).
    draft_visuals: tuple[DraftVisual, ...] = ()
    draft_fixture: bool = False


@dataclass(frozen=True)
class PlanningSheet:
    board: PlanningBoard
    request_id: str
    contract_version: str | None
    schema_version: str | None
    visual: SheetVisual
    started_at: str | None
    # The one-product proposal poster reads these views: one Trend style, the stock product matched to it,
    # and novelty's concept for the new product.
    trend: 'TrendFocus | None' = None
    stock: 'StockFocus | None' = None
    concept_line: BoardClaim | None = None
    spec_rows: tuple['SpecRow', ...] = ()
    insight_points: tuple[str, ...] = ()
    review_items: tuple[str, ...] = ()
    price_rows: tuple[tuple[str, str], ...] = ()
    target: 'TargetView | None' = None
    concept: 'ConceptView | None' = None


@dataclass(frozen=True)
class TrendFocus:
    """The Trend style the poster is about (focus_trend)."""
    name: str
    seasons: str
    definition: str | None
    paragraphs: tuple[str, ...]
    colors: tuple[str, ...]
    materials: tuple[str, ...]
    features: tuple[str, ...]  # item names, fit, shape, patterns, details


@dataclass(frozen=True)
class StockFocus:
    """The inventory product matched to the poster's Trend style."""
    style_id: str
    category: str
    colors: tuple[str, ...]
    materials: tuple[str, ...]
    fit: str
    quantity: str
    basis: str  # as-of date, inventory definition, data nature
    image: str | None
    reason: str


@dataclass(frozen=True)
class SpecRow:
    label: str
    value: str
    kind: str
    evidence_ids: tuple[str, ...]


ATTRIBUTE_LABELS = {'color': '컬러', 'colors': '컬러', 'material': '소재', 'materials': '소재', 'fit': '핏', 'detail': '디테일',
                    'details': '디테일', 'pattern': '패턴', 'patterns': '패턴', 'neckline': '넥라인', 'length': '기장',
                    'shape': '실루엣', 'target_customer': '대상 고객'}


def _texts(value: Any) -> tuple[str, ...]:
    values = value if isinstance(value, (list, tuple)) else [value]
    return tuple(text for text in (_scalar_text(item) for item in values) if text)


DRAFT_CLAIM_KEYS = ('opportunity', 'product_definition', 'product_profile', 'attributes', 'concept')


def draft_trend_citations(result: Mapping[str, Any]) -> dict[str, int]:
    """Article id -> how often novelty's Draft claims cite it (evidence ids 'trend:<article>:...').

    Only the claims that define the proposed product count (DRAFT_CLAIM_KEYS), not the Draft's full evidence list.
    """
    raw = _raw(result, 'planning_draft')
    draft = _mapping(_mapping(raw.get('result')).get('draft')) if raw else {}
    counts: dict[str, int] = {}

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key == 'evidence_ids':
                    for evidence_id in _list(child):
                        parts = evidence_id.split(':') if isinstance(evidence_id, str) else []
                        if len(parts) > 2 and parts[0] == 'trend' and parts[1]:
                            counts[parts[1]] = counts.get(parts[1], 0) + 1
                else:
                    visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    for key in DRAFT_CLAIM_KEYS:
        visit(draft.get(key))
    return counts


def draft_trend_basis_id(result: Mapping[str, Any]) -> str | None:
    """The Trend style id novelty's Draft names as the basis of its plan
    (``draft.trend_basis.source_style_id``), or None when the Draft carries none (e.g. runs recorded before it)."""
    _, body, _ = _draft_parts(result)
    style_id = _mapping(_mapping(_mapping(body.get('draft')).get('trend_basis'))).get('source_style_id')
    return style_id if isinstance(style_id, str) and style_id.strip() else None


def focus_trend(result: Mapping[str, Any]) -> Mapping[str, Any]:
    """The Trend style the one-product proposal is about. Every reader of the focus style goes through this
    function: the poster's Analysis column, its stock card (focus_stock), the trend photos and the SWOT input.

    novelty selects one primary trend by its rule and records it in the Draft as
    ``draft.trend_basis.source_style_id`` (user decision 2026-11-17); the focus is the Trend style with that id.
    A Draft without that id (runs recorded before it), or whose id matches no style of the stored Trend result,
    keeps the earlier rule: the Trend result has no defined order (trend does not order its styles), so the focus is the
    style whose article the Draft cites most; the first style when the Draft cites none (or on a tie).
    """
    raw = _raw(result, 'trend')
    trends = [_mapping(trend) for trend in _list(_mapping(raw.get('result')).get('trends'))] if raw else []
    if not trends:
        return {}
    basis = draft_trend_basis_id(result)
    if basis is not None:
        # novelty keeps the style id as str(style['id']) (TrendCandidate.source_style_id); compare the same way.
        named = next((trend for trend in trends if _mapping(trend.get('style')).get('id') is not None
                      and str(_mapping(trend.get('style'))['id']) == basis), None)
        if named is not None:
            return named
    citations = draft_trend_citations(result)

    def article(trend: Mapping[str, Any]) -> str | None:
        style = _mapping(trend.get('style'))
        if style.get('article_id') is not None:
            return str(style['article_id'])
        style_id = style.get('id')
        return style_id.split('-')[0] if isinstance(style_id, str) and style_id else None

    best = max(range(len(trends)), key=lambda index: (citations.get(article(trends[index]) or '', 0), -index))
    return trends[best]


def trend_image_urls(result: Mapping[str, Any], limit: int = 3) -> tuple[str, ...]:
    """Example photo URLs of the focus style: style.example_images first, then its items' image_urls."""
    trend = focus_trend(result)
    style = _mapping(trend.get('style'))
    urls = [_mapping(item).get('url') for item in _list(style.get('example_images'))]
    urls += [url for item in _list(trend.get('items')) for url in _list(_mapping(item).get('image_urls'))]
    return tuple(dict.fromkeys(url.strip() for url in urls if isinstance(url, str) and url.strip()))[:limit]


def _trend_focus(result: Mapping[str, Any]) -> TrendFocus | None:
    trend = focus_trend(result)
    if not trend:
        return None
    style = _mapping(trend.get('style'))
    items = [_mapping(item) for item in _list(trend.get('items'))]
    paragraphs = tuple(entry['text'].strip() for entry in (_mapping(e) for e in _list(style.get('description')))
                       if isinstance(entry.get('text'), str) and entry['text'].strip())
    colors = _unique(text for item in items for text in _texts(item.get('colors')))
    materials = _unique(text for item in items for text in _texts(item.get('materials')))
    features = _unique([*(item['name'].strip() for item in items if isinstance(item.get('name'), str) and item['name'].strip()),
                        *(text for item in items for key in ('fit', 'shape', 'patterns', 'details') for text in _texts(item.get(key)))])
    definition = style.get('definition')
    return TrendFocus(
        name=style['name'].strip() if isinstance(style.get('name'), str) and style['name'].strip() else '트렌드 이름 미제공',
        seasons=' · '.join(text for text in (_season_text(s) for s in _list(style.get('seasons'))) if text),
        definition=definition.strip() if isinstance(definition, str) and definition.strip() else None,
        paragraphs=paragraphs, colors=colors, materials=materials, features=features)


def focus_stock(result: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """The inventory slot (trend_results[]) and product matched to the focus Trend style, or None."""
    raw = _raw(result, 'stock')
    body = _mapping(raw.get('result')) if raw else {}
    products = _list(body.get('products'))
    slots = [_mapping(slot) for slot in _list(body.get('trend_results'))]
    style = _mapping(focus_trend(result).get('style'))
    wanted = {value for value in (style.get('id'), style.get('name')) if isinstance(value, str) and value}

    def product_of(slot):
        index = slot.get('product_index')
        valid = isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(products)
        return _mapping(products[index]) if valid else {}

    # The focus style's own slot first; a slot that only shares its name comes after it (two styles can share one,
    # e.g. two '스커트' styles from different articles).
    matched = sorted((slot for slot in slots
                      if {_mapping(slot.get('style')).get('id'), _mapping(slot.get('style')).get('name')} & wanted),
                     key=lambda slot: _mapping(slot.get('style')).get('id') != style.get('id'))
    slot = next((s for s in matched if product_of(s)), None) or next((s for s in slots if product_of(s)), None)
    return (slot, product_of(slot)) if slot is not None else None


def _stock_focus(result: Mapping[str, Any]) -> StockFocus | None:
    focus = focus_stock(result)
    if focus is None:
        return None
    slot, product = focus
    attributes = _mapping(product.get('attributes'))
    inventory = _mapping(product.get('inventory'))
    quantity = inventory.get('quantity')
    stock = (f'{quantity:,}{_unit_text(inventory.get("unit"))}' if isinstance(quantity, int) and not isinstance(quantity, bool)
             else '수량 미확인')
    basis = ' · '.join(text for text in (f'기준 {inventory["as_of"]}' if isinstance(inventory.get('as_of'), str) else '',
                                         inventory['definition'] if isinstance(inventory.get('definition'), str) else '',
                                         inventory['data_nature'] if isinstance(inventory.get('data_nature'), str) else '') if text)
    reason = slot.get('summary').strip() if isinstance(slot.get('summary'), str) and slot['summary'].strip() else ''
    return StockFocus(style_id=_scalar_text(product.get('style_id')) or '상품 식별자 미제공',
                      category=_scalar_text(product.get('category')), colors=_texts(attributes.get('color')),
                      materials=_texts(attributes.get('material')), fit=_scalar_text(attributes.get('fit')),
                      quantity=stock, basis=basis, image=_product_image(product), reason=reason)


PROFILE_FIELDS = (('category', '카테고리'), ('season', '시즌'), ('product_type', '상품 종류'), ('gender', '성별'),
                  ('silhouette', '실루엣'), ('color', '컬러'), ('material', '소재'))
GENDER_LABELS = {'women': '여성', 'men': '남성', 'unisex': '공용'}


@dataclass(frozen=True)
class ConceptView:
    """novelty's concept as the Design column shows it."""
    line: BoardClaim | None  # product definition
    opportunity: str | None
    findings: tuple[str, ...]
    profile: tuple[SpecRow, ...]  # draft.product_profile: the new product's own specification
    differentiation: tuple[str, ...]
    own_products: tuple[str, ...]
    points: tuple[str, ...]
    questions: tuple[str, ...]


def _claim_value(value: Any) -> str | None:
    """Text of a SourcedClaim {value, kind, evidence_ids} or of a plain string."""
    text = _scalar_text(_mapping(value).get('value') if isinstance(value, Mapping) else value)
    return text or None


def _claim_kind(value: Any) -> str:
    kind = _mapping(value).get('kind')
    return kind if kind in KIND_LABELS else 'proposal'


# A parenthesised snake_case source id inside novelty sentences, e.g. "화이트 (trend_analysis_result)".
_SOURCE_TAG = re.compile(r'\s*\((?:[a-z0-9]+_)+[a-z0-9]+\)')


def _drop_source_tags(text: str | None) -> str | None:
    """The sentence without its parenthesised source ids; the ids stay in the stored run."""
    return _SOURCE_TAG.sub('', text).strip() if isinstance(text, str) else text


def _concept_view(result: Mapping[str, Any]) -> ConceptView:
    """Reads the current novelty Draft (top-level product_definition, product_profile, opportunity, ...)
    and, for runs recorded before that shape, the earlier draft.concept / draft.claims."""
    _, body, _ = _draft_parts(result)
    draft = _mapping(body.get('draft'))
    concept = _mapping(draft.get('concept'))
    line = None
    for definition in (draft.get('product_definition'), concept.get('product_definition')):
        text = _claim_value(definition)
        if text:
            line = _claim(_drop_source_tags(text), _claim_kind(definition), _strings(_mapping(definition).get('evidence_ids')))
            break
    profile = _mapping(draft.get('product_profile'))
    rows = []
    for key, label in PROFILE_FIELDS:
        item = _mapping(profile.get(key))
        text = _claim_value(item)
        if not text or item.get('kind') == 'unknown':
            continue
        rows.append(SpecRow(label, GENDER_LABELS.get(text, text) if key == 'gender' else text, _claim_kind(item),
                            _strings(item.get('evidence_ids'))))
    for item in (_mapping(entry) for entry in _list(profile.get('details'))):
        text = _scalar_text(item.get('value'))
        if text and item.get('kind') != 'unknown':
            name = item.get('name') if isinstance(item.get('name'), str) and item['name'].strip() else 'detail'
            rows.append(SpecRow(ATTRIBUTE_LABELS.get(name, name), text, _claim_kind(item), _strings(item.get('evidence_ids'))))
    opportunity = _claim_value(draft.get('opportunity')) or _claim_value(concept.get('opportunity'))
    findings = _unique(_claim_value(_mapping(item).get('statement')) for item in _list(draft.get('opportunity_findings')))
    differentiation = _unique(_claim_value(item) for item in
                              [*_list(draft.get('differentiation')), *_list(concept.get('differentiation'))])
    own_products = _unique(_claim_value(item) for item in _list(draft.get('relation_to_own_products')))
    analysis = _mapping(body.get('planning_analysis'))
    points = _unique(_claim_value(item) for item in [*_list(analysis.get('proposals')), *_list(analysis.get('interpretations'))])
    points = tuple(point for point in points if point not in (opportunity, line.text if line else None))
    if not points:  # runs recorded before planning_analysis: the Draft's claims
        claims = [_mapping(claim) for claim in _list(draft.get('claims'))]
        texts = lambda kinds: [c['text'].strip() for c in claims  # noqa: E731
                               if c.get('kind') in kinds and isinstance(c.get('text'), str) and c['text'].strip()]
        points = _unique(texts(('proposal', 'interpretation'))) or _unique(texts(('fact',)))
    questions = _unique((*_strings(draft.get('open_questions')), *_strings(draft.get('questions_for_validation')),
                         *_strings(concept.get('questions_for_validation'))))
    clean = lambda texts: tuple(_drop_source_tags(text) for text in texts)  # noqa: E731
    return ConceptView(line, _drop_source_tags(opportunity), clean(findings), tuple(rows), clean(differentiation),
                       clean(own_products), clean(points), questions)


def _price_rows(result: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """research price, cost and margin values exactly as reported (research review result); absent values are not shown."""
    raw = _raw(result, 'sales_market_review')
    body = _mapping(raw.get('result')) if raw else {}
    units = {'KRW': '원', 'KRW/m': '원/m', 'USD': ' USD'}
    rows = []
    for key, field, label in (('price_analysis', 'proposed_price', '제안 판매가'),
                              ('fabric_cost_analysis', 'unit_cost', '소재 원가')):
        item = _mapping(body.get(key))
        value = item.get(field)
        if isinstance(value, bool) or value is None:
            continue
        unit = item.get('unit') or item.get('currency')
        suffix = '%' if field == 'margin_rate_percent' else units.get(unit, f' {unit}' if isinstance(unit, str) and unit else '')
        if isinstance(value, (int, float)):
            shown = f'{int(value):,}' if float(value).is_integer() else f'{value:,}'
            rows.append((label, shown + suffix))
        elif isinstance(value, str) and value.strip():
            rows.append((label, value.strip() + suffix))
    margin = _mapping(body.get('margin_analysis'))
    margin_rate = margin.get('margin_rate_percent')
    if isinstance(margin_rate, (int, float, str)) and not isinstance(margin_rate, bool):
        benchmark = (margin.get('estimate_type') == 'industry_average_gross_margin_benchmark'
                     or margin.get('status') in {'benchmark_only', 'benchmark_estimate'}
                     or bool(margin.get('benchmark_source')))
        if benchmark:
            label = '업계 평균 마진 참고치'
        else:
            label = '마진율'
        suffix = '%' if isinstance(margin_rate, (int, float)) or '%' not in margin_rate else ''
        shown = f'{margin_rate:,.2f}'.rstrip('0').rstrip('.') if isinstance(margin_rate, float) else str(margin_rate)
        rows.append((label, shown + suffix))
    return tuple(rows)


@dataclass(frozen=True)
class TargetView:
    """research's target audience: the H&M age cohort (research aggregate) and the LLM target hypothesis."""
    age: str | None
    age_note: str | None
    primary: str | None
    summary: str | None
    secondary: tuple[str, ...]
    occasions: tuple[str, ...]
    missing: str | None


def _target_view(result: Mapping[str, Any]) -> TargetView | None:
    raw = _raw(result, 'sales_market_review')
    body = _mapping(raw.get('result')) if raw else {}
    target = _mapping(body.get('target_audience')) or _mapping(body.get('target_audience_analysis'))
    if not target:
        return None
    cohort = _mapping(target.get('age_cohort'))
    age = age_note = None
    if cohort:
        age = _scalar_text(cohort.get('definition')) or _scalar_text(cohort.get('age_band')) or None
        share = cohort.get('share_of_customers_with_known_age')
        if cohort.get('suppressed') is True:
            age_note = '표본이 적어 비율 비공개'
        elif isinstance(share, (int, float)) and not isinstance(share, bool):
            age_note = f'연령 확인 고객의 {share * 100:.1f}%'
    hypothesis = target.get('proposal')
    hypothesis = hypothesis if isinstance(hypothesis, Mapping) else ({'proposal': hypothesis} if isinstance(hypothesis, str) else {})
    fit = _mapping(hypothesis.get('product_fit'))
    limitations = _strings(target.get('limitations'))
    return TargetView(age=age, age_note=age_note,
                      primary=_scalar_text(hypothesis.get('primary_segment')) or None,
                      summary=_scalar_text(hypothesis.get('proposal')) or None,
                      secondary=_texts(hypothesis.get('secondary_segments')),
                      occasions=_texts(fit.get('wearing_occasions')),
                      missing=limitations[0] if limitations and not (age or hypothesis) else None)


def _synthetic(result: Mapping[str, Any]) -> bool:
    """Whether any source reports synthetic or sample data (the header badge)."""
    for key in ('trend', 'stock', 'planning_draft', 'sales_market_review'):
        source = _mapping(_mapping(result.get('sources')).get(key))
        observation = _mapping(source.get('observation'))
        natures = {_nature(observation)}
        if key == 'sales_market_review':
            review = _mapping(_mapping(_mapping(source.get('raw_response')).get('result')).get('review'))
            natures |= {_nature(_mapping(metric).get('observation')) for metric in _list(review.get('metrics'))}
        if natures & {'synthetic', 'sample'} or observation.get('synthetic') is True:
            return True
    return False


def build_sheet(envelope: Mapping[str, Any]) -> PlanningSheet:
    """Map a stored integrated-result envelope onto the poster model (presentation only)."""
    envelope = _mapping(envelope)
    result = _mapping(envelope.get('result'))
    if not result:
        raise PlanningSheetError('PLANNING_SHEET_INPUT_INVALID', 422)
    sections = (_trend(result), _inventory(result), _sales(result), _insight(result), _finance(result))
    _, draft_body, _ = _draft_parts(result)
    draft = _mapping(draft_body.get('draft'))
    context = _mapping(_mapping(result.get('planning_request')).get('context'))
    season = _scalar_text(draft.get('season')) or _scalar_text(context.get('season')) or '시즌 미정'
    category = _scalar_text(draft.get('category')) or _scalar_text(context.get('category')) or '카테고리 미정'
    title = draft.get('title') if isinstance(draft.get('title'), str) and draft['title'].strip() else f'{season} {category} 신상품 기획안'
    fixture = any(FIXTURE_IMAGE_MARKER in text for text in _strings(result.get('limitations')))
    image = _mapping(result.get('final_image'))
    visuals = _mapping(draft.get('visuals'))
    text_of = lambda value: value if isinstance(value, str) else None  # noqa: E731
    draft_visuals = tuple(
        DraftVisual(key=key, label=label, status=text_of(_mapping(visuals.get(key)).get('status')),
                    artifact=_mapping(visuals.get(key)).get('artifact') if isinstance(_mapping(visuals.get(key)).get('artifact'), Mapping) else None,
                    model=text_of(_mapping(visuals.get(key)).get('model')))
        for key, label in DRAFT_VISUALS) if visuals else ()
    visual = SheetVisual(
        status=text_of(image.get('status')),
        artifact=image.get('artifact') if isinstance(image.get('artifact'), Mapping) else None,
        draft_visuals=draft_visuals,
        draft_fixture=any(DRAFT_FIXTURE_MARKER in text for text in _strings(draft.get('limitations'))))
    traced = [stage for stage in _list(result.get('stages')) if isinstance(stage, Mapping)]
    started = min((s['started_at'] for s in traced if isinstance(s.get('started_at'), str)), default=None)
    board = PlanningBoard(title=title.strip(), season=season, category=category, review_label=REVIEW_LABEL,
                          sections=sections, synthetic_context=_synthetic(result) or fixture)
    request_id = result.get('request_id') or envelope.get('request_id')
    concept = _concept_view(result)
    return PlanningSheet(
        trend=_trend_focus(result), stock=_stock_focus(result), concept_line=concept.line, spec_rows=concept.profile,
        insight_points=concept.points, review_items=concept.questions, price_rows=_price_rows(result),
        concept=concept, target=_target_view(result),
        board=board, request_id=request_id if isinstance(request_id, str) else '—',
        contract_version=text_of(envelope.get('contract_version')), schema_version=text_of(result.get('schema_version')),
        visual=visual, started_at=started)


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
        self.regular = _pick(_REGULAR, environ.get('ORCHESTRATOR_PLANNING_SHEET_FONT'))
        self.bold = _pick(_BOLD, environ.get('ORCHESTRATOR_PLANNING_SHEET_FONT_BOLD')) or self.regular
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
                font.set_variation_by_name({'display_bold': 'SemiBold', 'display_heavy': 'Bold'}.get(role, 'Medium'))
            else:
                path, index = self.bold if role in ('bold', 'display_bold', 'display_heavy') else self.regular
                font = self._truetype(path, size, index=index)
            self._cache[key] = font
        return self._cache[key]


# --- rendering -----------------------------------------------------------------------------------

C = {
    'page': '#EEEDEA', 'header': '#DCDAD6', 'card_grey': '#E3E2DF', 'paper': '#FFFFFF', 'ink': '#161616', 'ink2': '#474747',
    'ink3': '#8A8A8A', 'rule': '#D5D3CF', 'rule_s': '#B8B6B1', 'accent': '#1A3A28', 'accent_lt': '#E1EAE3', 'red': '#C0392B',
    'yellow': '#E6D45A', 'yellow_lt': '#FBF8E4', 'amber': '#92400E', 'amber_lt': '#FCEBC5', 'placeholder': '#D9D7D2',
    'swot_s': '#2D7A45', 'swot_s_lt': '#E6F2EA', 'swot_w': '#B0392B', 'swot_w_lt': '#FBEDEB', 'swot_o': '#1A6FAF',
    'swot_o_lt': '#E9F1F8', 'swot_t': '#8A6D00', 'swot_t_lt': '#FAF5DC',
    'col_analysis': '#3E6B50', 'col_design': '#8F6A48', 'col_marketing': '#4A6A90',
}
COLUMN_TONES = ('col_analysis', 'col_design', 'col_marketing')
HEADER_H = 120   # banner height in design units
BANNER_SLOGAN = 'Reframe the Everyday'  # fixed banner centre line on every poster
BANNER_SUBTITLE = '일상을 새로운 시선으로 MERCHANDISING PROPOSAL'
COL3_SCALE = 1.2
SWOT_CELLS = (('strengths', 'S · 강점', 'swot_s'), ('weaknesses', 'W · 약점', 'swot_w'),
              ('opportunities', 'O · 기회', 'swot_o'), ('threats', 'T · 위협', 'swot_t'))

W = 1600          # design width; every coordinate below is in design units
SCALE = 2         # device pixels per design unit (3200 px wide PNG)


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
        return role if text.isascii() and self.fonts.display else {'display': 'regular', 'display_bold': 'bold', 'display_heavy': 'bold'}.get(role, role)

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
        canvas = self.Image.new('RGB', (W * SCALE, round(height * SCALE)), C['page'])
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
                box = (s(w), s(h))
                try:
                    with self.Image.open(io.BytesIO(data)) as source:
                        # Shrink to the slot before converting, so a large photo never sits in memory at full size as RGBA.
                        width, height = source.size
                        scale = (max if mode == 'cover' else min)(box[0] / width, box[1] / height)
                        if scale < 1:
                            target = (max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale)))
                            source.draft('RGB', target)  # JPEG decodes at a reduced scale; other formats ignore this
                            source.load()
                            picture = source.resize(target, self.Image.LANCZOS).convert('RGBA')
                        else:
                            source.load()
                            picture = source.convert('RGBA')
                except Exception:  # undecodable bytes: draw the failure, never crash the sheet
                    draw.text((s(x) + 8, s(y) + 8), '이미지 읽기 실패', font=self.fonts.get('regular', s(12)), fill=C['red'])
                    continue
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


def _decode_image(value: str | None) -> bytes | None:
    if not isinstance(value, str):
        return None
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    return data if 0 < len(data) <= MAX_EMBEDDED_IMAGE_BYTES else None


def _image_size(data: bytes | None) -> tuple[int, int] | None:
    if not data:
        return None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as picture:
            return picture.size
    except Exception:  # undecodable bytes are drawn as a failure later; layout falls back to side by side
        return None


def _any_landscape(images: Mapping[str, bytes | None]) -> bool:
    """True when an embedded novelty visual is wider than tall (the outfit is 1536x1024): then the LOOK panels are stacked
    full width, since side by side a landscape image would shrink to a strip."""
    sizes = [_image_size(data) for data in images.values() if data]
    return any(size is not None and size[0] > size[1] for size in sizes)


# --- swatches and textures -------------------------------------------------------------------------

# Colour names as the producers write them → swatch colour. A name not in the table is shown as text only.
SWATCHES = {
    '블랙': '#1C1C1C', '검정': '#1C1C1C', 'black': '#1C1C1C', '화이트': '#F7F7F4', '흰색': '#F7F7F4', 'white': '#F7F7F4',
    '오프화이트': '#F2EFE6', '아이보리': '#F1EBDA', 'ivory': '#F1EBDA', '크림': '#EFE4CB', 'cream': '#EFE4CB',
    '오트밀': '#E3D8C3', '베이지': '#D8C3A0', 'beige': '#D8C3A0', '샌드': '#D6C09A', '카멜': '#B98D5A', 'camel': '#B98D5A',
    '탄': '#C2A27A', '브라운': '#6E4B33', 'brown': '#6E4B33', '다크브라운': '#4A3123', '초콜릿': '#4A3123', '모카': '#7B5B47',
    '카키': '#6E6B43', 'khaki': '#6E6B43', '올리브': '#68703F', 'olive': '#68703F', '그레이': '#9B9B9B', '회색': '#9B9B9B',
    'gray': '#9B9B9B', 'grey': '#9B9B9B', '라이트그레이': '#C9C9C9', '멜란지': '#B7B5B0', '차콜': '#3C3C3E', 'charcoal': '#3C3C3E',
    '네이비': '#1F2B47', 'navy': '#1F2B47', '블루': '#2F5DA8', 'blue': '#2F5DA8', '스카이블루': '#9CC3E6', '하늘색': '#9CC3E6',
    '데님': '#4A6A8F', 'denim': '#4A6A8F', '인디고': '#2E3C6E', '그린': '#3F7D4E', 'green': '#3F7D4E', '민트': '#A8D8C4',
    '레드': '#B22D2D', 'red': '#B22D2D', '버건디': '#6E1F2B', 'burgundy': '#6E1F2B', '와인': '#6B1E33', '핑크': '#E8A5B5',
    'pink': '#E8A5B5', '라벤더': '#BBA8D6', '퍼플': '#6E4A8E', 'purple': '#6E4A8E', '옐로우': '#E9C84A', '노랑': '#E9C84A',
    'yellow': '#E9C84A', '머스타드': '#C99A2E', '오렌지': '#E07A2E', 'orange': '#E07A2E', '테라코타': '#B8603E', '코랄': '#E9806E',
    '실버': '#BFC1C2', 'silver': '#BFC1C2', '골드': '#C8A951', 'gold': '#C8A951',
}
# Material words → texture pattern drawn on the chip (a schematic hint, not a photo of the fabric).
TEXTURES = (('knit', ('니트', '울', '캐시미어', '모헤어', '앙고라', '알파카', 'wool', 'knit', 'cashmere')),
            ('denim', ('데님', '진', 'denim')),
            ('corduroy', ('코듀로이', '골덴', 'corduroy')),
            ('leather', ('가죽', '레더', '스웨이드', 'leather', 'suede')),
            ('silk', ('실크', '새틴', 'silk', 'satin')),
            ('lace', ('레이스', 'lace')),
            ('tweed', ('트위드', '부클', 'tweed', 'boucle')),
            ('fur', ('퍼', '시어링', '플리스', '무스탕', 'fur', 'fleece', 'shearling')),
            ('linen', ('린넨', '리넨', '마', 'linen')),
            ('woven', ('면', '코튼', '옥스포드', 'cotton')),
            ('synthetic', ('합성', '폴리', '나일론', '아크릴', '레이온', 'poly', 'nylon', 'acrylic')))


def swatch_color(name: str) -> str | None:
    key = name.replace(' ', '').lower()
    if key in SWATCHES:
        return SWATCHES[key]
    for word in sorted(SWATCHES, key=len, reverse=True):  # '다크 브라운' → 다크브라운, '라이트 베이지' → 베이지
        if word in key:
            return SWATCHES[word]
    return None


def texture_kind(name: str) -> str:
    key = name.replace(' ', '').lower()
    return next((kind for kind, words in TEXTURES if any(word in key for word in words)), 'plain')


_TEXTURE_CACHE: dict[str, bytes] = {}


def _texture_png(kind: str) -> bytes:
    """A small schematic pattern for a material chip, drawn once per kind."""
    if kind in _TEXTURE_CACHE:
        return _TEXTURE_CACHE[kind]
    from PIL import Image, ImageDraw
    size = 96
    base, line = {'knit': ('#E9E1D3', '#B9A98E'), 'denim': ('#5B7899', '#3E5878'), 'corduroy': ('#B48C62', '#8E6A45'),
                  'leather': ('#7A5238', '#5E3E2A'), 'silk': ('#ECE6EE', '#C9BED0'), 'lace': ('#F6F3EE', '#BDB3A6'),
                  'tweed': ('#C9BCA8', '#6F6253'), 'fur': ('#D9CCBA', '#A8967F'), 'linen': ('#E7DFCF', '#BFB29A'),
                  'woven': ('#EEEAE2', '#C4BBAB'), 'synthetic': ('#E3E6EA', '#B5BCC6'), 'plain': ('#ECEAE6', '#C8C4BC')}[kind]
    image = Image.new('RGB', (size, size), base)
    draw = ImageDraw.Draw(image)
    if kind == 'knit':  # stacked V stitches
        for y in range(0, size, 12):
            for x in range(0, size, 12):
                draw.line((x, y, x + 6, y + 10), fill=line, width=2)
                draw.line((x + 6, y + 10, x + 12, y), fill=line, width=2)
    elif kind in ('denim', 'synthetic'):  # twill diagonals
        step = 7 if kind == 'denim' else 10
        for offset in range(-size, size, step):
            draw.line((offset, size, offset + size, 0), fill=line, width=2 if kind == 'denim' else 1)
    elif kind == 'corduroy':  # vertical wales
        for x in range(0, size, 8):
            draw.rectangle((x, 0, x + 4, size), fill=line)
    elif kind == 'leather':  # pebble grain
        for y in range(4, size, 14):
            for x in range(4 + (y // 14 % 2) * 7, size, 14):
                draw.ellipse((x, y, x + 6, y + 5), outline=line, width=1)
    elif kind == 'silk':  # sheen waves
        for y in range(6, size, 14):
            draw.arc((-20, y - 10, size // 2 + 10, y + 10), 0, 180, fill=line, width=2)
            draw.arc((size // 2 - 10, y - 10, size + 20, y + 10), 180, 360, fill=line, width=2)
    elif kind == 'lace':  # open rings
        for y in range(6, size, 16):
            for x in range(6 + (y // 16 % 2) * 8, size, 16):
                draw.ellipse((x, y, x + 9, y + 9), outline=line, width=2)
    elif kind == 'tweed':  # flecks
        for index in range(220):
            x, y = (index * 37) % size, (index * 53 + index // 7) % size
            draw.line((x, y, x + 3, y + (index % 3) - 1), fill=line if index % 4 else '#A0523D', width=2)
    elif kind == 'fur':  # pile strokes
        for y in range(0, size, 8):
            for x in range((y // 8 % 2) * 4, size, 8):
                draw.line((x, y, x + 3, y + 7), fill=line, width=2)
    elif kind in ('linen', 'woven'):  # plain weave grid
        step = 6 if kind == 'linen' else 8
        for x in range(0, size, step):
            draw.line((x, 0, x, size), fill=line, width=1)
        for y in range(0, size, step):
            draw.line((0, y, size, y), fill=line, width=1)
    else:
        for y in range(6, size, 12):
            for x in range(6, size, 12):
                draw.point((x, y), fill=line)
    out = io.BytesIO()
    image.save(out, format='PNG')
    _TEXTURE_CACHE[kind] = out.getvalue()
    return _TEXTURE_CACHE[kind]


# --- poster layout -----------------------------------------------------------------------------------

MARGIN, GUTTER = 30, 30
COLUMN_W = (W - 2 * MARGIN - 2 * GUTTER) / 3
COLUMNS = tuple((MARGIN + index * (COLUMN_W + GUTTER), COLUMN_W) for index in range(3))
COLUMN_TITLES = ('Analysis', 'Design', 'Marketing')


def _heading(c: _Canvas, x: float, y: float, w: float, title: str, *, scale: float = 1.0) -> float:
    s = 15 * scale
    c.text(x, y, title, 'bold', s, 'ink')
    start = x + c.width(title, 'bold', s) + 12
    c.rect(start, y + 11 * scale, x + w - start, 1.2, 'ink2')
    return y + 30 * scale


def _paragraph(c: _Canvas, x: float, y: float, w: float, text: str, *, size: float = 11.5, lines: int = 6,
               color: str = 'ink2', role: str = 'regular', leading: float = 1.55) -> float:
    for line in c.wrap(text, role, size, w, lines):
        c.text(x, y, line, role, size, color)
        y += size * leading
    return y


def _bullets(c: _Canvas, x: float, y: float, w: float, items: Sequence[str], *, limit: int, lines: int = 3,
             mark: str = 'dot', scale: float = 1.0) -> float:
    indent = 20 * scale
    for item in items[:limit]:
        if mark == 'warn':
            r = 14 * scale
            fs = 8.5 * scale
            c.rect(x, y + 2, r, r, 'amber_lt', r / 2)
            c.text(x + r / 2 - c.width('!', 'bold', fs) / 2, y + 3, '!', 'bold', fs, 'amber')
        else:
            ds = 5 * scale
            c.rect(x + 3 * scale, y + 7 * scale, ds, ds, 'ink', 3 * scale)
        y = _paragraph(c, x + indent, y, w - indent, item, size=11 * scale, lines=lines) + 6 * scale
    if len(items) > limit:
        fs = 9.5 * scale
        c.text(x + indent, y, f'외 {len(items) - limit:,}건 — 전체는 실행 기록(JSON)에 있습니다', 'regular', fs, 'ink3')
        y += 18 * scale
    return y


def _label(c: _Canvas, x: float, y: float, text: str, *, scale: float = 1.0) -> None:
    c.text(x, y, text, 'display_bold', 9.5 * scale, 'ink3')


def _swatches(c: _Canvas, x: float, y: float, w: float, names: Sequence[str]) -> float:
    """Colour circles with their names; a name outside the swatch table is a plain text chip."""
    px, row_h = x, 56
    for name in names[:8]:
        colour = swatch_color(name)
        label_w = c.width(name, 'regular', 9.5)
        width = max(30, label_w) + 12 if colour else label_w + 20
        if px + width > x + w and px > x:
            px, y = x, y + row_h
        if colour:
            centre = px + width / 2
            c.rect(centre - 13, y, 26, 26, colour, 13, 'rule_s')
            c.text(centre - label_w / 2, y + 30, name, 'regular', 9.5, 'ink2')
        else:
            c.rect(px, y + 4, width, 20, 'paper', 10, 'rule_s')
            c.text(px + 10, y + 7, name, 'regular', 9.5, 'ink2')
        px += width + 6
    return y + row_h


def _textures(c: _Canvas, x: float, y: float, w: float, names: Sequence[str]) -> float:
    px, row_h = x, 74
    for name in names[:6]:
        label_w = c.width(name, 'regular', 9.5)
        width = max(44, label_w)
        if px + width > x + w and px > x:
            px, y = x, y + row_h
        tile = px + (width - 44) / 2
        c.rect(tile - 1, y - 1, 46, 46, 'rule_s', 6)
        c.image(tile, y, 44, 44, _texture_png(texture_kind(name)), 'cover')
        c.text(px + (width - label_w) / 2, y + 48, name, 'regular', 9.5, 'ink2')
        px += width + 12
    return y + row_h


def _pills(c: _Canvas, x: float, y: float, w: float, items: Sequence[str], limit: int = 10, *,
           scale: float = 1.0) -> float:
    px = x
    s = 10 * scale
    pad = 10 * scale
    for text in items[:limit]:
        text = c.wrap(text, 'regular', s, w - 2 * pad, 1)[0]
        width = c.width(text, 'regular', s) + 2 * pad
        h = 20 * scale
        if px + width > x + w and px > x:
            px, y = x, y + 26 * scale
        c.rect(px, y, width, h, 'accent_lt', h / 2)
        c.text(px + pad, y + 3 * scale, text, 'regular', s, 'accent')
        px += width + 6
    return y + 28 * scale


def _photo(c: _Canvas, x: float, y: float, w: float, h: float, data: bytes | None, mode: str = 'cover',
           empty: str = '이미지 없음') -> None:
    if data:
        c.rect(x, y, w, h, 'paper')
        c.image(x, y, w, h, data, mode)
    else:
        c.rect(x, y, w, h, 'placeholder')
        c.text(x + (w - c.width(empty, 'regular', 10.5)) / 2, y + h / 2 - 7, empty, 'regular', 10.5, 'ink3')


# --- Analysis ----------------------------------------------------------------------------------------

def _analysis(c: _Canvas, x: float, y: float, w: float, sheet: PlanningSheet,
              photos: Sequence[tuple[bytes, str]], swot: Mapping[str, Any] | None = None) -> float:
    trend = sheet.trend
    sections = {section.section_id: section for section in sheet.board.sections}
    y = _heading(c, x, y, w, '소비 트렌드')
    if trend is None:
        y = _paragraph(c, x, y, w, sections['trend'].notes[0] if sections['trend'].notes else '트렌드 결과 없음', lines=3)
    else:
        c.text(x, y, c.wrap(trend.name, 'bold', 21, w, 1)[0], 'bold', 21, 'ink')
        y += 32
        if trend.seasons:
            c.text(x, y, trend.seasons, 'display', 10.5, 'ink3')
            y += 20
        if trend.definition:
            box_lines = c.wrap(trend.definition, 'regular', 11.5, w - 28, 4)
            c.rect(x, y, w, len(box_lines) * 18 + 20, 'card_grey')
            y = _paragraph(c, x + 14, y + 10, w - 28, trend.definition, lines=4, color='ink') + 18
        if trend.paragraphs:
            y = _paragraph(c, x, y, w, ' '.join(trend.paragraphs), lines=9) + 8
        if trend.colors:
            _label(c, x, y, 'COLOR')
            y = _swatches(c, x, y + 18, w, trend.colors)
        if trend.materials:
            _label(c, x, y, 'MATERIAL · TEXTURE')
            y = _textures(c, x, y + 18, w, trend.materials)
        if trend.features:
            _label(c, x, y, 'KEY ITEM · DETAIL')
            y = _pills(c, x, y + 18, w, trend.features)
    # article photos from the Trend result (fetched and stored by stage 06)
    if photos:
        count = len(photos[:3])
        gap = 8
        pw = (w - gap * (count - 1)) / count
        ph = round(pw * 4 / 3) if count > 1 else round(w * 0.62)
        for index, (data, url) in enumerate(photos[:3]):
            _photo(c, x + index * (pw + gap), y, pw, ph, data)
        y += ph + 6
        hosts = ', '.join(dict.fromkeys(host for host in (_host(url) for _, url in photos[:3]) if host))
        c.text(x, y, f'트렌드 기사 이미지 · 출처 {hosts}' if hosts else '트렌드 기사 이미지', 'regular', 9.5, 'ink3')
        y += 22
    elif trend is not None:
        _photo(c, x, y, w, 90, None, empty='트렌드 기사 이미지 없음')
        y += 104
    # the stock product matched to this trend
    y = _heading(c, x, y + 8, w, '재고 현황')
    stock = sheet.stock
    if stock is None:
        section = sections['inventory']
        y = _paragraph(c, x, y, w, section.notes[0] if section.notes else '매칭된 재고 상품 없음', lines=3)
        return _swot_box(c, x, y + 12, w, swot)
    image = _decode_image(stock.image)
    iw, ih = 168, 210
    top = y
    _photo(c, x, y, iw, ih, image, 'contain', '재고 이미지 없음')
    rx, rw = x + iw + 16, w - iw - 16
    c.text(rx, y, c.wrap(stock.style_id, 'display_bold', 17, rw, 1)[0], 'display_bold', 17, 'ink')
    y += 26
    if stock.category:
        c.text(rx, y, stock.category, 'regular', 11, 'ink3')
        y += 22
    rows = [('컬러', None), ('소재', None), ('핏', stock.fit or '미제공'), ('재고', stock.quantity)]
    for label, value in rows:
        _label(c, rx, y + 3, label)
        vx = rx + 44
        if label == '컬러':
            if stock.colors:
                for name in stock.colors[:3]:
                    colour = swatch_color(name)
                    if colour:
                        c.rect(vx, y + 1, 16, 16, colour, 8, 'rule_s')
                        vx += 22
                    c.text(vx, y + 1, name, 'regular', 11, 'ink')
                    vx += c.width(name, 'regular', 11) + 12
            else:
                c.text(vx, y + 1, '미제공', 'regular', 11, 'ink3')
            y += 26
        elif label == '소재':
            if stock.materials:
                for name in stock.materials[:3]:
                    c.rect(vx - 1, y - 1, 22, 22, 'rule_s', 3)
                    c.image(vx, y, 20, 20, _texture_png(texture_kind(name)), 'cover')
                    c.text(vx + 26, y + 2, name, 'regular', 11, 'ink')
                    vx += 26 + c.width(name, 'regular', 11) + 12
            else:
                c.text(vx, y + 1, '미제공', 'regular', 11, 'ink3')
            y += 28
        else:
            c.text(vx, y + 1, c.wrap(value, 'regular', 11, rw - 44, 1)[0], 'bold' if label == '재고' else 'regular', 11, 'ink')
            y += 24
    if stock.basis:
        y = _paragraph(c, rx, y + 2, rw, stock.basis, size=9.5, lines=2, color='ink3')
    y = max(y, top + ih) + 12
    if stock.reason:
        _label(c, x, y, 'MATCH · 매칭 이유')
        y = _paragraph(c, x, y + 18, w, stock.reason, size=11, lines=5)
    return _swot_box(c, x, y + 12, w, swot)


def _swot_box(c: _Canvas, x: float, y: float, w: float, swot: Mapping[str, Any] | None) -> float:
    """The LLM SWOT draft stage 06 kept for this run, as a 2x2 grid; absent drafts leave a one-line note."""
    y = _heading(c, x, y + 8, w, 'SWOT 분석')
    quadrants = _mapping(_mapping(swot).get('swot'))
    if not quadrants:
        return _paragraph(c, x, y, w, 'SWOT 초안 없음 (LLM 미설정 또는 생성 실패)', size=10.5, lines=1, color='ink3') + 6
    gap, pad = 8, 12
    cell_w = (w - gap) / 2
    text_w = cell_w - 2 * pad - 10
    cells = []
    for key, title, tone in SWOT_CELLS:
        entries = [_mapping(item) for item in _list(quadrants.get(key))]
        lines = [c.wrap(item.get('text') or '', 'regular', 10.5, text_w, 3) for item in entries]
        height = 34 + sum(len(item) * 15 + 4 for item in lines) + (15 if not entries else 0) + 6
        cells.append((title, tone, entries, lines, height))
    for row in range(2):
        row_cells = cells[row * 2:row * 2 + 2]
        row_h = max(cell[4] for cell in row_cells)
        for column, (title, tone, entries, lines, _) in enumerate(row_cells):
            cx = x + column * (cell_w + gap)
            c.rect(cx, y, cell_w, row_h, tone + '_lt', 4)
            c.text(cx + pad, y + 10, title, 'bold', 11, tone)
            cy = y + 32
            if not entries:
                c.text(cx + pad, cy, '근거 부족', 'regular', 10, 'ink3')
            for item, wrapped in zip(entries, lines):
                c.rect(cx + pad + 1, cy + 6, 4, 4, tone, 2)
                for index, line in enumerate(wrapped):
                    c.text(cx + pad + 10, cy + index * 15, line, 'regular', 10.5, 'ink2')
                cy += len(wrapped) * 15 + 4
        y += row_h + gap
    note = 'LLM 가설 · MD 검토 필요' + (f" · model {swot.get('model')}" if _mapping(swot).get('model') else '')
    return _paragraph(c, x, y, w, note, size=9.5, lines=1, color='ink3')


# --- Design ------------------------------------------------------------------------------------------

def _design(c: _Canvas, x: float, y: float, w: float, sheet: PlanningSheet, images: Mapping[str, bytes | None],
            unreadable: Sequence[str], final_image: bytes | None, final_unreadable: bool,
            swot: Mapping[str, Any] | None = None) -> float:
    sections = {section.section_id: section for section in sheet.board.sections}
    y = _heading(c, x, y, w, '신상품 컨셉')
    concept = sheet.concept_line.text if sheet.concept_line else None
    if concept:
        lines = c.wrap(concept, 'regular', 12, w - 32, 6)
        c.rect(x, y, w, len(lines) * 19 + 24, 'card_grey')
        y = _paragraph(c, x + 16, y + 12, w - 32, concept, size=12, lines=6, color='ink') + 22
    view = sheet.concept
    if view and view.opportunity:
        _label(c, x, y, 'OPPORTUNITY · 기획 기회')
        y = _paragraph(c, x, y + 18, w, view.opportunity, size=11, lines=4) + 4
        for finding in view.findings[:2]:
            y = _paragraph(c, x, y, w, '· ' + finding, size=10, lines=2, color='ink3')
        y += 12
    visual = sheet.visual
    if visual.draft_visuals:
        stacked = _any_landscape(images)
        count = len(visual.draft_visuals)
        gap = 12
        pw = w if stacked else (w - gap * (count - 1)) / count
        ph = round(w * 2 / 3) if stacked else round(pw * 3 / 2)
        for index, item in enumerate(visual.draft_visuals):
            px = x if stacked else x + index * (pw + gap)
            py = y if not stacked else y + index * (ph + 40)
            name = 'NEW PRODUCT' if item.key == 'product' else 'STYLING'
            c.text(px, py, f'LOOK {index + 1:02d}.', 'display_bold', 15, 'ink')
            c.text(px + c.width(f'LOOK {index + 1:02d}.', 'display_bold', 15) + 8, py + 4,
                   f"{name} · {item.label.split(' · ')[0]}", 'regular', 10.5, 'ink3')
            data = images.get(item.key)
            if data:
                _photo(c, px, py + 26, pw, ph, data, 'contain')
            else:
                headline = '이미지를 읽지 못했습니다' if item.key in unreadable else \
                    {'failed': '생성 실패', 'skipped': '생략'}.get(item.status or '', '이미지 없음')
                _photo(c, px, py + 26, pw, ph, None, empty=headline)
        y += (count * (ph + 40) - 14) if stacked else ph + 26
        y += 8
        models = list(dict.fromkeys(item.model for item in visual.draft_visuals if item.model))
        note = 'novelty 생성 시안' + (f' · {", ".join(models)}' if models else '') + \
            (' · 로컬 fixture 이미지 — 생성된 컨셉 아님' if visual.draft_fixture else '')
        c.text(x, y, note, 'regular', 9.5, 'red' if any(i.status == 'failed' for i in visual.draft_visuals) else 'ink3')
        y += 24
    else:  # a draft without visuals: the final image or a placeholder
        ph = round(w * 1.1)
        headline = '시안 이미지를 읽지 못했습니다' if final_unreadable else \
            {'failed': '시안 이미지 생성 실패', 'skipped': '시안 이미지 생략'}.get(visual.status or '', '시안 이미지 없음')
        _photo(c, x, y, w, ph, final_image, 'contain', headline)
        y += ph + 24
    rows = [row for row in sheet.spec_rows]
    y = _heading(c, x, y, w, '상품 프로필')
    if not rows:
        y = _paragraph(c, x, y, w, '상품 프로필 미제공 — MD 확인 필요', size=11, lines=2, color='ink3') + 6
    for row in rows[:10]:
        c.text(x, y + 1, c.wrap(row.label, 'bold', 11, 80, 1)[0], 'bold', 11, 'ink')
        vx, vw = x + 86, w - 86
        if row.label == '컬러' and swatch_color(row.value):
            c.rect(vx, y + 1, 16, 16, swatch_color(row.value), 8, 'rule_s')
            vx, vw = vx + 22, vw - 22
        elif row.label == '소재':
            c.rect(vx - 1, y - 1, 20, 20, 'rule_s', 3)
            c.image(vx, y, 18, 18, _texture_png(texture_kind(row.value)), 'cover')
            vx, vw = vx + 26, vw - 26
        lines = c.wrap(row.value, 'regular', 11, vw - 70, 3)
        for index, line in enumerate(lines):
            c.text(vx, y + 1 + index * 16, line, 'regular', 11, 'ink2')
        if row.kind not in ('user_requested', 'evidence_observation'):
            c.text(x + w - c.width('MD 확인', 'regular', 9.5), y + 2, 'MD 확인', 'regular', 9.5, 'amber')
        y += max(1, len(lines)) * 16 + 10
        c.rect(x, y - 5, w, 1, 'rule')
    if len(rows) > 10:
        c.text(x, y + 2, f'외 {len(rows) - 10:,}개 — 전체는 실행 기록(JSON)에 있습니다', 'regular', 9.5, 'ink3')
        y += 18
    y += 10
    # novelty's differentiation first; while it is empty, the stage-06 LLM stand-in (swot.py).
    stand_in = () if view and view.differentiation else tuple(
        text for text in (_mapping(item).get('text') for item in _list(_mapping(_mapping(swot).get('swot')).get('differentiation')))
        if isinstance(text, str) and text.strip())
    if view and (view.differentiation or view.own_products or stand_in):
        y = _heading(c, x, y, w, '차별화 포인트')
        if view.differentiation:
            y = _bullets(c, x, y, w, view.differentiation, limit=3)
        elif stand_in:
            y = _bullets(c, x, y, w, stand_in, limit=3)
            y = _paragraph(c, x, y, w, 'orchestrator LLM 가설 (novelty 차별화 미제공) · MD 검토 필요',
                           size=9.5, lines=1, color='ink3') + 4
        if view.own_products:
            _label(c, x, y + 2, 'OWN PRODUCTS · 자사 상품과의 관계')
            y = _bullets(c, x, y + 20, w, view.own_products, limit=2)
        y += 10
    if sheet.insight_points:
        y = _heading(c, x, y, w, '기획 포인트')
        y = _bullets(c, x, y, w, sheet.insight_points, limit=4)
    elif not sections['insight'].claims and sections['insight'].notes:
        y = _heading(c, x, y, w, '기획 포인트')
        y = _paragraph(c, x, y, w, sections['insight'].notes[0], size=11, lines=3, color='ink3')
    return y


# --- Marketing ---------------------------------------------------------------------------------------

def _promotions(c: _Canvas, x: float, y: float, w: float, swot: Mapping[str, Any] | None, *,
                scale: float = 1.0) -> float:
    """Promotion ideas from the stage-06 LLM draft (swot.py): numbered title and one line each."""
    y = _heading(c, x, y, w, '프로모션 아이디어', scale=scale)
    if not _mapping(swot).get('swot'):
        return _paragraph(c, x, y, w, '프로모션 초안 없음 (LLM 미설정 또는 생성 실패)', size=10.5 * scale, lines=1, color='ink3') + 4
    ideas = [_mapping(item) for item in _list(_mapping(swot.get('swot')).get('promotions'))]
    if not ideas:
        return _paragraph(c, x, y, w, '근거가 부족해 제안된 프로모션 없음', size=10.5 * scale, lines=1, color='ink3') + 4
    badge = 18 * scale
    indent = 28 * scale
    for index, idea in enumerate(ideas[:3]):
        c.rect(x, y + 1, badge, badge, 'accent', badge / 2)
        number = str(index + 1)
        ns = 9.5 * scale
        c.text(x + badge / 2 - c.width(number, 'display_bold', ns) / 2, y + 3, number, 'display_bold', ns, 'paper')
        ts = 12 * scale
        c.text(x + indent, y, c.wrap(idea.get('title') or '', 'bold', ts, w - indent, 1)[0], 'bold', ts, 'ink')
        y = _paragraph(c, x + indent, y + 21 * scale, w - indent, idea.get('text') or '', size=10.5 * scale, lines=3) + 8 * scale
    return _paragraph(c, x, y, w, 'LLM 가설 · MD 검토 필요', size=9.5 * scale, lines=1, color='ink3')


def _target(c: _Canvas, x: float, y: float, w: float, target: TargetView | None, *,
            scale: float = 1.0) -> float:
    y = _heading(c, x, y, w, '타깃', scale=scale)
    if target is None or not (target.age or target.primary or target.summary):
        message = target.missing if target and target.missing else '타깃 분석 결과 없음'
        return _paragraph(c, x, y, w, message, size=11 * scale, lines=2, color='ink3') + 14
    if target.age:
        _label(c, x, y, 'AGE · 연령대', scale=scale)
        age_s = 18 * scale
        c.text(x, y + 16 * scale, c.wrap(target.age, 'bold', age_s, w, 1)[0], 'bold', age_s, 'ink')
        y += 44 * scale
        note = ' · '.join(text for text in (target.age_note, 'H&M 과거 구매 고객 기준 (연구용)') if text)
        y = _paragraph(c, x, y, w, note, size=9.5 * scale, lines=2, color='ink3') + 8
    if target.primary or target.summary:
        sum_s = 11 * scale
        pri_s = 12.5 * scale
        sum_lead = 17 * scale
        pri_lead = 19 * scale
        pad = 14 * scale
        lines = c.wrap(target.summary or '', 'regular', sum_s, w - 2 * pad, 6) if target.summary else []
        primary = c.wrap(target.primary or '', 'bold', pri_s, w - 2 * pad, 2) if target.primary else []
        box_h = 34 * scale + len(primary) * pri_lead + len(lines) * sum_lead + (8 * scale if lines and primary else 0)
        c.rect(x, y, w, box_h, 'card_grey')
        _label(c, x + pad, y + 10 * scale, 'TARGET HYPOTHESIS · 가설', scale=scale)
        ty = y + 28 * scale
        for line in primary:
            c.text(x + pad, ty, line, 'bold', pri_s, 'ink')
            ty += pri_lead
        if lines and primary:
            ty += 8 * scale
        for line in lines:
            c.text(x + pad, ty, line, 'regular', sum_s, 'ink2')
            ty += sum_lead
        y += box_h + 10
        if target.secondary:
            _label(c, x, y, 'SECONDARY', scale=scale)
            y = _pills(c, x, y + 18 * scale, w, target.secondary, limit=2, scale=scale)
        if target.occasions:
            _label(c, x, y, 'OCCASION · 착용 상황', scale=scale)
            y = _pills(c, x, y + 18 * scale, w, target.occasions, limit=4, scale=scale)
        y = _paragraph(c, x, y, w, 'research LLM 가설 · 검증 필요', size=9.5 * scale, lines=1, color='ink3')
    return y + 14


def _marketing(c: _Canvas, x: float, y: float, w: float, sheet: PlanningSheet,
               swot: Mapping[str, Any] | None = None) -> float:
    sc = COL3_SCALE
    sections = {section.section_id: section for section in sheet.board.sections}
    sales = sections['sales_market']
    y = _target(c, x, y, w, sheet.target, scale=sc)
    y = _heading(c, x, y, w, '판매·시장 분석', scale=sc)
    findings = [claim.text for claim in sales.claims]
    if findings:
        y = _bullets(c, x, y, w, findings, limit=5, scale=sc)
        for note in sales.notes[:3]:
            y = _paragraph(c, x, y, w, '※ ' + note, size=9.5 * sc, lines=2, color='ink3') + 2
    else:
        y = _paragraph(c, x, y, w, sales.notes[0] if sales.notes else '판매·시장 분석 결과 없음', size=11 * sc, lines=3, color='ink3')
    y += 12
    y = _heading(c, x, y, w, '가격 전략', scale=sc)
    price = dict(sheet.price_rows).get('제안 판매가')
    box_h = 70 * sc
    c.rect(x, y, w, box_h, 'yellow_lt', 4, 'yellow')
    _label(c, x + 14, y + 10 * sc, 'PRICE', scale=sc)
    c.text(x + 14, y + 26 * sc, price or '미제공', 'bold', 20 * sc, 'ink' if price else 'ink3')
    sub = 'research 과거 거래가격을 참고한 연구용 제안 · MD 확인 필요' if price else '판매가 근거 없음 · MD 판단 필요'
    sub_s = 9.5 * sc
    c.text(x + w - 14 - c.width(sub, 'regular', sub_s), y + 34 * sc, sub, 'regular', sub_s, 'ink3')
    y += box_h + 12
    for label, value in [row for row in sheet.price_rows if row[0] != '제안 판매가']:
        ls, vs = 11 * sc, 12.5 * sc
        c.text(x, y + 2, label, 'regular', ls, 'ink2')
        c.text(x + w - c.width(value, 'display_bold', vs), y, value, 'display_bold', vs, 'ink')
        y += 24 * sc
        c.rect(x, y - 4, w, 1, 'rule')
    decisions = [claim.text.partition(':')[0] for claim in sections['finance'].claims]
    if decisions:
        _label(c, x, y + 4, 'MD DECISION', scale=sc)
        y = _pills(c, x, y + 22 * sc, w, decisions, scale=sc)
        c.text(x, y - 2, '위 항목은 MD가 결정합니다 (자동 산출하지 않음)', 'regular', 9.5 * sc, 'ink3')
        y += 20
    y = _promotions(c, x, y + 12, w, swot, scale=sc)
    y += 12
    y = _heading(c, x, y, w, 'MD 검토 포인트', scale=sc)
    if sheet.review_items:
        y = _bullets(c, x, y, w, sheet.review_items, limit=6, lines=3, mark='warn', scale=sc)
    else:
        y = _paragraph(c, x, y, w, '추가 확인 질문 없음', size=11 * sc, lines=1, color='ink3')
    return y


# --- the poster --------------------------------------------------------------------------------------

def _header(c: _Canvas, sheet: PlanningSheet) -> float:
    board = sheet.board
    k = HEADER_H / 130  # the banner layout below is drawn on a 130-unit grid and scaled to HEADER_H
    c.rect(0, 0, W, HEADER_H, 'header')
    c.text(MARGIN + 5, 40.5 * k, 'FASHION PLANNER', 'display_bold', 34 * k, 'ink')  # vertically centred in the banner
    slogan_s = 50 * k
    sub_s = 15.6 * k
    gap = 20 * k
    block_h = slogan_s + gap + sub_s
    top_y = (HEADER_H - block_h) / 2
    c.text((W - c.width(BANNER_SLOGAN, 'display_heavy', slogan_s)) / 2, top_y, BANNER_SLOGAN, 'display_heavy', slogan_s, 'ink')
    c.text((W - c.width(BANNER_SUBTITLE, 'display', sub_s)) / 2, top_y + slogan_s + gap, BANNER_SUBTITLE, 'display', sub_s, 'ink2')
    created = sheet.started_at[:10] if isinstance(sheet.started_at, str) and len(sheet.started_at) >= 10 else '—'
    right = W - MARGIN - 6
    for index, (text, role, size, colour) in enumerate(((f'{board.season} · {board.category}', 'bold', 20.4 * k, 'ink'),
                                                        (f'생성일 {created}', 'regular', 14 * k, 'ink2'))):
        c.text(right - c.width(text, role, size), (21 + index * 32) * k, text, role, size, colour)
    stamp = board.review_label
    stamp_w = c.width(stamp, 'bold', 12 * k) + 20 * k
    c.rect(right - stamp_w, 84 * k, stamp_w, 28 * k, 'accent', 4)
    c.text(right - stamp_w + 10 * k, 89 * k, stamp, 'bold', 12 * k, 'paper')
    if board.synthetic_context:
        badge = '합성·샘플 데이터 포함'
        badge_w = c.width(badge, 'bold', 12 * k) + 20 * k
        c.rect(right - stamp_w - 8 * k - badge_w, 84 * k, badge_w, 28 * k, 'amber_lt', 4)
        c.text(right - stamp_w - 8 * k - badge_w + 10 * k, 89 * k, badge, 'bold', 12 * k, 'amber')
    for (x, w), title_text, tone in zip(COLUMNS, COLUMN_TITLES, COLUMN_TONES):
        c.rect(x, HEADER_H + 20, w, 66, tone, 6)  # each column title sits in its own colour band
        c.text(x + (w - c.width(title_text, 'display_bold', 36)) / 2, HEADER_H + 30, title_text, 'display_bold', 36, 'paper')
    return HEADER_H + 116


def render_png(sheet: PlanningSheet, *, image_bytes: bytes | None = None, image_unreadable: bool = False,
               fonts: Fonts | None = None, draft_images: Mapping[str, bytes | None] | None = None,
               draft_unreadable: Sequence[str] = (), trend_photos: Sequence[tuple[bytes, str]] = (),
               swot: Mapping[str, Any] | None = None) -> bytes:
    """A one-product proposal poster in three columns: Analysis (the Trend style with colour, texture and article
    photos; the stock product matched to it), Design (novelty's concept, LOOK visuals, specification, planning points)
    and Marketing (target, sales & market, price, promotion ideas, MD review points). Producer values are shown as stored; nothing is
    invented, and absent values stay absent."""
    c = _Canvas(fonts or Fonts())
    top = _header(c, sheet)
    (x1, w1), (x2, w2), (x3, w3) = COLUMNS
    bottoms = [_analysis(c, x1, top, w1, sheet, trend_photos, swot),
               _design(c, x2, top, w2, sheet, draft_images or {}, draft_unreadable, image_bytes, image_unreadable, swot),
               _marketing(c, x3, top, w3, sheet, swot)]
    y = max(bottoms) + 30
    c.rect(MARGIN, y, W - 2 * MARGIN, 1, 'rule_s')
    c.text(MARGIN, y + 12, FOOTER_NOTE, 'regular', 9.5, 'ink3')
    right = f'Fashion Planner · {SHEET_FORMAT} · {sheet.request_id}'
    c.text(W - MARGIN - c.width(right, 'regular', 9.5), y + 12, right, 'regular', 9.5, 'ink3')
    height = y + 40
    board = sheet.board
    metadata = {'Title': f'{board.title} — 상품기획서 (검토용)', 'Software': 'fashion-planner-orchestrator planning_sheet',
                'fashion-planner:format': SHEET_FORMAT, 'fashion-planner:request_id': sheet.request_id,
                'fashion-planner:contract_version': sheet.contract_version or '', 'fashion-planner:schema_version': sheet.schema_version or ''}
    return c.render(height, metadata)


def planning_sheet_png(envelope: Mapping[str, Any], *, load_artifact: Callable[[Mapping[str, Any]], bytes] | None = None,
                       fonts: Fonts | None = None,
                       trend_photos: Sequence[tuple[bytes, str]] = (), swot: Mapping[str, Any] | None = None) -> bytes:
    """PNG bytes for a run. ``load_artifact`` returns verified bytes for an image reference: the novelty visuals,
    or the final image when the draft carries none. ``trend_photos`` are the Trend article photos
    stage 06 stored for this run, as (bytes, source URL); ``swot`` is the SWOT draft it kept (swot.py), or None."""
    sheet = build_sheet(envelope)
    image_bytes, unreadable = None, False
    if sheet.visual.draft_visuals:
        # 0.2.0: embed novelty's verified visuals; the final image of such a run is this sheet and is never embedded.
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
        return render_png(sheet, fonts=fonts, draft_images=images, draft_unreadable=tuple(missing),
                          trend_photos=trend_photos, swot=swot)
    artifact = sheet.visual.artifact
    if artifact and sheet.visual.status == 'generated' and artifact.get('media_type') in IMAGE_MEDIA:
        try:
            image_bytes = load_artifact(dict(artifact)) if load_artifact else None
        except Exception:  # an unreadable artifact is reported on the sheet; the sheet still renders
            image_bytes = None
        if image_bytes is not None and (not isinstance(image_bytes, bytes) or len(image_bytes) > MAX_EMBEDDED_IMAGE_BYTES):
            image_bytes = None
        unreadable = image_bytes is None
    return render_png(sheet, image_bytes=image_bytes, image_unreadable=unreadable, fonts=fonts,
                      trend_photos=trend_photos, swot=swot)


# --- command line (read-only) --------------------------------------------------------------------

SHA256 = re.compile(r'[0-9a-f]{64}')


def _read_only_store(store_dir: Path, request_id: str):
    database = store_dir / 'checkpoints.sqlite3'
    if not database.is_file():
        raise SystemExit(f'store not found: {database}')
    connection = sqlite3.connect(f'file:{database}?mode=ro&immutable=1', uri=True)
    try:
        rows = dict(connection.execute('SELECT attempt, value FROM runs WHERE rid=?', (request_id,)).fetchall())
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

    return envelope, load


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Render the planning sheet (기획서) PNG of a stored run.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', type=Path, help='integrated-result envelope JSON (GET /api/workflow/runs/{id} body)')
    source.add_argument('--request-id', help='read the run from an orchestrator store directory (opened read-only)')
    parser.add_argument('--store-dir', type=Path, default=Path(os.environ.get('ORCHESTRATOR_WORKFLOW_STORE_DIR', '.local/workflow')))
    parser.add_argument('--image', type=Path, help='final image bytes to embed when reading --input (optional)')
    parser.add_argument('--output', required=True, help="PNG path, or '-' for stdout")
    args = parser.parse_args(argv)
    if args.input:
        envelope = json.loads(args.input.read_text(encoding='utf-8'))
        envelope = envelope.get('envelope', envelope)
        image = args.image.read_bytes() if args.image else None
        load = (lambda artifact: image) if image else None  # noqa: E731
    else:
        envelope, load = _read_only_store(args.store_dir, args.request_id)
    png = planning_sheet_png(envelope, load_artifact=load)
    if args.output == '-':
        sys.stdout.buffer.write(png)
    else:
        Path(args.output).write_bytes(png)
        print(f'{args.output} ({len(png):,} bytes)', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
