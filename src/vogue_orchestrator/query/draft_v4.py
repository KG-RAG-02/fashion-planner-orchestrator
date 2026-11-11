"""Draft v4: what the LLM returns under prompt v5 (meta.json format "draft_v4"). Decision FL-1 (2026-11-11).

Prompt v4 asked for conditions NESTED inside items (draft v3). Measured on dev it lost values (17/30
against 24/30 for the flat prompt v3) although it never put a condition on the wrong item. Draft v4 goes
back to ONE flat list of conditions, like draft v2, and adds one field per condition saying which item it
belongs to. Code groups them into draft v3 and the rest of the path is unchanged:

    {
      "seasons": [...], "style_names": [...], "product_model_names": [...],      as in draft v3
      "items": [{"raw": "숏패딩", "suggestion": "패딩"}],                       every item asked for
      "conditions":  [{"raw", "kind", "polarity", "suggestion", "item": "<raw of an item>" | null}],
      "unsupported": [{"type", "raw", "polarity", "item": "<raw of an item>" | null}]
    }

`item: null` = the whole query (draft v3 `common`; code copies it into every item, IT-1).

Grouping (`group_draft`, pure code, no word lists - W-10):
  - `item` is matched to `items[].raw`: same text, then same text ignoring spaces/case (lexicon.norm_key),
    then one item whose raw contains it or is contained in it. Nothing matches -> the condition becomes
    query-wide (`common`) with warning `condition_item_unknown` (kept, not dropped).
  - Several items with the same raw ("블랙 셔츠랑 화이트 셔츠"): the k-th such item stands for the k-th place
    the text appears in the query, and a condition goes to the first of them written at or after the
    condition (a modifier binds to the next item, IT-2); none after it -> the last one before it.
The draft v3 the grouping returns goes through the same assembly as before (structured.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pydantic import Field

from .draft import DraftCondition, DraftSeason, DraftTerm, DraftUnsupported, _Strict, repair_label_lists
from .draft_v3 import DraftCommon, DraftItem, QueryDraftV3
from .lexicon import norm_key

DRAFT_FORMAT = "draft_v4"  # value of "format" in prompts/<name>/<version>/meta.json
WARN_CONDITION_ITEM_UNKNOWN = "condition_item_unknown"


class DraftItemRef(_Strict):
    raw: str = Field(min_length=1, pattern=r"\S")  # the item's words in the query
    suggestion: Optional[str] = None  # a standard category or null (dictionary first, resolve.py)


class DraftConditionV4(DraftCondition):
    item: Optional[str] = None  # raw of the item this condition belongs to; null = the whole query


class DraftUnsupportedV4(DraftUnsupported):
    item: Optional[str] = None


class QueryDraftV4(_Strict):
    seasons: list[DraftSeason] = Field(default_factory=list)
    style_names: list[DraftTerm] = Field(default_factory=list)
    product_model_names: list[DraftTerm] = Field(default_factory=list)
    items: list[DraftItemRef] = Field(default_factory=list)
    conditions: list[DraftConditionV4] = Field(default_factory=list)
    unsupported: list[DraftUnsupportedV4] = Field(default_factory=list)

    def raws(self) -> list[str]:
        values = [s.raw for s in self.seasons]
        values += [t.raw for t in (*self.style_names, *self.product_model_names)]
        values += [i.raw for i in self.items] + [c.raw for c in self.conditions] + [u.raw for u in self.unsupported]
        return values


def repair_draft_v4_labels(payload: object) -> tuple[object, tuple[str, ...]]:
    """Decision OQ-12′ on draft v4: the same label rules as draft v2/v3 on the one conditions/unsupported
    pair. The `item` field of a moved entry is kept."""
    if not isinstance(payload, dict):
        return payload, ()
    repaired = repair_label_lists(payload.get("conditions", []), payload.get("unsupported", []))
    if repaired is None:
        return payload, ()
    conditions, unsupported, codes = repaired
    return {**payload, "conditions": conditions, "unsupported": unsupported}, codes


@dataclass(frozen=True)
class GroupedDraft:
    draft: QueryDraftV3
    warnings: tuple[str, ...]


def group_draft(draft: QueryDraftV4, query: str) -> GroupedDraft:
    """Put every condition under the item its `item` field names (module docstring)."""
    warnings: list[str] = []
    places = _item_places(draft.items, query)
    grouped = [DraftItem(raw=i.raw, suggestion=i.suggestion) for i in draft.items]
    common = DraftCommon()

    def target(reference: Optional[str], raw: str) -> Optional[int]:
        if reference is None:
            return None
        candidates = _matching_items(reference, draft.items)
        if not candidates:
            if WARN_CONDITION_ITEM_UNKNOWN not in warnings:
                warnings.append(WARN_CONDITION_ITEM_UNKNOWN)
            return None
        if len(candidates) == 1:
            return candidates[0]
        at = query.find(raw)
        after = [k for k in candidates if places[k] >= 0 and places[k] >= at >= 0]
        if after:
            return min(after, key=lambda k: places[k])
        return max(candidates, key=lambda k: places[k])

    for c in draft.conditions:
        condition = DraftCondition(raw=c.raw, kind=c.kind, polarity=c.polarity, suggestion=c.suggestion)
        k = target(c.item, c.raw)
        (common.conditions if k is None else grouped[k].conditions).append(condition)
    for u in draft.unsupported:
        entry = DraftUnsupported(type=u.type, raw=u.raw, polarity=u.polarity)
        k = target(u.item, u.raw)
        (common.unsupported if k is None else grouped[k].unsupported).append(entry)

    return GroupedDraft(
        draft=QueryDraftV3(seasons=draft.seasons, style_names=draft.style_names,
                           product_model_names=draft.product_model_names, items=grouped, common=common),
        warnings=tuple(warnings),
    )


def _matching_items(reference: str, items: list[DraftItemRef]) -> list[int]:
    exact = [k for k, i in enumerate(items) if i.raw == reference]
    if exact:
        return exact
    key = norm_key(reference)
    same = [k for k, i in enumerate(items) if norm_key(i.raw) == key]
    if same:
        return same
    if not key:
        return []
    overlapping = [k for k, i in enumerate(items) if key in norm_key(i.raw) or norm_key(i.raw) in key]
    return overlapping if len({norm_key(items[k].raw) for k in overlapping}) == 1 else []


def _item_places(items: list[DraftItemRef], query: str) -> list[int]:
    """Where each item is written: the k-th item with a given raw takes the k-th place that text appears
    (-1 when the text is not in the query)."""
    seen: dict[str, int] = {}
    places: list[int] = []
    for i in items:
        start = 0
        place = -1
        for _ in range(seen.get(i.raw, 0) + 1):
            place = query.find(i.raw, start)
            if place < 0:
                break
            start = place + len(i.raw)
        seen[i.raw] = seen.get(i.raw, 0) + 1
        places.append(place)
    return places


def from_draft_v3(payload: dict) -> dict:
    """A draft v3 label written as a draft v4 (the dataset labels stay grouped; the fixture provider and the
    tests feed them to a draft_v4 prompt through this). Conditions follow the order items then common."""
    conditions: list[dict] = []
    unsupported: list[dict] = []
    for item in payload.get("items", []):
        conditions += [{**c, "item": item["raw"]} for c in item.get("conditions", [])]
        unsupported += [{**u, "item": item["raw"]} for u in item.get("unsupported", [])]
    common = payload.get("common", {})
    conditions += [{**c, "item": None} for c in common.get("conditions", [])]
    unsupported += [{**u, "item": None} for u in common.get("unsupported", [])]
    return {
        "seasons": payload.get("seasons", []), "style_names": payload.get("style_names", []),
        "product_model_names": payload.get("product_model_names", []),
        "items": [{"raw": i["raw"], "suggestion": i.get("suggestion")} for i in payload.get("items", [])],
        "conditions": conditions, "unsupported": unsupported,
    }
