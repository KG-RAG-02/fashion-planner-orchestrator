"""Pydantic models of the StructuredQuery 1.0 contract (the output of query structuring).

Owner of the contract: THIS repository (decision Y1). Receiver-facing definition:
contracts/agents/vogue-orchestrator/output/structured-query.md. The JSON Schema handed to receivers,
contracts/agents/vogue-orchestrator/output/structured-query-1.0.schema.json, is generated from these models by
scripts/export_structured_query_schema.py (drift check: tests/test_query_structured_models.py).

Values come from the K-Fashion standard vocabulary owned by trend-analysis-agent (vocabulary.py,
plus the two length kinds in contract_vocabulary.py, decision OQ-9′). Each attribute kind has its own
reference type (ColorRef, ...) so the allowed values are part of the JSON Schema, not only of this
code (decision X2).

Rules enforced here (the producer side, so every model forbids unknown fields):
  - The conditions are grouped per item (decisions IT-1..IT-9, 2026-11-11): `items[]`, each with its own
    category, excluded categories, gender, attributes and unsupported conditions.
  - Empty is `[]`, never null. The seven attribute keys are always present on both sides, and so are
    the gender lists (decision G-3: gender is include/exclude like categories and attributes).
  - `unsupported.type` is one of the nine closed types (decision OQ-3′).
  - `match == "none"` if and only if `value is None` (AttributeRef, GenderRef).
  - A CategoryRef always names a standard category: an unresolved category is an out-of-scope item
    (`out_of_scope_items`, decision IT-7) when the user asks for it, and an `unsupported` entry (type
    "category") when the user excludes it, so `match` is never "none" here. A major-only reference
    is used only when the user named the major category itself, so its `match` is "exact".
  - When `minor` is given, `major` is derived from it; a contradicting `major` is rejected.

The previous contract shape (trend's TrendQuery) lives in trend_query.py.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import contract_vocabulary as cvocab
from . import vocabulary as vocab
from .season import next_fashion_season

SCHEMA_VERSION = "1.0"

# Closed value sets come from the generated vocabulary module (a tuple subscript means several values).
Season = Literal[vocab.SEASONS]  # type: ignore[valid-type]
SeasonOrigin = Literal[vocab.SEASON_ORIGINS]  # type: ignore[valid-type]
Gender = Literal[vocab.GENDERS]  # type: ignore[valid-type]
CategoryMajor = Literal[vocab.CATEGORY_MAJOR]  # type: ignore[valid-type]
CategoryMinor = Literal[vocab.CATEGORY_MINOR]  # type: ignore[valid-type]
Color = Literal[vocab.ATTRIBUTE_VALUES["color"]]  # type: ignore[valid-type]
Material = Literal[vocab.ATTRIBUTE_VALUES["material"]]  # type: ignore[valid-type]
Pattern = Literal[vocab.ATTRIBUTE_VALUES["pattern"]]  # type: ignore[valid-type]
Detail = Literal[vocab.ATTRIBUTE_VALUES["detail"]]  # type: ignore[valid-type]
Fit = Literal[vocab.ATTRIBUTE_VALUES["fit"]]  # type: ignore[valid-type]
BodyLength = Literal[cvocab.ATTRIBUTE_VALUES["body_length"]]  # type: ignore[valid-type]
SleeveLength = Literal[cvocab.ATTRIBUTE_VALUES["sleeve_length"]]  # type: ignore[valid-type]
UnsupportedType = Literal[cvocab.UNSUPPORTED_TYPES]  # type: ignore[valid-type]

# How a value was decided (contract section 4). Always set by code, never by the LLM.
Match = Literal["exact", "alias", "approx", "llm", "none"]
# A CategoryRef is never unresolved (unresolved categories are recorded in `unsupported`).
CategoryMatch = Literal["exact", "alias", "approx", "llm"]
Polarity = Literal["include", "exclude"]

NonEmptyStr = Annotated[str, Field(min_length=1)]


class _Contract(BaseModel):
    """Base of every StructuredQuery model: unknown fields are rejected, and the exported
    (serialization) JSON Schema marks defaulted fields as required because they are always emitted."""

    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


def _check_match_and_value(match: str, value: Any) -> None:
    if (match == "none") != (value is None):
        raise ValueError('match must be "none" exactly when value is null')


class SeasonRequest(_Contract):
    """Planning season. `default_next` = the user named none and code filled the next fashion season."""

    year: int
    season: Season
    raw: Optional[str] = None
    origin: SeasonOrigin

    @model_validator(mode="after")
    def _raw_matches_origin(self) -> "SeasonRequest":
        if self.origin == "default_next" and self.raw is not None:
            raise ValueError("a default_next season has no raw text")
        if self.origin == "user_requested" and not self.raw:
            raise ValueError("a user_requested season keeps the user's raw text")
        return self

    @classmethod
    def default_next(cls, today: date) -> "SeasonRequest":
        year, season = next_fashion_season(today)
        return cls(year=year, season=season, raw=None, origin="default_next")


class TermRef(_Contract):
    """Trend name or product model name. No normalised vocabulary exists yet, so `value` is always null."""

    raw: NonEmptyStr
    value: None = None


class TrendTerms(_Contract):
    """Query-wide terms (decision IT-5″).

    `seasons` (decision SQ-2, 2026-11-11): every season the user named, in query order, never empty - a query
    naming no season gets the one `default_next` season. Duplicates (same year and season) are removed.
    `product_model_names` (decision SQ-1, renamed from `item_terms`): specific product / model names that no
    standard category or attribute can express (e.g. a model name); items the user asks for are in `items`."""

    seasons: list[SeasonRequest] = Field(min_length=1)
    style_names: list[TermRef] = Field(default_factory=list)
    product_model_names: list[TermRef] = Field(default_factory=list)


class CategoryRef(_Contract):
    """A standard category. `minor` set = only that minor category; `minor` null = the whole major one."""

    raw: NonEmptyStr
    major: CategoryMajor
    minor: Optional[CategoryMinor] = None
    match: CategoryMatch

    @model_validator(mode="before")
    @classmethod
    def _derive_major(cls, data: Any) -> Any:
        # The major category of a minor one is fixed by the vocabulary, so it may be omitted on input.
        if isinstance(data, dict) and data.get("major") is None and data.get("minor") in vocab.CATEGORY_MINOR_TO_MAJOR:
            data = {**data, "major": vocab.CATEGORY_MINOR_TO_MAJOR[data["minor"]]}
        return data

    @model_validator(mode="after")
    def _check_category(self) -> "CategoryRef":
        if self.minor is not None and self.major != vocab.CATEGORY_MINOR_TO_MAJOR[self.minor]:
            raise ValueError(f"major must be {vocab.CATEGORY_MINOR_TO_MAJOR[self.minor]!r} for minor {self.minor!r}")
        if self.minor is None and self.match != "exact":
            raise ValueError('a major-only category is used only when the user named it, so match must be "exact"')
        return self


class GenderRef(_Contract):
    """Same shape as AttributeRef; `value` is one of the three standard genders or null."""

    raw: NonEmptyStr
    value: Optional[Gender]
    match: Match

    @model_validator(mode="after")
    def _check_match(self) -> "GenderRef":
        _check_match_and_value(self.match, self.value)
        return self


class AttributeRef(_Contract):
    """Common shape of an attribute reference: `{raw, value, match}`. `value` null = not decided
    (the raw text stays in `raw`). Concrete per-kind types below narrow `value` to that kind's vocabulary."""

    raw: NonEmptyStr
    value: Optional[str]
    match: Match

    @model_validator(mode="after")
    def _check_match(self) -> "AttributeRef":
        _check_match_and_value(self.match, self.value)
        return self


