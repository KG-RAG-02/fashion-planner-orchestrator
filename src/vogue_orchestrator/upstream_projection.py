"""Registered projections of the Trend and Stock results P08 sends to P06 (novelty-handoff).

P08 keeps the full upstream results in its run (body['sources'] during the run, then the checkpoints and
blobs) and sends P06 a registered projection of them: the same values minus fields P06 does not read, so the
request stays under P06's 1,000,000-byte body limit (req-xxxxxxxx sent 1,041,802 B of raw results).

The binding check (WorkflowContract.integrated, and the same rule before the P06 call) accepts the stored raw
result or the output of any registered version for it. A stored run therefore stays verifiable after the
current version changes, so a registered version is never edited in place or removed: tests pin each
version's output hash on a fixture and every key ever registered. A new projection is a new key.

Pure functions only: inputs are deep-copied and never mutated, and array lengths, order and null slots are
kept. Standard library only, no imports from this package.
"""
from __future__ import annotations

import copy
from types import MappingProxyType

# VOGUE_WORKFLOW_P06_INPUT_PROJECTION=raw sends the stored results unchanged (stage 1 of the rollout: P08
# already accepts the projections, P06 still receives raw). It is a send mode, not a registered projection.
RAW = 'raw'


def _p06_input_1_trend(trend_result):
    """p06-input/1, Trend side: drop trends[].items[].similar_products (Stock's input, not P06's) and the
    evidence rows' url and title. quote stays: trend-result-1.1 requires it, so the 1.1 label stays true."""
    view = copy.deepcopy(trend_result)
    if not isinstance(view, dict):
        return view
    for trend in view.get('trends') or []:
        for item in (trend.get('items') or []) if isinstance(trend, dict) else []:
            if isinstance(item, dict):
                item.pop('similar_products', None)
    for row in view.get('evidence') or []:
        if isinstance(row, dict):
            for key in ('url', 'title'):
                row.pop(key, None)
    return view


def _p06_input_1_stock(stock_result):
    """p06-input/1, Stock side: a product image without a url becomes null. P06 reads only image.url; the
    base64 data is the bulk of the result, and stock-result-1.0 allows a null image (an image object must
    carry its data), so the 1.0 label stays true."""
    view = copy.deepcopy(stock_result)
    if not isinstance(view, dict):
        return view
    for product in view.get('products') or []:
        if isinstance(product, dict) and isinstance(product.get('image'), dict) and 'url' not in product['image']:
            product['image'] = None
    return view


def _p06_input_2_stock(stock_result):
    """p06-input/2, Stock side: each product's photo is sent once. P06's outfit image pairs the new product with
    one stock item and sends that item's photo to the image edit call (VOGUE decision 2026-11-17), so photos
    stay, but a later entry of a style_id already sent gets null: the same product fills several per-trend
    slots (14 entries / 6 products in req-xxxxxxxx). Images with a url, and products without a string style_id,
    are kept as they are. stock-result-1.0 allows a null image, so the 1.0 label stays true."""
    view = copy.deepcopy(stock_result)
    if not isinstance(view, dict):
        return view
    sent = set()
    for product in view.get('products') or []:
        if not isinstance(product, dict) or not isinstance(product.get('image'), dict) or 'url' in product['image']:
            continue
        style_id = product.get('style_id')
        if not isinstance(style_id, str):
            continue
        if style_id in sent:
            product['image'] = None
        else:
            sent.add(style_id)
    return view


# key -> (trend projection, stock projection). Append only: never edit or remove a registered version.
P06_INPUT_PROJECTIONS = MappingProxyType({
    'p06-input/1': (_p06_input_1_trend, _p06_input_1_stock),
    'p06-input/2': (_p06_input_1_trend, _p06_input_2_stock),
})
CURRENT_P06_INPUT_PROJECTION = 'p06-input/2'


def p06_input_projection_valid(version):
    """True for raw or a registered projection key."""
    return version == RAW or version in P06_INPUT_PROJECTIONS


def p06_inputs(trend_result, stock_result, version=CURRENT_P06_INPUT_PROJECTION):
    """(trend_view, stock_view) P08 sends to P06 under `version` (raw or a registered key); new objects."""
    if version == RAW:
        return copy.deepcopy(trend_result), copy.deepcopy(stock_result)
    if version not in P06_INPUT_PROJECTIONS:
        raise ValueError('P06_INPUT_PROJECTION_INVALID')
    trend, stock = P06_INPUT_PROJECTIONS[version]
    return trend(trend_result), stock(stock_result)


def p06_trend_views(raw):
    """Every Trend result P06 may have received for this stored raw result: [raw] + each registered version."""
    return [raw, *(trend(raw) for trend, _ in P06_INPUT_PROJECTIONS.values())]


def p06_stock_views(raw):
    """Every Stock result P06 may have received for this stored raw result: [raw] + each registered version."""
    return [raw, *(stock(raw) for _, stock in P06_INPUT_PROJECTIONS.values())]
