"""StructuredQuery 1.0 -> TrendQuery (the input trend-analysis-agent knows today). Decisions IT-4, SQ-2, X4, FW-1.

The pipeline takes ONE item and ONE season today, so only `items[0]` (the item named first) and
`seasons[0]` (the season named first) are converted. Everything else stays in the StructuredQuery and
is reported, never dropped silently (X4 safety condition):

  - more items      -> warning `items_not_forwarded`
  - more seasons    -> warning `seasons_not_forwarded`
  - no item at all (`items == []`) -> no TrendQuery (None), warning `no_item_to_forward`; the caller does
    not call trend. Since IT-7′ (2026-11-12) the assembler always writes at least one item, so this is a guard.
  - `out_of_scope_items` (IT-7′) -> `TrendQuery.unsupported` with type `category` and the item's reason, so
    trend reports them as not applied; warning `conditions_not_forwarded`. Their own conditions are not sent.
  - conditions of items[0] that TrendQuery has no field for (excluded attributes, body/sleeve length,
    an excluded or several genders, gender without a standard value) -> written to
    `TrendQuery.unsupported` (type + raw + reason, trend already reads that list as "not applied")
    and warning `conditions_not_forwarded`.
  - items[0].unsupported -> `TrendQuery.unsupported`; TrendQuery has no polarity, so an excluded one
    says so in its reason.

Mapping of items[0]:
  category (or null = all categories) -> requested.categories.include ([] = all four majors)
  excluded_categories                 -> requested.categories.exclude
  gender: exactly one include with a value and no exclude -> requested.gender, otherwise not forwarded
  attributes.include of color/material/pattern/detail/fit -> requested.attributes (value null kept as
                                         null with its raw, like the v1 path)
`match` is not in TrendQuery and is not converted (the StructuredQuery keeps it).
Pure code, no I/O. Warnings are codes only (no user text).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import StructuredQuery
from .trend_query import (
    CategoryFilter, CategoryRef, GenderRef, Requested, RequestedAttribute, SeasonRequest, TermRef, TrendQuery,
    TrendTerms, UnsupportedCondition,
)
from . import vocabulary as vocab

WARN_ITEMS_NOT_FORWARDED = "items_not_forwarded"
WARN_SEASONS_NOT_FORWARDED = "seasons_not_forwarded"
WARN_NO_ITEM_TO_FORWARD = "no_item_to_forward"
WARN_CONDITIONS_NOT_FORWARDED = "conditions_not_forwarded"

NOT_IN_TREND_QUERY = "trend 입력(TrendQuery)에 담을 칸이 없어 반영되지 않음"
TREND_ATTRIBUTE_TYPES = tuple(vocab.ATTRIBUTE_TYPES)  # color, material, pattern, detail, fit
LENGTH_KINDS = ("body_length", "sleeve_length")


@dataclass(frozen=True)
class TrendConversion:
    trend_query: Optional[TrendQuery]  # None when there is no item to forward
    warnings: tuple[str, ...]

    def to_dict(self) -> Optional[dict]:
        return None if self.trend_query is None else self.trend_query.model_dump(mode="json")


def to_trend_query(sq: StructuredQuery) -> TrendConversion:
    warnings: list[str] = []

    def warn(code: str) -> None:
        if code not in warnings:
            warnings.append(code)

    if len(sq.trend_terms.seasons) > 1:
        warn(WARN_SEASONS_NOT_FORWARDED)
    if len(sq.items) > 1:
        warn(WARN_ITEMS_NOT_FORWARDED)
    if not sq.items:
        warn(WARN_NO_ITEM_TO_FORWARD)
        return TrendConversion(trend_query=None, warnings=tuple(warnings))

    first_season = sq.trend_terms.seasons[0]
    item = sq.items[0]
    unsupported: list[UnsupportedCondition] = []

    def not_forwarded(type_: str, raw: str, detail: str = "") -> None:
        unsupported.append(UnsupportedCondition(type=type_, raw=raw, reason=NOT_IN_TREND_QUERY + detail))
        warn(WARN_CONDITIONS_NOT_FORWARDED)

    categories = CategoryFilter(
        include=[] if item.category is None else [
            CategoryRef(raw=item.category.raw, major=item.category.major, minor=item.category.minor)],
        exclude=[CategoryRef(raw=c.raw, major=c.major, minor=c.minor) for c in item.excluded_categories],
    )

    gender: Optional[GenderRef] = None
    included, excluded = item.gender.include, item.gender.exclude
    if len(included) == 1 and not excluded and included[0].value is not None:
        gender = GenderRef(raw=included[0].raw, value=included[0].value)
    else:
        for ref in included:
            not_forwarded("gender", ref.raw)
        for ref in excluded:
            not_forwarded("gender", ref.raw, " (제외 요청)")

    attributes: list[RequestedAttribute] = []
    for kind in TREND_ATTRIBUTE_TYPES:
        for ref in getattr(item.attributes.include, kind):
            attributes.append(RequestedAttribute(type=kind, raw=ref.raw, value=ref.value))
        for ref in getattr(item.attributes.exclude, kind):
            not_forwarded(kind, ref.raw, " (제외 요청)")
    for kind in LENGTH_KINDS:
        for ref in getattr(item.attributes.include, kind):
            not_forwarded(kind, ref.raw)
        for ref in getattr(item.attributes.exclude, kind):
            not_forwarded(kind, ref.raw, " (제외 요청)")

    for entry in item.unsupported:
        reason = entry.reason + (" (제외 요청)" if entry.polarity == "exclude" else "")
        unsupported.append(UnsupportedCondition(type=entry.type, raw=entry.raw, reason=reason))

    for entry in sq.out_of_scope_items:  # IT-7′: searched over every category, reported as not applied
        unsupported.append(UnsupportedCondition(type="category", raw=entry.raw, reason=entry.reason))
        warn(WARN_CONDITIONS_NOT_FORWARDED)

    trend_query = TrendQuery(
        request_id=sq.request_id,
        original_query=sq.original_query,
        trend_terms=TrendTerms(
            season=SeasonRequest(year=first_season.year, season=first_season.season, raw=first_season.raw,
                                 origin=first_season.origin),
            style_names=[TermRef(raw=t.raw) for t in sq.trend_terms.style_names],
            item_terms=[TermRef(raw=t.raw) for t in sq.trend_terms.product_model_names],
        ),
        requested=Requested(categories=categories, gender=gender, attributes=attributes),
        unsupported=unsupported,
        missing_fields=list(sq.missing_fields),
    )
    return TrendConversion(trend_query=trend_query, warnings=tuple(warnings))