class ColorRef(AttributeRef):
    value: Optional[Color]


class MaterialRef(AttributeRef):
    value: Optional[Material]


class PatternRef(AttributeRef):
    value: Optional[Pattern]


class DetailRef(AttributeRef):
    """Detail, neckline and collar values share this kind (61 values)."""

    value: Optional[Detail]


class FitRef(AttributeRef):
    value: Optional[Fit]


class BodyLengthRef(AttributeRef):
    """Body length (기장), trend's BodyLength values (decision OQ-9′)."""

    value: Optional[BodyLength]


class SleeveLengthRef(AttributeRef):
    """Sleeve length (소매 기장), trend's SleeveLength values (decision OQ-9′)."""

    value: Optional[SleeveLength]


# attribute kind -> reference type (same keys and order as contract_vocabulary.ATTRIBUTE_TYPES)
ATTRIBUTE_REF_TYPES: dict[str, type[AttributeRef]] = {
    "color": ColorRef,
    "material": MaterialRef,
    "pattern": PatternRef,
    "detail": DetailRef,
    "fit": FitRef,
    "body_length": BodyLengthRef,
    "sleeve_length": SleeveLengthRef,
}


class AttributeValues(_Contract):
    """One side (include or exclude) of the requested attributes: every kind is always present."""

    color: list[ColorRef] = Field(default_factory=list)
    material: list[MaterialRef] = Field(default_factory=list)
    pattern: list[PatternRef] = Field(default_factory=list)
    detail: list[DetailRef] = Field(default_factory=list)
    fit: list[FitRef] = Field(default_factory=list)
    body_length: list[BodyLengthRef] = Field(default_factory=list)
    sleeve_length: list[SleeveLengthRef] = Field(default_factory=list)


