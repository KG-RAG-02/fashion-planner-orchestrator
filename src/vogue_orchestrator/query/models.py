"""Pydantic models of the TrendQuery contract (the output of query structuring).

Owner of the contract: trend-analysis-agent. This is a LOCAL implementation of the snapshot in
docs/contracts/ (decision D1 = (d)): this repository does not import the trend package; the two
sides exchange JSON. tests/test_query_models.py compares this model's JSON Schema with the
snapshot, so drift is detected. Field names and validation rules mirror the original on purpose.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from . import vocabulary as vocab
from .season import next_fashion_season

# Closed value sets come from the generated vocabulary module (a tuple subscript means several values).
Season = Literal[vocab.SEASONS]  # type: ignore[valid-type]
SeasonOrigin = Literal[vocab.SEASON_ORIGINS]  # type: ignore[valid-type]
Gender = Literal[vocab.GENDERS]  # type: ignore[valid-type]
CategoryMajor = Literal[vocab.CATEGORY_MAJOR]  # type: ignore[valid-type]
CategoryMinor = Literal[vocab.CATEGORY_MINOR]  # type: ignore[valid-type]
AttributeType = Literal[vocab.ATTRIBUTE_TYPES]  # type: ignore[valid-type]


class SeasonRequest(BaseModel):
    year: int
    season: Season
    raw: Optional[str] = None
    origin: SeasonOrigin

    @classmethod
    def default_next(cls, today: date) -> "SeasonRequest":
        year, season = next_fashion_season(today)
        return cls(year=year, season=season, raw=None, origin="default_next")


class TermRef(BaseModel):
    raw: str
    value: Optional[str] = None  # no vocabulary for trend names / products yet, so always null


class TrendTerms(BaseModel):
    season: SeasonRequest
    style_names: list[TermRef] = Field(default_factory=list)
    item_terms: list[TermRef] = Field(default_factory=list)


class CategoryRef(BaseModel):
    raw: str
    major: Optional[CategoryMajor] = None
    minor: Optional[CategoryMinor] = None

    @model_validator(mode="after")
    def _derive_major(self) -> "CategoryRef":
        # When a minor category is given, the major one is always derived from it.
        if self.minor is not None:
            self.major = vocab.CATEGORY_MINOR_TO_MAJOR[self.minor]
        return self


class CategoryFilter(BaseModel):
    include: list[CategoryRef] = Field(default_factory=list)  # empty = all four major categories
    exclude: list[CategoryRef] = Field(default_factory=list)


class GenderRef(BaseModel):
    raw: Optional[str] = None
    value: Optional[Gender] = None


class RequestedAttribute(BaseModel):
    type: AttributeType
    raw: str
    value: Optional[str] = None

    @model_validator(mode="after")
    def _value_in_vocabulary(self) -> "RequestedAttribute":
        if self.value is not None and self.value not in vocab.ATTRIBUTE_VALUES[self.type]:
            raise ValueError(f"{self.value!r} is not a valid {self.type} value")
        return self


class UnsupportedCondition(BaseModel):
    type: str
    raw: str
    reason: str


class Requested(BaseModel):
    categories: CategoryFilter = Field(default_factory=CategoryFilter)
    gender: Optional[GenderRef] = None
    attributes: list[RequestedAttribute] = Field(default_factory=list)


class TrendQuery(BaseModel):
    request_id: str
    original_query: str
    trend_terms: TrendTerms
    requested: Requested = Field(default_factory=Requested)
    unsupported: list[UnsupportedCondition] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
