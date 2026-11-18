"""The LLM draft of a structured query, and how code groups its conditions per item.

The model interprets the query; code decides the values. In one call the model receives the standard
vocabulary (not the alias dictionary) and reports, for every expression in the query, the exact substring
(`raw`), its `kind`, whether the user wants it or wants it out (`polarity`) and a `suggestion` from the
standard list of that kind (or null). The resolver (resolve.py) looks `raw` up in the alias dictionary first
and uses the suggestion only when the dictionary does not know the expression; it also sets `match`, which
the model never writes. Default season, major derived from minor, reason texts and contract validation
are code as well (docs/query-structuring.md).

What the model writes (`QueryDraft`): one flat list of conditions, each naming the item it belongs to.

    {
      "seasons": [{"year", "season", "raw"}],                      every season named, query-wide
      "style_names": [{"raw"}], "product_model_names": [{"raw"}],  query-wide
      "items": [{"raw": "숏패딩", "suggestion": "패딩"}],            every item asked for, in query order
      "conditions":  [{"raw", "kind", "polarity", "suggestion", "item": "<raw of an item>" | null}],
      "unsupported": [{"type", "raw", "polarity", "item": "<raw of an item>" | null}]
    }

`item: null` means the whole query. An item the user names is always in `items`, also when it is not a
standard category (가방, 부츠): code looks it up and records an out-of-scope item. Excluded categories are
conditions with kind "category" and polarity "exclude". A modifier written before an item belongs to that
item only. (A grouped output written by the model lost more values in evaluation than the flat list with an
`item` field, so the model writes flat and code groups.)

What the assembly reads (`GroupedQueryDraft`): the same entries grouped per item, plus `common` for the
conditions of the whole query; code copies `common` into every item. `group_draft` builds it:
  - `item` is matched to `items[].raw`: same text, then same text ignoring spaces and case
    (lexicon.norm_key), then the one item whose raw contains it or is contained in it. Nothing matches:
    the condition becomes query-wide with the warning `condition_item_unknown` (kept, not dropped).
  - Several items with the same raw ("블랙 셔츠랑 화이트 셔츠"): the k-th such item stands for the k-th place
    the text appears in the query, and a condition goes to the first of them written at or after the
    condition (a modifier binds to the next item); none after it: the last one before it.

Validation is about shape only:
  - Unknown keys are rejected, so a model that writes `match` or `value` fails validation.
  - `kind`, `polarity`, `unsupported.type` and the season code are closed sets.
  - `suggestion` is a plain string. A suggestion outside the standard list is not a shape error: the
    resolver discards it with a warning, so one bad suggestion does not throw away the rest of the draft.
  - Whether `raw` is a substring of the query is checked by the assembly, as a warning.
  - Before validation `repair_draft_labels` moves an entry with a wrong `kind` / `unsupported.type` label to
    the right list with a warning instead of failing the whole draft. The models stay strict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import contract_vocabulary as cvocab
from . import vocabulary as vocab
from .config import CONFIG
from .lexicon import norm_key

# kind of a condition = the contract attribute keys + category + gender. "detail" covers the standard
# detail, neckline and collar values (the contract's `detail` holds all three).
CONDITION_KINDS: tuple[str, ...] = ("category", *cvocab.ATTRIBUTE_TYPES, "gender")
POLARITIES: tuple[str, ...] = ("include", "exclude")
# Conditions the contract can not express as a standard value: the same closed list as the contract,
# with "other" for anything that fits none of them.
UNSUPPORTED_TYPES: tuple[str, ...] = cvocab.UNSUPPORTED_TYPES

SeasonCode = Literal[vocab.SEASONS]  # type: ignore[valid-type]
ConditionKind = Literal[CONDITION_KINDS]  # type: ignore[valid-type]
Polarity = Literal[POLARITIES]  # type: ignore[valid-type]
UnsupportedType = Literal[UNSUPPORTED_TYPES]  # type: ignore[valid-type]

WARN_CONDITION_ITEM_UNKNOWN = "condition_item_unknown"
# Warning codes of repair_draft_labels (codes only, no user text).
WARN_DRAFT_KIND_MOVED_TO_UNSUPPORTED = "draft_kind_moved_to_unsupported"
WARN_DRAFT_UNKNOWN_KIND = "draft_unknown_kind"
WARN_DRAFT_TYPE_MOVED_TO_CONDITIONS = "draft_unsupported_type_moved_to_conditions"
WARN_DRAFT_UNKNOWN_UNSUPPORTED_TYPE = "draft_unknown_unsupported_type"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------------------------- shared entries
class DraftSeason(_Strict):
    # What the user said. year is null when the query names no year (code infers it); a query without any
    # season expression has no seasons and code fills the default next season.
    # Year bounds from configs/query_structuring.json (draft.year_min/max).
    year: Optional[int] = Field(default=None, ge=CONFIG.draft_year_min, le=CONFIG.draft_year_max)
    season: SeasonCode
    raw: str = Field(min_length=1, pattern=r"\S")  # not blank: the resolver rejects blank raw


class DraftTerm(_Strict):
    raw: str = Field(min_length=1, pattern=r"\S")


class DraftCondition(_Strict):
    raw: str = Field(min_length=1, pattern=r"\S")
    kind: ConditionKind
    polarity: Polarity
    # A standard value of `kind` proposed by the model, or null. Not checked against the vocabulary here;
    # the resolver accepts it only when it is in the standard list of `kind` (and, for category, only a
    # minor unless the raw text itself is a major name).
    suggestion: Optional[str] = None


class DraftUnsupported(_Strict):
    type: UnsupportedType
    raw: str = Field(min_length=1, pattern=r"\S")
    polarity: Polarity


# ------------------------------------------------------------------------------ what the model writes
class DraftItemRef(_Strict):
    raw: str = Field(min_length=1, pattern=r"\S")  # the item's words in the query
    suggestion: Optional[str] = None  # a standard category or null (dictionary first, resolve.py)


class ItemCondition(DraftCondition):
    item: Optional[str] = None  # raw of the item this condition belongs to; null = the whole query


class ItemUnsupported(DraftUnsupported):
    item: Optional[str] = None


class QueryDraft(_Strict):
    seasons: list[DraftSeason] = Field(default_factory=list)  # [] = the user named no season
    style_names: list[DraftTerm] = Field(default_factory=list)
    product_model_names: list[DraftTerm] = Field(default_factory=list)
    items: list[DraftItemRef] = Field(default_factory=list)
    conditions: list[ItemCondition] = Field(default_factory=list)
    unsupported: list[ItemUnsupported] = Field(default_factory=list)

    def raws(self) -> list[str]:
        """Every `raw` in the draft, in field order (for the substring-of-the-query check)."""
        values = [s.raw for s in self.seasons]
        values += [t.raw for t in (*self.style_names, *self.product_model_names)]
        values += [i.raw for i in self.items] + [c.raw for c in self.conditions] + [u.raw for u in self.unsupported]
        return values


# ------------------------------------------------------------------------------ what the assembly reads
class DraftItem(_Strict):
    raw: str = Field(min_length=1, pattern=r"\S")  # the item's words in the query ("숏패딩", "블레이저")
    suggestion: Optional[str] = None
    conditions: list[DraftCondition] = Field(default_factory=list)
    unsupported: list[DraftUnsupported] = Field(default_factory=list)


class DraftCommon(_Strict):
    conditions: list[DraftCondition] = Field(default_factory=list)
    unsupported: list[DraftUnsupported] = Field(default_factory=list)


class GroupedQueryDraft(_Strict):
    seasons: list[DraftSeason] = Field(default_factory=list)
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


# ------------------------------------------------------------------------------------------ label repair
def _well_formed(entry: object) -> bool:
    return (isinstance(entry, dict) and isinstance(entry.get("raw"), str) and bool(entry["raw"].strip())
            and entry.get("polarity") in POLARITIES)


def _item_of(entry: dict) -> dict:
    """The `item` field of an entry, carried over when the entry moves between the two lists."""
    return {"item": entry["item"]} if "item" in entry else {}


def repair_label_lists(conditions: object, unsupported: object
                       ) -> Optional[tuple[list[object], list[object], tuple[str, ...]]]:
    """A wrong label in one entry must not throw away the whole draft.

    Only the closed-set labels `conditions[].kind` and `unsupported[].type` are repaired, and only in an
    entry that is otherwise well formed (a dict whose `raw` is a non-blank string and whose `polarity` is
    valid). Unknown keys, missing fields and wrong JSON types are left alone and still fail validation, so
    prompt drift stays visible. The rules are about the two label sets, not about particular words:
      1. condition kind is an unsupported type ("abstract", "age", ...) -> moved to `unsupported` with that type
      2. condition kind is any other unknown label                       -> moved to `unsupported`, type "other"
      3. unsupported type is a condition kind that is not also an unsupported type ("gender", "color", ...)
                                                                         -> moved to `conditions` with suggestion
         null, so the dictionary or the gender table decides the value like any other condition
      4. unsupported type is any other unknown label                    -> type "other"
    Returns None when nothing was repaired or the lists are not lists (validation then reports it).
    """
    if not isinstance(conditions, list) or not isinstance(unsupported, list):
        return None
    warnings: list[str] = []

    def warn(code: str) -> None:
        if code not in warnings:
            warnings.append(code)

    new_conditions: list[object] = []
    new_unsupported: list[object] = []
    for entry in conditions:
        kind = entry.get("kind") if isinstance(entry, dict) else None
        if not _well_formed(entry) or not isinstance(kind, str) or kind in CONDITION_KINDS:
            new_conditions.append(entry)
            continue
        if kind in UNSUPPORTED_TYPES:
            warn(WARN_DRAFT_KIND_MOVED_TO_UNSUPPORTED)
            new_unsupported.append({"type": kind, "raw": entry["raw"], "polarity": entry["polarity"], **_item_of(entry)})
        else:
            warn(WARN_DRAFT_UNKNOWN_KIND)
            new_unsupported.append({"type": "other", "raw": entry["raw"], "polarity": entry["polarity"], **_item_of(entry)})
    for entry in unsupported:
        type_ = entry.get("type") if isinstance(entry, dict) else None
        if not _well_formed(entry) or not isinstance(type_, str) or type_ in UNSUPPORTED_TYPES:
            new_unsupported.append(entry)
            continue
        if type_ in CONDITION_KINDS:
            warn(WARN_DRAFT_TYPE_MOVED_TO_CONDITIONS)
            new_conditions.append({"raw": entry["raw"], "kind": type_, "polarity": entry["polarity"], "suggestion": None,
                                   **_item_of(entry)})
        else:
            warn(WARN_DRAFT_UNKNOWN_UNSUPPORTED_TYPE)
            new_unsupported.append({**entry, "type": "other"})
    if not warnings:
        return None
    return new_conditions, new_unsupported, tuple(warnings)


def repair_draft_labels(payload: object) -> tuple[object, tuple[str, ...]]:
    """`repair_label_lists` on the draft's conditions/unsupported pair; a moved entry keeps its `item`.
    Returns the (possibly new) payload and one warning code per kind of repair made."""
    if not isinstance(payload, dict):
        return payload, ()
    repaired = repair_label_lists(payload.get("conditions", []), payload.get("unsupported", []))
    if repaired is None:
        return payload, ()
    conditions, unsupported, codes = repaired
    return {**payload, "conditions": conditions, "unsupported": unsupported}, codes


# --------------------------------------------------------------------------------------------- grouping
@dataclass(frozen=True)
class GroupingResult:
    draft: GroupedQueryDraft
    warnings: tuple[str, ...]


def group_draft(draft: QueryDraft, query: str) -> GroupingResult:
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

    return GroupingResult(
        draft=GroupedQueryDraft(seasons=draft.seasons, style_names=draft.style_names,
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


def flatten_draft(payload: dict) -> dict:
    """A grouped draft written in the model's flat shape. The evaluation labels are grouped; the fixture
    provider and the tests feed them to the model's format through this. Conditions follow the order
    items then common."""
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
