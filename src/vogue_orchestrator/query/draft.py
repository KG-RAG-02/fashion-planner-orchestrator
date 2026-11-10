"""Draft v2: what the LLM returns under prompt v2 (meta.json format "draft_v2"). Decision Q3 = gamma.

The model interprets the query; code decides the values. In one call the model receives the standard
vocabulary (NOT the alias dictionary) and reports, for every condition expression in the query:
the exact substring (`raw`), its `kind`, whether the user wants it or wants it out (`polarity`), and
a `suggestion` from the standard list of that kind (or null). The resolver (resolve.py) then looks
`raw` up in the alias dictionary first and uses the suggestion only when the dictionary does not know
the expression; it also sets `match`, which the model never writes. Default season, major derived
from minor, `reason` texts and contract validation are code as well. Plan: docs/structured-query-plan.md
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
The archived v1 format (the model picked the final values) lives in legacy_v1.py.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import vocabulary as vocab
from .config import CONFIG

DRAFT_FORMAT = "draft_v2"  # value of "format" in prompts/<name>/<version>/meta.json

# kind of a condition = the contract attribute keys + category + gender. "detail" covers the standard
# detail, neckline and collar values (the contract's `detail` holds all three).
CONDITION_KINDS: tuple[str, ...] = ("category", *vocab.ATTRIBUTE_TYPES, "gender")
POLARITIES: tuple[str, ...] = ("include", "exclude")
# Conditions the contract can not express as a standard value. The first four have fixed reason texts in
# configs/query_structuring.json (unsupported.reasons); the rest get unsupported.generic_reason. The set
# is the one prompt v1 already used (rules 6, 9, 10, 11) plus "other" for anything that fits none of them.
UNSUPPORTED_TYPES: tuple[str, ...] = (
    "brand", "category", "attribute", "abstract", "price", "market", "channel", "age", "other",
)

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
