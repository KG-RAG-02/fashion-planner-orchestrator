"""SWOT and promotion drafts for the planning sheet (stage 06), written by the configured LLM.

Stage 06 builds a compact JSON summary of the stored run — the poster's Trend style, the stock product matched to
it with stock-agent's indicators (sell-through, stock value, recent trend, score), new-product-agent's concept and
sales-market-agent's price, cost, margin and target — and asks the model configured for query structuring
(``LLM_MODEL`` / ``LLM_API_KEY``, see llm_provider.py) for a SWOT of 0-4 short items per quadrant and 0-3 promotion
ideas, each naming the inputs it rests on. While new-product-agent leaves ``draft.differentiation`` empty, the same call
also returns 0-3 differentiation points (new product vs the matched stock product and the trend); the poster uses
them only when new-product-agent's own list is empty. sales-market-agent's channel/content hypotheses and merchandising actions
are part of the summary. The answer is validated and kept in the store under ``swot:<request_id>``; the planning-sheet
route renders that stored draft and never calls the model. A failed or invalid answer leaves the SWOT slot empty
and never fails the run; a later stage-06 retry calls the model again only when no valid draft was kept.

The integrated envelope does not change. The summary sent to the provider holds the run's product texts and
indicators, not the user's raw request or any image.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from .planning_sheet import (_concept_view, _draft_parts, _list, _mapping, _raw, _scalar_text, _strings, _texts,
                             focus_stock, focus_trend)

ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT / 'prompts' / 'swot'
QUADRANTS = ('strengths', 'weaknesses', 'opportunities', 'threats')
BASES = ('trend', 'stock', 'product', 'market')
MAX_ITEMS = 4
MAX_PROMOTIONS = 3
MAX_DIFFERENTIATION = 3
MAX_TITLE = 40
MAX_TEXT = 160
MAX_INPUT_TEXT = 400


class SwotRejected(Exception):
    """The model answer is not a usable SWOT; ``code`` says why (never the answer itself)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return str((os.environ if env is None else env).get('VOGUE_SWOT_ENABLED') or '1').strip() != '0'


def checkpoint_key(request_id: str) -> str:
    return f'swot:{request_id}'


def _clip(value: Any, limit: int = MAX_INPUT_TEXT) -> str | None:
    text = _scalar_text(value)
    return text[:limit] if text else None


def _number(value: Any) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _compact(value: Any) -> Any:
    """Drop empty values so the summary sent to the model stays small."""
    if isinstance(value, Mapping):
        out = {key: _compact(item) for key, item in value.items()}
        return {key: item for key, item in out.items() if item not in (None, '', [], {})}
    if isinstance(value, (list, tuple)):
        return [item for item in (_compact(entry) for entry in value) if item not in (None, '', [], {})]
    return value


