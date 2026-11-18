"""Deterministic conversion from a validated query draft to StructuredQuery.

This module contains no provider calls or I/O. It applies the query rules to a validated
draft and records warning codes without copying user text into diagnostics:

* Seasons are query-wide, ordered, and deduplicated; missing years are resolved from the
  supplied date, and an unnamed season uses ``default_next``.
* Common conditions are copied into every item. Included category conditions become items;
  excluded categories remain filters. Dictionary-only category rescue is used for category
  values the draft marked unsupported.
* Item words resolve first as categories, then as exact/alias attribute values. Unresolved
  items remain in ``out_of_scope_items`` with their original conditions.
* Item and out-of-scope ordering follows the text position, with draft order breaking ties.
  Leading gender is shared only under the IT-9 rule in :func:`_share_leading_gender`.
* Resolved values and unsupported conditions are deduplicated before model construction.

The higher-level prompt/provider flow is in :mod:`query.structured`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Optional, Sequence

from .assembly_rules import (
    GENERIC_UNSUPPORTED_REASON, UNSUPPORTED_REASONS, WARN_NO_CONDITIONS_EXTRACTED,
    WARN_RAW_NOT_IN_QUERY, WARN_SEASON_YEAR_INFERRED,
)
from .draft import DraftUnsupported, GroupedQueryDraft
from .lexicon import Lexicon, norm_key
from .models import (
    ATTRIBUTE_REF_TYPES, AttributeFilter, AttributeValues, CategoryRef, GenderFilter, GenderRef,
    OutOfScopeItem, QueryItem, SeasonRequest, StructuredQuery, TermRef, TrendTerms,
    UnsupportedCondition,
)
from .resolve import Condition, ResolvedValue, resolve_condition, resolve_conditions
from .season import resolve_season_year

WARN_UNSUPPORTED_CATEGORY_RESOLVED = "unsupported_category_resolved"
WARN_CATEGORY_CONDITION_MOVED_TO_ITEM = "category_condition_moved_to_item"
# IT-7′ (2026-11-12): no named item is a standard category -> one item over every category (common conditions only)
WARN_ONLY_OUT_OF_SCOPE_ITEMS = "only_out_of_scope_items"
WARN_ITEM_WORD_IS_CONDITION = "item_word_used_as_condition"  # IT-10: an item word that is an attribute value
# IT-10: an item word resolving to several attribute kinds -> exact before alias, then this order
ITEM_WORD_ATTRIBUTE_PRIORITY = ("sleeve_length", "body_length", "color", "material", "pattern", "fit", "detail")
WARN_STRAY_CATEGORY_VALUE = "stray_category_value"  # an included category produced inside an item's conditions
WARN_LEADING_GENDER_SHARED = "leading_gender_shared"  # IT-9: gender before the first item moved to every item


@dataclass(frozen=True)
class StructuredAssembly:
    structured_query: StructuredQuery
    warnings: tuple[str, ...]


@dataclass
class _ItemSpec:
    """One item expression of the draft, before its category is looked up."""

    raw: str
    suggestion: Optional[str]
    conditions: list[Condition] = field(default_factory=list)
    unsupported: list[DraftUnsupported] = field(default_factory=list)
    from_unsupported: bool = False  # came from `unsupported` (type category): dictionary only (OQ-13′)


def assemble_structured_query(
    draft: GroupedQueryDraft,
    *,
    request_id: str,
    original_query: str,
    today: date,
    lexicon: Optional[Lexicon] = None,
    gender_aliases: Optional[Mapping[str, Sequence[str]]] = None,
) -> StructuredAssembly:
    warnings: list[str] = []

    def warn(code: str) -> None:
        if code not in warnings:
            warnings.append(code)

    # seasons (SQ-2): every season the user named, in query order, the A-1 year rule for each; none named ->
    # the next fashion season computed from `today` (same as the initial season rule). Same (year, season) twice -> kept once.
    seasons: list[SeasonRequest] = []
    for named in draft.seasons:
        year = named.year
        if year is None:
            year = resolve_season_year(named.season, today)
            warn(WARN_SEASON_YEAR_INFERRED)
        if any((s.year, s.season) == (year, named.season) for s in seasons):
            continue
        seasons.append(SeasonRequest(year=year, season=named.season, raw=named.raw, origin="user_requested"))
    if not seasons:
        seasons.append(SeasonRequest.default_next(today))

    def sort_out(conditions: Sequence, unsupported: Sequence[DraftUnsupported]
                 ) -> tuple[list[Condition], list[DraftUnsupported], list[_ItemSpec]]:
        """Split one conditions/unsupported pair into what stays and the categories that are items."""
        kept: list[Condition] = []
        kept_unsupported: list[DraftUnsupported] = []
        moved: list[_ItemSpec] = []
        for c in conditions:
            if c.kind == "category" and c.polarity == "include":
                moved.append(_ItemSpec(c.raw, c.suggestion))
                warn(WARN_CATEGORY_CONDITION_MOVED_TO_ITEM)
            else:
                kept.append(Condition(c.raw, c.kind, c.polarity, c.suggestion))
        for u in unsupported:
            if u.type != "category":
                kept_unsupported.append(u)
            elif u.polarity == "include":
                moved.append(_ItemSpec(u.raw, None, from_unsupported=True))
            else:
                # OQ-13′: dictionary only (suggestion None), so only a dictionary answer rescues it
                recheck = resolve_condition(Condition(u.raw, "category", "exclude", None), lexicon=lexicon)
                if recheck.values:
                    kept.append(Condition(u.raw, "category", "exclude", None))
                    warn(WARN_UNSUPPORTED_CATEGORY_RESOLVED)
                else:
                    kept_unsupported.append(u)
        return kept, kept_unsupported, moved

    common_conditions, common_unsupported, specs = sort_out(draft.common.conditions, draft.common.unsupported)
    for item in draft.items:
        conditions, unsupported, moved = sort_out(item.conditions, item.unsupported)
        specs.append(_ItemSpec(item.raw, item.suggestion, conditions, unsupported))
        specs.extend(moved)
    if _share_leading_gender(specs, common_conditions, original_query):
        warn(WARN_LEADING_GENDER_SHARED)

    # look every item expression up; keep (position, order, payload) so the result follows the query order.
    # category None = an item word that is an attribute value (IT-10): an item over every category.
    found: list[tuple[int, int, Optional[ResolvedValue], list[ResolvedValue], _ItemSpec]] = []
    out_of_scope: list[tuple[int, int, OutOfScopeItem]] = []
    category_reason = UNSUPPORTED_REASONS.get("category", GENERIC_UNSUPPORTED_REASON)
    common_raws = [c.raw for c in common_conditions] + [u.raw for u in common_unsupported]
    for order, spec in enumerate(specs):
        position = _position(original_query, spec.raw)
        suggestion = None if spec.from_unsupported else spec.suggestion
        resolution = resolve_condition(Condition(spec.raw, "category", "include", suggestion), lexicon=lexicon)
        for w in resolution.warnings:
            warn(w.code)
        categories = [v for v in resolution.values if v.kind == "category"]
        others = [v for v in resolution.values if v.kind != "category"]  # e.g. a dictionary route
        if categories:
            if spec.from_unsupported:
                warn(WARN_UNSUPPORTED_CATEGORY_RESOLVED)
            for category in categories:
                found.append((position, order, category, others, spec))
        elif (attribute := _item_word_as_attribute(spec.raw, lexicon)) is not None:
            found.append((position, order, None, [attribute], spec))
            warn(WARN_ITEM_WORD_IS_CONDITION)
        else:
            raws = [c.raw for c in spec.conditions] + [u.raw for u in spec.unsupported] + common_raws
            out_of_scope.append((position, order, OutOfScopeItem(
                raw=spec.raw, reason=category_reason, conditions_raw=list(dict.fromkeys(raws)))))

    items: list[QueryItem] = []
    if not found:
        # IT-6: no item named -> one item over every category, holding the common conditions.
        # IT-7′: every named item out of scope -> the same item, so the season still reaches trend; the
        # out-of-scope items' own conditions stay in out_of_scope_items (never widen this item).
        if out_of_scope:
            warn(WARN_ONLY_OUT_OF_SCOPE_ITEMS)
        items.append(_build_item("i1", None, [], common_conditions, common_unsupported, lexicon, gender_aliases, warn))
    for _, _, category, others, spec in sorted(found, key=lambda entry: entry[:2]):
        items.append(_build_item(f"i{len(items) + 1}", category, others, spec.conditions + common_conditions,
                                 spec.unsupported + common_unsupported, lexicon, gender_aliases, warn))

    raws = draft.raws()
    if any(raw not in original_query for raw in raws):
        warn(WARN_RAW_NOT_IN_QUERY)
    if not raws:
        warn(WARN_NO_CONDITIONS_EXTRACTED)

    structured = StructuredQuery(
        request_id=request_id,
        original_query=original_query,
        trend_terms=TrendTerms(seasons=seasons, style_names=_terms(draft.style_names),
                               product_model_names=_terms(draft.product_model_names)),
        items=items,
        out_of_scope_items=[entry for _, _, entry in sorted(out_of_scope, key=lambda e: e[:2])],
        missing_fields=[],  # ASSUMPTION A-2 (same as the initial season rule)
    )
    return StructuredAssembly(structured_query=structured, warnings=tuple(warnings))


def _build_item(item_id: str, category: Optional[ResolvedValue], extra_values: Sequence[ResolvedValue],
                conditions: Sequence[Condition], draft_unsupported: Sequence[DraftUnsupported],
                lexicon: Optional[Lexicon], gender_aliases, warn) -> QueryItem:
    resolution = resolve_conditions(conditions, lexicon=lexicon, gender_aliases=gender_aliases)
    for w in resolution.warnings:
        warn(w.code)

    excluded: list[CategoryRef] = []
    genders: dict[str, list[GenderRef]] = {"include": [], "exclude": []}
    attributes: dict[str, dict[str, list]] = {side: {kind: [] for kind in ATTRIBUTE_REF_TYPES} for side in genders}
    seen: set[tuple] = set()
    for v in (*extra_values, *resolution.values):
        if v.kind == "category":
            key: tuple = (v.polarity, "category", v.major, v.minor)
        else:
            key = (v.polarity, v.kind, v.value if v.value is not None else ("raw", norm_key(v.raw)))
        if key in seen:
            continue
        seen.add(key)
        if v.kind == "category":
            if v.polarity == "exclude":
                excluded.append(CategoryRef(raw=v.raw, major=v.major, minor=v.minor, match=v.match))
            else:  # cannot come from a draft (sort_out moves included categories); kept visible, not silent
                warn(WARN_STRAY_CATEGORY_VALUE)
        elif v.kind == "gender":
            genders[v.polarity].append(GenderRef(raw=v.raw, value=v.value, match=v.match))
        else:
            attributes[v.polarity][v.kind].append(ATTRIBUTE_REF_TYPES[v.kind](raw=v.raw, value=v.value, match=v.match))

    unsupported: list[UnsupportedCondition] = []
    seen_unsupported: set[tuple[str, str, str]] = set()
    for type_, raw, polarity in [*((u.type, u.raw, u.polarity) for u in resolution.unsupported),
                                 *((u.type, u.raw, u.polarity) for u in draft_unsupported)]:
        key = (type_, norm_key(raw), polarity)
        if key in seen_unsupported:
            continue
        seen_unsupported.add(key)
        reason = UNSUPPORTED_REASONS.get(type_, GENERIC_UNSUPPORTED_REASON)
        unsupported.append(UnsupportedCondition(type=type_, raw=raw, reason=reason, polarity=polarity))

    return QueryItem(
        item_id=item_id,  # IT-8: position in query order, 1-based
        category=None if category is None else CategoryRef(
            raw=category.raw, major=category.major, minor=category.minor, match=category.match),
        excluded_categories=excluded,
        gender=GenderFilter(**genders),
        attributes=AttributeFilter(include=AttributeValues(**attributes["include"]),
                                   exclude=AttributeValues(**attributes["exclude"])),
        unsupported=unsupported,
    )


def _share_leading_gender(specs: list[_ItemSpec], common: list[Condition], query: str) -> bool:
    """Decision IT-9 (2026-11-11): a gender word written BEFORE the first item of a query with several items
    is meant for every item ("여성 셔츠랑 스커트" = 여성 셔츠 + 여성 스커트), unless another item or the common
    block names a gender of its own ("여성 셔츠랑 남성 바지" stays as it is). The model may put such a word on
    the first item only (IT-2); it is moved to `common` here, so it is copied into every item (IT-1).
    Rule on kind and position only, no word list (W-10). Returns True when something was moved."""
    if len(specs) < 2 or any(c.kind == "gender" for c in common):
        return False
    first = min(specs, key=lambda spec: _position(query, spec.raw))  # ties: the first written
    start = query.find(first.raw)
    if start < 0 or any(c.kind == "gender" for spec in specs if spec is not first for c in spec.conditions):
        return False
    leading = [c for c in first.conditions if c.kind == "gender" and 0 <= query.find(c.raw) < start]
    if not leading:
        return False
    first.conditions = [c for c in first.conditions if all(c is not moved for moved in leading)]
    common.extend(leading)
    return True


def _item_word_as_attribute(raw: str, lexicon: Optional[Lexicon]) -> Optional[ResolvedValue]:
    """Decision IT-10 (2026-11-12): an item word that is not a category but a dictionary attribute value
    ("반팔" -> sleeve_length 반팔) is that attribute, not an out-of-scope item. Dictionary only (no model
    suggestion), `exact`/`alias` only: an `approx` answer ("벨트" -> detail 띠) would turn a request for a
    belt into a search for a detail, so it stays out of scope. Several kinds -> exact before alias, then
    ITEM_WORD_ATTRIBUTE_PRIORITY ("민소매" -> sleeve_length, "체크" -> pattern). Rule on kinds, no word list."""
    candidates: list[tuple[int, int, ResolvedValue]] = []
    for rank, kind in enumerate(ITEM_WORD_ATTRIBUTE_PRIORITY):
        resolution = resolve_condition(Condition(raw, kind, "include", None), lexicon=lexicon)
        for value in resolution.values:
            if value.kind == kind and value.value is not None and value.match in ("exact", "alias"):
                candidates.append((0 if value.match == "exact" else 1, rank, value))
    return min(candidates, key=lambda entry: entry[:2])[2] if candidates else None


def _position(query: str, raw: str) -> int:
    """Where an item's words first appear in the query (items follow the query order, IT-4). A raw that is
    not in the query (warning raw_not_in_query) goes last; ties keep the draft order."""
    index = query.find(raw)
    return index if index >= 0 else len(query)


def _terms(entries) -> list[TermRef]:
    terms: list[TermRef] = []
    for entry in entries:
        term = TermRef(raw=entry.raw)
        if term not in terms:
            terms.append(term)
    return terms
