"""Pydantic models of the StructuredQuery 1.0 contract (the output of query structuring).

Owner of the contract: THIS repository (decision Y1). Receiver-facing definition:
contracts/structured-query.md. The JSON Schema handed to receivers,
contracts/structured-query.schema.json, is generated from these models by
scripts/export_structured_query_schema.py (drift check: tests/test_query_structured_models.py).

Values come from the K-Fashion standard vocabulary owned by trend-analysis-agent (vocabulary.py).
Each attribute kind has its own reference type (ColorRef, ...) so the allowed values are part of
the JSON Schema, not only of this code (decision X2).

Rules enforced here (the producer side, so every model forbids unknown fields):
  - Empty is `[]`, never null. The five attribute keys are always present on both sides.
  - `match == "none"` if and only if `value is None` (AttributeRef, GenderRef).
  - A CategoryRef always names a standard category: an unresolved category goes to
    `unsupported` (type "category"), so `match` is never "none" here. A major-only reference
    is used only when the user named the major category itself, so its `match` is "exact".
  - When `minor` is given, `major` is derived from it; a contradicting `major` is rejected.

The previous contract shape (trend's TrendQuery) lives in trend_query.py.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    """Trend name or product term. No normalised vocabulary exists yet, so `value` is always null."""

    raw: NonEmptyStr
    value: None = None


class TrendTerms(_Contract):
    season: SeasonRequest
    style_names: list[TermRef] = Field(default_factory=list)
    item_terms: list[TermRef] = Field(default_factory=list)


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


class CategoryFilter(_Contract):
    include: list[CategoryRef] = Field(default_factory=list)  # empty = all four major categories
    exclude: list[CategoryRef] = Field(default_factory=list)


class GenderRef(_Contract):
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


# attribute kind -> reference type (same keys and order as vocab.ATTRIBUTE_TYPES)
ATTRIBUTE_REF_TYPES: dict[str, type[AttributeRef]] = {
    "color": ColorRef,
    "material": MaterialRef,
    "pattern": PatternRef,
    "detail": DetailRef,
    "fit": FitRef,
}


class AttributeValues(_Contract):
    """One side (include or exclude) of the requested attributes: every kind is always present."""

    color: list[ColorRef] = Field(default_factory=list)
    material: list[MaterialRef] = Field(default_factory=list)
    pattern: list[PatternRef] = Field(default_factory=list)
    detail: list[DetailRef] = Field(default_factory=list)
    fit: list[FitRef] = Field(default_factory=list)


class AttributeFilter(_Contract):
    include: AttributeValues = Field(default_factory=AttributeValues)
    exclude: AttributeValues = Field(default_factory=AttributeValues)


class Requested(_Contract):
    categories: CategoryFilter = Field(default_factory=CategoryFilter)
    gender: Optional[GenderRef] = None
    attributes: AttributeFilter = Field(default_factory=AttributeFilter)


class UnsupportedCondition(_Contract):
    """A requested condition the vocabulary or this contract cannot express; recorded, never dropped."""

    type: NonEmptyStr
    raw: NonEmptyStr
    reason: NonEmptyStr
    polarity: Polarity


class StructuredQuery(_Contract):
    """StructuredQuery 1.0: one natural-language query structured with the K-Fashion standard
    vocabulary. Definition: contracts/structured-query.md (owner: vogue-orchestrator)."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    request_id: NonEmptyStr
    original_query: NonEmptyStr
    trend_terms: TrendTerms
    requested: Requested = Field(default_factory=Requested)
    unsupported: list[UnsupportedCondition] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)  # reserved, always [] for now


def export_json_schema() -> dict[str, Any]:
    """JSON Schema of the emitted StructuredQuery (serialization mode: every field is always present)."""
    schema = StructuredQuery.model_json_schema(mode="serialization")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
