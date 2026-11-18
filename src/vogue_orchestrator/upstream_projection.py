"""What the orchestrator sends novelty of the trend and inventory results (novelty-input-projection).

The orchestrator keeps the full upstream results in its run (body['sources'] during the run, then the
checkpoints and blobs) and sends novelty a projection of them: the same values minus what novelty does not
read, so the request stays under novelty's 1,000,000-byte body limit (an unprojected request once reached
1,041,802 bytes; docs/troubleshooting.md, T-HTTP-01).

The binding check (WorkflowContract.integrated, and the same rule before the novelty call) recomputes the
projection from the stored raw results and compares it with what was sent. Tests pin the projection's
output hash on a fixture, so a change to it is visible.

Pure functions only: inputs are deep-copied and never mutated, and array lengths, order and null slots are
kept. Standard library only, no imports from this package.
"""
from __future__ import annotations

import copy

PROJECTION_ID = 'novelty-input-projection'


def trend_view(trend_result):
    """Trend side: drop trends[].items[].similar_products (the inventory service's input, not novelty's) and
    the evidence rows' url and title. quote stays: the trend result schema requires it."""
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


def inventory_view(inventory_result):
    """Inventory side: each product's photo is sent once. novelty's outfit image pairs the new product with one
    inventory item and sends that item's photo to the image edit call, so photos stay, but a later entry of a
    style_id already sent gets null: the same product fills several per-trend slots. Images with a url, and
    products without a string style_id, are kept as they are. The inventory result schema allows a null image."""
    view = copy.deepcopy(inventory_result)
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


def novelty_inputs(trend_result, inventory_result):
    """(trend_view, inventory_view) the orchestrator sends novelty; new objects."""
    return trend_view(trend_result), inventory_view(inventory_result)
