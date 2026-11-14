"""Draft v2: what the LLM returns under prompts v2 and v3 (meta.json format "draft_v2"). Decision Q3 = gamma.

Since 2026-11-11 the StructuredQuery path runs draft v3 (draft_v3.py: the same entries, grouped per item,
decisions IT-1..IT-7). This module keeps the shared entry models (season, term, condition, unsupported)
and the label repair, and the v2 draft itself so the archived prompts v2/v3 still validate.

The model interprets the query; code decides the values. In one call the model receives the standard
vocabulary (NOT the alias dictionary) and reports, for every condition expression in the query:
the exact substring (`raw`), its `kind`, whether the user wants it or wants it out (`polarity`), and
a `suggestion` from the standard list of that kind (or null). The resolver (resolve.py) then looks
`raw` up in the alias dictionary first and uses the suggestion only when the dictionary does not know
the expression; it also sets `match`, which the model never writes. Default season, major derived
from minor, `reason` texts and contract validation are code as well. Rules and current behavior: docs/query-structuring.md
§2.1 (resolver) and §2.2 (this format).

Validation here is about SHAPE only:
  - Unknown keys are rejected (prompt drift is caught early), so a model that writes `match` or
    `value` fails validation.
  - `kind`, `polarity`, `unsupported.type` and the season code are closed sets.
  - `suggestion` is a plain string on purpose. A suggestion outside the standard list is NOT a shape
    error: the resolver discards it with a warning and records `match = none` (plan §2.1), so one bad
    suggestion does not throw away the rest of the draft.
  - Whether `raw` is a substring of the query is checked by code that has the query (assembly), as a
    warning, like v1.
  - Before validation, the v2 path (structured.py) runs `repair_draft_labels` (decision OQ-12): a wrong
    `kind` / `unsupported.type` label in one entry is moved to the right list with a warning instead of
    failing the whole draft. The models below stay strict; the repair is a separate, explicit step.
The initial flat extraction format is no longer supported; current inference uses StructuredQuery drafts.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import contract_vocabulary as cvocab
from . import vocabulary as vocab
from .config import CONFIG

DRAFT_FORMAT = "draft_v2"  # value of "format" in prompts/<name>/<version>/meta.json

# kind of a condition = the contract attribute keys + category + gender. "detail" covers the standard
# detail, neckline and collar values (the contract's `detail` holds all three). The two length kinds
# (body_length, sleeve_length) were added on 2026-11-10 (decision OQ-9′).
CONDITION_KINDS: tuple[str, ...] = ("category", *cvocab.ATTRIBUTE_TYPES, "gender")
POLARITIES: tuple[str, ...] = ("include", "exclude")
# Conditions the contract can not express as a standard value: the same closed list as the contract
# (contract_vocabulary.UNSUPPORTED_TYPES, decision OQ-3′). It is the set prompt v1 already used
# (rules 6, 9, 10, 11) plus "other" for anything that fits none of them.
UNSUPPORTED_TYPES: tuple[str, ...] = cvocab.UNSUPPORTED_TYPES

SeasonCode = Literal[vocab.SEASONS]  # type: ignore[valid-type]
ConditionKind = Literal[CONDITION_KINDS]  # type: ignore[valid-type]
Polarity = Literal[POLARITIES]  # type: ignore[valid-type]
UnsupportedType = Literal[UNSUPPORTED_TYPES]  # type: ignore[valid-type]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftSeason(_Strict):
    # What the user said. year is null when the query names no year (code infers it); a query without
    # any season expression has season = null on the draft and code fills the default next season.
    # Year bounds from configs/query_structuring.json (draft.year_min/max).
    year: Optional[int] = Field(default=None, ge=CONFIG.draft_year_min, le=CONFIG.draft_year_max)
    season: SeasonCode
    raw: str = Field(min_length=1, pattern=r"\S")  # not blank: the resolver rejects blank raw


class DraftTerm(_Strict):
    raw: str = Field(min_length=1, pattern=r"\S")  # not blank: the resolver rejects blank raw


class DraftCondition(_Strict):
    raw: str = Field(min_length=1, pattern=r"\S")  # not blank: the resolver rejects blank raw
    kind: ConditionKind
    polarity: Polarity
    # A standard value of `kind` proposed by the model, or null. Not checked against the vocabulary here
    # (see module docstring); the resolver accepts it only when it is in the standard list of `kind`
    # (and, for category, only a minor unless the raw text itself is a major name; decision G-2).
    suggestion: Optional[str] = None


class DraftUnsupported(_Strict):
    type: UnsupportedType
    raw: str = Field(min_length=1, pattern=r"\S")  # not blank: the resolver rejects blank raw
    polarity: Polarity


# ---------------------------------------------------------------------------- label repair (OQ-12)
# Warning codes of repair_draft_labels (codes only, no user text).
WARN_DRAFT_KIND_MOVED_TO_UNSUPPORTED = "draft_kind_moved_to_unsupported"
WARN_DRAFT_UNKNOWN_KIND = "draft_unknown_kind"
WARN_DRAFT_TYPE_MOVED_TO_CONDITIONS = "draft_unsupported_type_moved_to_conditions"
WARN_DRAFT_UNKNOWN_UNSUPPORTED_TYPE = "draft_unknown_unsupported_type"


def repair_draft_labels(payload: object) -> tuple[object, tuple[str, ...]]:
    """Decision OQ-12 (2026-11-10): a wrong LABEL in one entry must not throw away the whole draft.

    Only the closed-set labels `conditions[].kind` and `unsupported[].type` are repaired, and only in an
    entry that is otherwise well formed (a dict whose `raw` is a non-blank string and whose `polarity`
    is valid). Everything else - unknown keys, missing fields, wrong JSON types - is left alone and still
    fails validation (prompt drift must stay visible). The rules are about the two label sets, not about
    particular words:
      1. condition kind is an unsupported type ("abstract", "age", ...) -> moved to `unsupported` with that type
      2. condition kind is any other unknown label                       -> moved to `unsupported`, type "other"
      3. unsupported type is a condition kind that is not also an unsupported type ("gender", "color", ...)
                                                                         -> moved to `conditions` with
         suggestion null, so the dictionary / gender table decides the value like any other condition
      4. unsupported type is any other unknown label                    -> type "other"
    Returns the (possibly new) payload and one warning code per kind of repair made.
    """
    if not isinstance(payload, dict):
        return payload, ()
    repaired = repair_label_lists(payload.get("conditions", []), payload.get("unsupported", []))
    if repaired is None:
        return payload, ()
    new_conditions, new_unsupported, warnings = repaired
    return {**payload, "conditions": new_conditions, "unsupported": new_unsupported}, warnings


def _well_formed(entry: object) -> bool:
    return (isinstance(entry, dict) and isinstance(entry.get("raw"), str) and bool(entry["raw"].strip())
            and entry.get("polarity") in POLARITIES)


def _item_of(entry: dict) -> dict:
    """The draft v4 `item` field of an entry, carried over when the entry moves between the two lists."""
    return {"item": entry["item"]} if "item" in entry else {}


def repair_label_lists(conditions: object, unsupported: object
                       ) -> Optional[tuple[list[object], list[object], tuple[str, ...]]]:
    """The OQ-12 rules on one pair of `conditions` / `unsupported` lists (draft v2 has one pair, draft v3
    one pair per item plus the common pair). Returns None when nothing was repaired or the lists are not
    lists (then validation reports the problem). Draft v4 entries keep their `item` field."""
    if not isinstance(conditions, list) or not isinstance(unsupported, list):
        return None
    well_formed = _well_formed
    warnings: list[str] = []

    def warn(code: str) -> None:
        if code not in warnings:
            warnings.append(code)

    new_conditions: list[object] = []
    new_unsupported: list[object] = []
    for entry in conditions:
        kind = entry.get("kind") if isinstance(entry, dict) else None
        if not well_formed(entry) or not isinstance(kind, str) or kind in CONDITION_KINDS:
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
        if not well_formed(entry) or not isinstance(type_, str) or type_ in UNSUPPORTED_TYPES:
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


class QueryDraft(_Strict):
    season: Optional[DraftSeason] = None
    style_names: list[DraftTerm] = Field(default_factory=list)
    item_terms: list[DraftTerm] = Field(default_factory=list)
    conditions: list[DraftCondition] = Field(default_factory=list)
    unsupported: list[DraftUnsupported] = Field(default_factory=list)

    def raws(self) -> list[str]:
        """Every `raw` in the draft, in field order (for the substring-of-the-query check)."""
        values = [self.season.raw] if self.season else []
        values += [t.raw for t in (*self.style_names, *self.item_terms)]
        values += [c.raw for c in self.conditions] + [u.raw for u in self.unsupported]
        return values
