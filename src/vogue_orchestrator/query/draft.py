"""The LLM-facing draft schema: what the model is asked to return.

The draft is deliberately smaller and looser than TrendQuery. The model only reports what the
query says (raw expressions plus values chosen from closed lists). Everything that can be decided
by code is NOT asked of the model: request_id, original_query, default season, major category
derived from minor, `reason` texts of unsupported conditions, `missing_fields`, final validation.
See assemble.py. Unknown keys are rejected so prompt drift is caught early.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import vocabulary as vocab
from .config import CONFIG

SeasonCode = Literal[vocab.SEASONS]  # type: ignore[valid-type]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftSeason(_Strict):
    # null when the query names no year; bounds from configs/query_structuring.json (draft.year_min/max)
    year: Optional[int] = Field(default=None, ge=CONFIG.draft_year_min, le=CONFIG.draft_year_max)
    season: SeasonCode
    raw: str = Field(min_length=1)


class DraftTerm(_Strict):
    raw: str = Field(min_length=1)


# Values below are plain strings on purpose: the assembler checks them against the vocabulary and
# routes anything outside it to `unsupported` (never silently drops it).
class DraftCategory(_Strict):
    raw: str = Field(min_length=1)
    major: Optional[str] = None
    minor: Optional[str] = None


class DraftGender(_Strict):
    raw: str = Field(min_length=1)
    value: Optional[str] = None


class DraftAttribute(_Strict):
    type: str
    raw: str = Field(min_length=1)
    value: Optional[str] = None


class DraftUnsupported(_Strict):
    type: str = Field(min_length=1)
    raw: str = Field(min_length=1)


class QueryDraft(_Strict):
    season: Optional[DraftSeason] = None
    style_names: list[DraftTerm] = Field(default_factory=list)
    item_terms: list[DraftTerm] = Field(default_factory=list)
    include_categories: list[DraftCategory] = Field(default_factory=list)
    exclude_categories: list[DraftCategory] = Field(default_factory=list)
    gender: Optional[DraftGender] = None
    attributes: list[DraftAttribute] = Field(default_factory=list)
    unsupported: list[DraftUnsupported] = Field(default_factory=list)
