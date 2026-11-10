"""Assemble a contract-valid TrendQuery from an LLM draft. Pure code, no LLM, no I/O.

Responsibilities that belong to code (not to the model):
  - request_id and original_query are copied in, never generated.
  - A season the query does not name becomes the next fashion season ("default_next").
  - Major category is derived from minor category; values outside the closed vocabulary never
    reach the output as filters: a bad attribute/gender value becomes null (no filter), a
    non-standard category or attribute type is recorded in `unsupported`. Nothing is dropped silently.
  - `reason` texts of unsupported conditions come from a fixed table, not from the model.

`warnings` holds developer-facing codes only (no user text) describing every correction made.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import vocabulary as vocab
from .config import CONFIG
from .legacy_v1 import DraftCategory, QueryDraft  # v1 draft until the StructuredQuery path (plan U4)
from .trend_query import (
    CategoryFilter, CategoryRef, GenderRef, Requested, RequestedAttribute, SeasonRequest,
    TermRef, TrendQuery, TrendTerms, UnsupportedCondition,
)
from .season import resolve_season_year

WARN_SEASON_YEAR_INFERRED = "season_year_inferred"
WARN_CATEGORY_NOT_STANDARD = "category_not_standard"
WARN_CATEGORY_MAJOR_OVERRIDDEN = "category_major_overridden"
WARN_ATTRIBUTE_VALUE_NOT_IN_VOCABULARY = "attribute_value_not_in_vocabulary"
WARN_ATTRIBUTE_TYPE_UNSUPPORTED = "attribute_type_unsupported"
WARN_GENDER_VALUE_NOT_IN_VOCABULARY = "gender_value_not_in_vocabulary"
WARN_RAW_NOT_IN_QUERY = "raw_not_in_query"
WARN_NO_CONDITIONS_EXTRACTED = "no_conditions_extracted"

# Fixed reason texts from configs/query_structuring.json (unsupported.reasons / generic_reason).
# "brand" is the text used in the contract snapshot's own example. Counts come from the vocabulary.
UNSUPPORTED_REASONS: dict[str, str] = {
    kind: text.format(major_count=len(vocab.CATEGORY_MAJOR), minor_count=len(vocab.CATEGORY_MINOR))
    for kind, text in CONFIG.unsupported_reasons.items()
}
GENERIC_UNSUPPORTED_REASON = CONFIG.generic_unsupported_reason


@dataclass(frozen=True)
class AssemblyResult:
    trend_query: TrendQuery
    warnings: tuple[str, ...]


def assemble_trend_query(
    draft: QueryDraft, *, request_id: str, original_query: str, today: date,
) -> AssemblyResult:
    warnings: list[str] = []

    def warn(code: str) -> None:
        if code not in warnings:
            warnings.append(code)

    unsupported: list[UnsupportedCondition] = []
    seen_unsupported: set[tuple[str, str]] = set()

    def add_unsupported(kind: str, raw: str) -> None:
        kind = kind.strip() or "other"
        if (kind, raw) in seen_unsupported:
            return
        seen_unsupported.add((kind, raw))
        reason = UNSUPPORTED_REASONS.get(kind, GENERIC_UNSUPPORTED_REASON)
        unsupported.append(UnsupportedCondition(type=kind, raw=raw, reason=reason))

    # Season: named by the user, or the next fashion season computed from `today`.
    if draft.season is None:
        season = SeasonRequest.default_next(today)
    else:
        year = draft.season.year
        if year is None:
            year = resolve_season_year(draft.season.season, today)
            warn(WARN_SEASON_YEAR_INFERRED)
        season = SeasonRequest(
            year=year, season=draft.season.season, raw=draft.season.raw, origin="user_requested",
        )

    style_names = _terms(draft.style_names)
    item_terms = _terms(draft.item_terms)

    def convert_category(entry: DraftCategory) -> CategoryRef | None:
        if entry.minor is not None:
            if entry.minor not in vocab.CATEGORY_MINOR:
                add_unsupported("category", entry.raw)
                warn(WARN_CATEGORY_NOT_STANDARD)
                return None
            if entry.major is not None and entry.major != vocab.CATEGORY_MINOR_TO_MAJOR[entry.minor]:
                warn(WARN_CATEGORY_MAJOR_OVERRIDDEN)
            return CategoryRef(raw=entry.raw, minor=entry.minor)
        if entry.major is not None and entry.major in vocab.CATEGORY_MAJOR:
            return CategoryRef(raw=entry.raw, major=entry.major)
        add_unsupported("category", entry.raw)
        warn(WARN_CATEGORY_NOT_STANDARD)
        return None

    def convert_categories(entries: list[DraftCategory]) -> list[CategoryRef]:
        converted: list[CategoryRef] = []
        for entry in entries:
            ref = convert_category(entry)
            if ref is not None and ref not in converted:
                converted.append(ref)
        return converted

    include = convert_categories(draft.include_categories)
    exclude = convert_categories(draft.exclude_categories)

    gender: GenderRef | None = None
    if draft.gender is not None:
        gender_value = draft.gender.value
        if gender_value is not None and gender_value not in vocab.GENDERS:
            gender_value = None
            warn(WARN_GENDER_VALUE_NOT_IN_VOCABULARY)
        gender = GenderRef(raw=draft.gender.raw, value=gender_value)

    attributes: list[RequestedAttribute] = []
    for entry in draft.attributes:
        if entry.type not in vocab.ATTRIBUTE_TYPES:
            add_unsupported("attribute", entry.raw)
            warn(WARN_ATTRIBUTE_TYPE_UNSUPPORTED)
            continue
        value = entry.value
        if value is not None and value not in vocab.ATTRIBUTE_VALUES[entry.type]:
            value = None  # no standard value -> no filter (contract rule)
            warn(WARN_ATTRIBUTE_VALUE_NOT_IN_VOCABULARY)
        attribute = RequestedAttribute(type=entry.type, raw=entry.raw, value=value)
        if attribute not in attributes:
            attributes.append(attribute)

    for entry in draft.unsupported:
        add_unsupported(entry.type, entry.raw)

    # A raw expression that is not a substring of the query means the model paraphrased it.
    raws = [draft.season.raw] if draft.season else []
    raws += [t.raw for t in (*draft.style_names, *draft.item_terms)]
    raws += [c.raw for c in (*draft.include_categories, *draft.exclude_categories)]
    raws += [draft.gender.raw] if draft.gender else []
    raws += [a.raw for a in draft.attributes] + [u.raw for u in draft.unsupported]
    if any(raw not in original_query for raw in raws):
        warn(WARN_RAW_NOT_IN_QUERY)

    if not raws:
        warn(WARN_NO_CONDITIONS_EXTRACTED)

    trend_query = TrendQuery(
        request_id=request_id,
        original_query=original_query,
        trend_terms=TrendTerms(season=season, style_names=style_names, item_terms=item_terms),
        requested=Requested(
            categories=CategoryFilter(include=include, exclude=exclude),
            gender=gender,
            attributes=attributes,
        ),
        unsupported=unsupported,
        # ASSUMPTION A-2: the contract does not name the fields that belong here and the trend
        # side never reads it, so nothing is inferred. Always empty until the owner defines it.
        missing_fields=[],
    )
    return AssemblyResult(trend_query=trend_query, warnings=tuple(warnings))


def _terms(entries: list) -> list[TermRef]:
    terms: list[TermRef] = []
    for entry in entries:
        term = TermRef(raw=entry.raw, value=None)
        if term not in terms:
            terms.append(term)
    return terms