def swot_input(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """The compact run summary the SWOT prompt reads (values as the producers reported them)."""
    result = _mapping(_mapping(envelope).get('result'))
    trend = focus_trend(result)
    style = _mapping(trend.get('style'))
    items = [_mapping(item) for item in _list(trend.get('items'))]
    summary: dict[str, Any] = {'trend': {
        'name': _clip(style.get('name')),
        'seasons': [_scalar_text(s) for s in _list(style.get('seasons'))],
        'definition': _clip(style.get('definition')),
        'observations': [_clip(_mapping(entry).get('text')) for entry in _list(style.get('description'))[:4]],
        'colors': list(dict.fromkeys(t for item in items for t in _texts(item.get('colors')))),
        'materials': list(dict.fromkeys(t for item in items for t in _texts(item.get('materials')))),
        'items': [_clip(item.get('name'), 80) for item in items[:6]],
    }}
    focus = focus_stock(result)
    if focus:
        slot, product = focus
        reason = _mapping(slot.get('reason'))
        inventory = _mapping(product.get('inventory'))
        attributes = _mapping(product.get('attributes'))
        recent = _mapping(reason.get('recent_trend'))
        summary['stock'] = {
            'style_id': _clip(product.get('style_id'), 80), 'category': _clip(product.get('category'), 80),
            'color': _texts(attributes.get('color')), 'material': _texts(attributes.get('material')),
            'fit': _clip(attributes.get('fit'), 80),
            'quantity': _number(inventory.get('quantity')), 'as_of': _clip(inventory.get('as_of'), 40),
            'data_nature': _clip(inventory.get('data_nature'), 40),
            'match_summary': _clip(slot.get('summary')), 'rank': _number(slot.get('rank')),
            'indicators': {
                'sell_through': _number(reason.get('sell_through')),
                'stock_value_krw': _number(reason.get('stock_value_krw')),
                'recent_trend': {'weeks': _number(recent.get('weeks')), 'recent_sold': _number(recent.get('recent_sold')),
                                 'prev_sold': _number(recent.get('prev_sold')), 'basis': _clip(recent.get('basis'), 60)},
                'season_share': _number(reason.get('season_share')), 'score': _number(reason.get('score')),
                'data_nature': {key: _clip(value, 40) for key, value in _mapping(reason.get('data_nature')).items()},
            },
        }
    _, body, _ = _draft_parts(result)
    draft = _mapping(body.get('draft'))
    concept = _concept_view(result)
    # Runs recorded before planning_analysis repeat the user's request among the Draft claims; it is not sent.
    request_texts = {text.strip() for text in (_mapping(result.get('user_input')).get('text'),
                                               _mapping(result.get('planning_request')).get('query'))
                     if isinstance(text, str) and text.strip()}
    own = lambda texts: [item for item in texts if item.strip() not in request_texts]  # noqa: E731
    summary['product'] = {
        'title': _clip(draft.get('title'), 120),
        'definition': _clip(concept.line.text if concept.line else None),
        'opportunity': _clip(concept.opportunity),
        'profile': [{'name': row.label, 'value': _clip(row.value, 80), 'kind': row.kind} for row in concept.profile][:10],
        'differentiation': [_clip(item) for item in own(concept.differentiation)][:4],
        'relation_to_own_products': [_clip(item) for item in own(concept.own_products)][:3],
        'planning_points': [_clip(item) for item in own(concept.points)][:4],
        'open_questions': [_clip(item) for item in own(concept.questions)][:4],
    }
    raw = _raw(result, 'sales_market_review')
    market = _mapping(raw.get('result')) if raw else {}
    if market:
        target = _mapping(market.get('target_audience')) or _mapping(market.get('target_audience_analysis'))
        hypothesis = _mapping(target.get('proposal'))
        cohort = _mapping(target.get('age_cohort'))
        price = _mapping(market.get('price_analysis'))
        fabric = _mapping(market.get('fabric_cost_analysis'))
        summary['market'] = {
            'proposed_price': _number(price.get('proposed_price')), 'price_currency': _clip(price.get('currency'), 10),
            'price_basis': 'H&M historical transaction prices (non-commercial research)' if price.get('proposed_price') is not None else None,
            'fabric_unit_cost': _number(fabric.get('unit_cost')), 'fabric_unit': _clip(fabric.get('unit'), 20),
            'margin_rate_percent': _number(_mapping(market.get('margin_analysis')).get('margin_rate_percent')),
            'margin_basis': _clip(_mapping(market.get('margin_analysis')).get('basis'), 120),
            'target_age_cohort': _clip(cohort.get('definition'), 80),
            'target_age_share': _number(cohort.get('share_of_customers_with_known_age')),
            'target_hypothesis': _clip(hypothesis.get('proposal')),
            'primary_segment': _clip(hypothesis.get('primary_segment')),
            'price_positioning': _mapping(hypothesis.get('price_positioning')) or None,
            'channel_content_hypotheses': [_clip(item, 200) for item in _list(hypothesis.get('channel_content_hypotheses'))][:4],
            'merchandising_actions': [_clip(item, 200) for item in _list(hypothesis.get('merchandising_actions'))][:4],
            'missing_data': [_clip(_mapping(item).get('field'), 80) for item in _list(market.get('missing_data'))],
        }
    summary['limitations'] = [_clip(item, 200) for item in _strings(result.get('limitations'))][:8]
    return _compact(summary)


def parse_swot(text: str) -> dict[str, list[dict[str, Any]]]:
    """Validate the model answer; raise SwotRejected when it is not the expected shape."""
    try:
        value = json.loads(text.strip().removeprefix('```json').removeprefix('```').removesuffix('```'))
    except (TypeError, ValueError):
        raise SwotRejected('SWOT_NOT_JSON') from None
    if not isinstance(value, dict) or not set(QUADRANTS) <= set(value):
        raise SwotRejected('SWOT_SHAPE_INVALID')
    out: dict[str, list[dict[str, Any]]] = {}
    for quadrant in QUADRANTS:
        entries = value[quadrant]
        if not isinstance(entries, list) or len(entries) > MAX_ITEMS:
            raise SwotRejected('SWOT_SHAPE_INVALID')
        items = []
        for entry in entries:
            text_value = entry.get('text') if isinstance(entry, dict) else None
            basis = entry.get('basis') if isinstance(entry, dict) else None
            if not isinstance(text_value, str) or not text_value.strip() or len(text_value.strip()) > MAX_TEXT:
                raise SwotRejected('SWOT_ITEM_INVALID')
            if not isinstance(basis, list) or not basis or any(item not in BASES for item in basis):
                raise SwotRejected('SWOT_BASIS_INVALID')
            items.append({'text': text_value.strip(), 'basis': list(dict.fromkeys(basis))})
        out[quadrant] = items
    if not any(out.values()):
        raise SwotRejected('SWOT_EMPTY')
    # Promotion ideas (prompt v2). A draft kept by prompt v1 has none.
    entries = value.get('promotions', [])
    if not isinstance(entries, list) or len(entries) > MAX_PROMOTIONS:
        raise SwotRejected('PROMOTION_SHAPE_INVALID')
    promotions = []
    for entry in entries:
        title = entry.get('title') if isinstance(entry, dict) else None
        text_value = entry.get('text') if isinstance(entry, dict) else None
        basis = entry.get('basis') if isinstance(entry, dict) else None
        if (not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE
                or not isinstance(text_value, str) or not text_value.strip() or len(text_value.strip()) > MAX_TEXT):
            raise SwotRejected('PROMOTION_ITEM_INVALID')
        if not isinstance(basis, list) or not basis or any(item not in BASES for item in basis):
            raise SwotRejected('PROMOTION_BASIS_INVALID')
        promotions.append({'title': title.strip(), 'text': text_value.strip(), 'basis': list(dict.fromkeys(basis))})
    out['promotions'] = promotions
    # Differentiation stand-in (prompt v3) while new-product-agent leaves draft.differentiation empty.
    entries = value.get('differentiation', [])
    if not isinstance(entries, list) or len(entries) > MAX_DIFFERENTIATION:
        raise SwotRejected('DIFFERENTIATION_SHAPE_INVALID')
    differentiation = []
    for entry in entries:
        text_value = entry.get('text') if isinstance(entry, dict) else None
        basis = entry.get('basis') if isinstance(entry, dict) else None
        if not isinstance(text_value, str) or not text_value.strip() or len(text_value.strip()) > MAX_TEXT:
            raise SwotRejected('DIFFERENTIATION_ITEM_INVALID')
        if not isinstance(basis, list) or not basis or any(item not in BASES for item in basis):
            raise SwotRejected('DIFFERENTIATION_BASIS_INVALID')
        differentiation.append({'text': text_value.strip(), 'basis': list(dict.fromkeys(basis))})
    out['differentiation'] = differentiation
    return out


def system_prompt() -> str:
    return (PROMPT_DIR / 'system.md').read_text(encoding='utf-8')


def prompt_sha256() -> str:
    """Identifies the SWOT prompt in a stored record (sha256 of prompts/swot/system.md)."""
    return hashlib.sha256((PROMPT_DIR / 'system.md').read_bytes().replace(b'\r\n', b'\n')).hexdigest()


class SwotWriter:
    """Stage 06 hook: ask the model once per run and keep the validated SWOT and promotion draft."""

    def __init__(self, store, call: Callable[[str, str], str], *, model: str | None = None):
        self.store, self.call = store, call
        self.model = model or getattr(call, 'model', None)

    def __call__(self, request_id: str, envelope: Mapping[str, Any]) -> dict[str, Any] | None:
        key = checkpoint_key(request_id)
        stored = self.store.checkpoint(key)
        if stored and stored[0] == 'done':
            return load_swot(self.store, request_id)
        try:
            answer = self.call(system_prompt(), json.dumps(swot_input(envelope), ensure_ascii=False, separators=(',', ':')))
            swot = parse_swot(answer)
        except SwotRejected as exc:
            self.store.checkpoint_write(key, 'failed', dict(reason=exc.code))
            return None
        except Exception as exc:  # provider, network or configuration failure: the slot stays empty
            self.store.checkpoint_write(key, 'failed', dict(reason=type(exc).__name__[:80]))
            return None
        record = dict(swot=swot, model=self.model, prompt_sha256=prompt_sha256())
        self.store.checkpoint_write(key, 'done', record)
        return record


def load_swot(store, request_id: str) -> dict[str, Any] | None:
    """The validated SWOT stage 06 kept for a run, or None."""
    stored = store.checkpoint(checkpoint_key(request_id))
    if not stored or stored[0] != 'done' or not isinstance(stored[1], Mapping):
        return None
    record = dict(stored[1])
    try:
        record['swot'] = parse_swot(json.dumps(record.get('swot')))
    except SwotRejected:
        return None
    return record
