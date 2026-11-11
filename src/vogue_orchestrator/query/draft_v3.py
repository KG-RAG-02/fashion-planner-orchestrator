"""Draft v3: what the LLM returns under prompt v4 (meta.json format "draft_v3"). Decisions IT-1..IT-7.

Same division of work as draft v2 (draft.py, decision Q3 = gamma): the model interprets, code decides
the values. What is new is that every condition says WHICH ITEM it belongs to:

    {
      "seasons": [{"year", "season", "raw"}],              every season named, query-wide (IT-5″, SQ-2)
      "style_names": [{"raw"}], "product_model_names": [{"raw"}],   query-wide (SQ-1: was item_terms)
      "items": [                                          every item the user asks for, in query order
        {"raw": "숏패딩", "suggestion": "패딩",              the item's own words and a standard minor (or null)
         "conditions": [DraftCondition], "unsupported": [DraftUnsupported]}   conditions of THIS item only
      ],
      "common": {"conditions": [...], "unsupported": [...]}   conditions for the whole query
    }

- An item the user names is always in `items`, also when it is not a standard category (가방, 부츠):
  code looks it up in the dictionary and records an out-of-scope item (IT-7). The model does not decide
  what is in scope.
- `common` holds conditions said for the whole query ("블랙으로 셔츠랑 팬츠", "남성 말고 ...", "둘 다"),
  and every condition of a query that names no item (IT-6). Code copies them into every item (IT-1);
  the model never repeats them.
- Excluded categories ("아우터는 빼고") are conditions with kind "category" and polarity "exclude".
- A modifier written before an item belongs to that item only (IT-2).

Validation is about SHAPE only, like draft v2. `repair_draft_v3_labels` applies the OQ-12′ label repair
to every conditions/unsupported pair (common and each item). Moving a mis-placed category (a category
INCLUDE written as a condition, or an included category sent to `unsupported`) into an item is done by
the assembly (structured.py), because it needs the dictionary.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from .draft import (
    DraftCondition, DraftSeason, DraftTerm, DraftUnsupported, _Strict, repair_label_lists,
)

DRAFT_FORMAT = "draft_v3"  # value of "format" in prompts/<name>/<version>/meta.json


class DraftItem(_Strict):
    raw: str = Field(min_length=1, pattern=r"\S")  # the item's words in the query ("숏패딩", "블레이저")
    # a standard category of the model's choice (minor, or the major the user named) or null; the
    # dictionary is looked up first, like any category condition (resolve.py)
    suggestion: Optional[str] = None
    conditions: list[DraftCondition] = Field(default_factory=list)
    unsupported: list[DraftUnsupported] = Field(default_factory=list)


class DraftCommon(_Strict):
    conditions: list[DraftCondition] = Field(default_factory=list)
    unsupported: list[DraftUnsupported] = Field(default_factory=list)


class QueryDraftV3(_Strict):
    seasons: list[DraftSeason] = Field(default_factory=list)  # [] = the user named no season
    style_names: list[DraftTerm] = Field(default_factory=list)
    product_model_names: list[DraftTerm] = Field(default_factory=list)
    items: list[DraftItem] = Field(default_factory=list)
    common: DraftCommon = Field(default_factory=DraftCommon)

    def raws(self) -> list[str]:
        """Every `raw` in the draft, in field order (for the substring-of-the-query check)."""
        values = [s.raw for s in self.seasons]
        values += [t.raw for t in (*self.style_names, *self.product_model_names)]
        for item in self.items:
            values += [item.raw] + [c.raw for c in item.conditions] + [u.raw for u in item.unsupported]
        values += [c.raw for c in self.common.conditions] + [u.raw for u in self.common.unsupported]
        return values


def repair_draft_v3_labels(payload: object) -> tuple[object, tuple[str, ...]]:
    """Decision OQ-12′ on draft v3: the same label rules as draft v2 (draft.repair_draft_labels), applied to
    the `common` pair and to the pair of every item. Shape errors are left for validation."""
    if not isinstance(payload, dict):
        return payload, ()
    warnings: list[str] = []

    def repair(block: object) -> object:
        if not isinstance(block, dict):
            return block
        repaired = repair_label_lists(block.get("conditions", []), block.get("unsupported", []))
        if repaired is None:
            return block
        conditions, unsupported, codes = repaired
        warnings.extend(code for code in codes if code not in warnings)
        return {**block, "conditions": conditions, "unsupported": unsupported}

    new = dict(payload)
    if "common" in new:
        new["common"] = repair(new["common"])
    if isinstance(new.get("items"), list):
        new["items"] = [repair(item) for item in new["items"]]
    if not warnings:
        return payload, ()
    return new, tuple(warnings)