class AttributeFilter(_Contract):
    include: AttributeValues = Field(default_factory=AttributeValues)
    exclude: AttributeValues = Field(default_factory=AttributeValues)


class GenderFilter(_Contract):
    """Decision G-3. Genders to select = the include values when there are any (minus excluded ones),
    otherwise all three standard genders minus the excluded ones; both empty = no gender filter.
    Entries with value null (match none) take no part in that. Code never rewrites an exclusion into the
    complement ("남성 말고" stays exclude [남]; it means 여·공용 by this definition)."""

    include: list[GenderRef] = Field(default_factory=list)
    exclude: list[GenderRef] = Field(default_factory=list)


class UnsupportedCondition(_Contract):
    """A requested condition the vocabulary or this contract cannot express; recorded, never dropped."""

    type: UnsupportedType
    raw: NonEmptyStr
    reason: NonEmptyStr
    polarity: Polarity


class QueryItem(_Contract):
    """One item to look for, with every condition that applies to it (decisions IT-1, IT-3).

    An item is complete on its own, so the same process can run once per item: conditions that the user
    gave for the whole query are copied into every item. `category` null = the user named no item to look
    for (IT-6): every category minus `excluded_categories` ("아우터는 빼고 블랙" = 상의·하의·원피스 중 블랙).
    `excluded_categories` keeps the exclusion form (like gender, G-3); code never rewrites it into the
    remaining categories.

    `item_id` (decision IT-8, 2026-11-11): "i1", "i2", ... in `items` order, so a result produced for one
    item (for example by a per-item Agent) can be joined back to it after the items are sent apart."""

    item_id: Annotated[str, Field(pattern=r"^i[1-9][0-9]*$")]
    category: Optional[CategoryRef] = None
    excluded_categories: list[CategoryRef] = Field(default_factory=list)
    gender: GenderFilter = Field(default_factory=GenderFilter)
    attributes: AttributeFilter = Field(default_factory=AttributeFilter)
    unsupported: list[UnsupportedCondition] = Field(default_factory=list)


class OutOfScopeItem(_Contract):
    """An item the user asked for that is not a standard category ("블랙 부츠" -> 부츠), decision IT-7.
    It is not turned into an item (that would widen the search to every category); its own conditions are
    kept as raw text in `conditions_raw` (the 블랙 of "블랙 부츠랑 셔츠" belongs to 부츠, not to 셔츠)."""

    raw: NonEmptyStr
    reason: NonEmptyStr
    conditions_raw: list[NonEmptyStr] = Field(default_factory=list)


class StructuredQuery(_Contract):
    """StructuredQuery 1.0: one natural-language query structured with the K-Fashion standard
    vocabulary. Definition: contracts/agents/vogue-orchestrator/output/structured-query.md (owner: vogue-orchestrator).

    `items` are in the order the user named them (decision IT-4: the pipeline takes one item today, so
    only `items[0]` is forwarded to trend). Season and trend names are query-wide (`trend_terms`,
    decision IT-5″). The assembler always writes at least one item: with no named item, or only
    out-of-scope ones, one item with `category: null` (IT-6, IT-7′). The model still accepts `[]`."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    request_id: NonEmptyStr
    original_query: NonEmptyStr
    trend_terms: TrendTerms
    items: list[QueryItem] = Field(default_factory=list)
    out_of_scope_items: list[OutOfScopeItem] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)  # reserved, always [] for now

    @model_validator(mode="after")
    def _item_ids_follow_the_order(self) -> "StructuredQuery":
        # IT-8: item_id = "i" + position (1-based), so ids are unique and items[0] is always "i1"
        expected = [f"i{n}" for n in range(1, len(self.items) + 1)]
        if [item.item_id for item in self.items] != expected:
            raise ValueError(f"item_id must be {expected} in items order")
        return self


def export_json_schema() -> dict[str, Any]:
    """JSON Schema of the emitted StructuredQuery (serialization mode: every field is always present)."""
    schema = StructuredQuery.model_json_schema(mode="serialization")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
