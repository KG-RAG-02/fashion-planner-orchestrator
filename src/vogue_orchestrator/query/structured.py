"""The StructuredQuery path ("v2 path": prompt v6 -> draft v4 -> grouped draft v3 -> resolver -> StructuredQuery).
Used by the UI server since 2026-11-11 (server.structure_query_for_ui, decision FW-1).

    query --build_prompt(version="v6")--> bundle --DraftProvider--> draft v4 JSON (flat, `item` per condition)
          --draft_v4.group_draft--> draft v3 (per item) --resolver, per item--> --assemble_structured_query-->
          StructuredQuery
    A draft_v3 prompt (v4, archived) skips the grouping step: the model writes draft v3 itself.

Status (2026-11-11, FW-1): the UI server calls `structure_query_v2` and converts the result for trend with
query/to_trend.py (first item + first season only); nothing is sent to an agent yet. The evaluation
script (scripts/evaluate_query_structuring.py) runs the same function. The archived v1 path
(`structure_query` in structure.py: prompt v1 -> TrendQuery) is kept but no longer used by the UI.
The prompt version of this path is STRUCTURED_PROMPT_VERSION below, not configs/query_structuring.json
(that `prompt.version` drives the v1 path only).

Assembly rules (pure code, no LLM, no I/O). Decisions IT-1..IT-7 (2026-11-11) group conditions per item:
  - Seasons and trend names are query-wide (IT-5″). `seasons` (SQ-2) = every named season in query order,
    A-1 year rule for each, duplicates removed; none named -> [default_next].
  - Every draft item's words are looked up like a category condition (dictionary first, then the model's
    suggestion; resolve.py). A standard category -> an item. Several categories in one expression
    ("셔츠 원피스", OQ-10′) -> one item each, with the same conditions. No standard category -> an
    out-of-scope item (IT-7) with the raw texts of its conditions; it never becomes an item.
  - Conditions of `common` are COPIED into every item (IT-1), and into every out-of-scope item's raw list.
    An item's own conditions stay with that item (IT-2). Exception IT-9: a gender word before the FIRST of
    several items goes to every item when no other item names a gender (warning `leading_gender_shared`).
  - No item named at all -> one item with `category: null` (IT-6) holding the common conditions.
  - Items are ordered by where their words first appear in the query (IT-4: only items[0] goes to trend
    today) and get item_id "i1", "i2", ... in that order (IT-8). Out-of-scope items are ordered the same way.
  - Per item: values only from the resolver (`match` set by code), gender merge and include/exclude
    conflicts (resolve_conditions), de-duplication by (polarity, kind, value) - categories (polarity,
    major, minor); an unresolved value compares by its raw text; the first raw wins. Unsupported
    conditions de-duplicated by (type, raw, polarity); `reason` texts from configs/query_structuring.json.
  - Mis-placed categories (label level, not word level - W-10): a category INCLUDE written as a condition
    becomes an item (warning `category_condition_moved_to_item`); an included category sent to
    `unsupported` becomes an item looked up with the dictionary only (decision OQ-13′: a dictionary answer
    gives an item + warning `unsupported_category_resolved`, otherwise it is out of scope); an EXCLUDED
    category in `unsupported` is looked up with the dictionary only and becomes an excluded category when
    the dictionary knows it (same warning).
  - Decision OQ-12′: `structure_query_v2` repairs wrong labels before validation (draft_v3.repair_draft_v3_labels).
  - `warnings` holds codes only (no user text).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from pydantic import ValidationError

from . import errors
from .assemble import (
    GENERIC_UNSUPPORTED_REASON, UNSUPPORTED_REASONS, WARN_NO_CONDITIONS_EXTRACTED, WARN_RAW_NOT_IN_QUERY,
    WARN_SEASON_YEAR_INFERRED,
)
from .config import QueryConfigError
from .draft import DraftUnsupported
from . import draft_v3, draft_v4
from .draft_v3 import QueryDraftV3, repair_draft_v3_labels
from .draft_v4 import QueryDraftV4, group_draft, repair_draft_v4_labels
from .errors import QueryStructuringError
from .lexicon import Lexicon, norm_key
from .models import (
    ATTRIBUTE_REF_TYPES, AttributeFilter, AttributeValues, CategoryRef, GenderFilter, GenderRef, OutOfScopeItem,
    QueryItem, SeasonRequest, StructuredQuery, TermRef, TrendTerms, UnsupportedCondition,
)
from .prompt import build_prompt
from .resolve import Condition, ResolvedValue, resolve_condition, resolve_conditions
from .season import resolve_season_year
from .structure import MAX_QUERY_CHARS, DraftProvider, _as_object

STRUCTURED_PROMPT_VERSION = "v6"  # prompt folder this path runs (draft_v4 since v5; v4 = draft_v3 until FL-1, 2026-11-11)
WARN_UNSUPPORTED_CATEGORY_RESOLVED = "unsupported_category_resolved"
WARN_CATEGORY_CONDITION_MOVED_TO_ITEM = "category_condition_moved_to_item"
WARN_ONLY_OUT_OF_SCOPE_ITEMS = "only_out_of_scope_items"  # items == []: nothing to send to trend
WARN_STRAY_CATEGORY_VALUE = "stray_category_value"  # an included category produced inside an item's conditions
WARN_LEADING_GENDER_SHARED = "leading_gender_shared"  # IT-9: gender before the first item moved to every item


@dataclass(frozen=True)
class StructuredAssembly:
    structured_query: StructuredQuery
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class StructuredQueryResult:
    structured_query: StructuredQuery
    draft: QueryDraftV3 | QueryDraftV4  # what the model wrote (draft v4 before grouping)
    warnings: tuple[str, ...]
    prompt_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_version": self.prompt_version,
            "structured_query": self.structured_query.model_dump(mode="json"),
            "draft": self.draft.model_dump(mode="json"),
            "warnings": list(self.warnings),
        }


@dataclass
class _ItemSpec:
    """One item expression of the draft, before its category is looked up."""

    raw: str
    suggestion: Optional[str]
    conditions: list[Condition] = field(default_factory=list)
    unsupported: list[DraftUnsupported] = field(default_factory=list)
    from_unsupported: bool = False  # came from `unsupported` (type category): dictionary only (OQ-13′)


def assemble_structured_query(
    draft: QueryDraftV3,
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
    # the next fashion season computed from `today` (same as v1). Same (year, season) twice -> kept once.
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

    # look every item expression up; keep (position, order, payload) so the result follows the query order
    found: list[tuple[int, int, ResolvedValue, list[ResolvedValue], _ItemSpec]] = []
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
        else:
            raws = [c.raw for c in spec.conditions] + [u.raw for u in spec.unsupported] + common_raws
            out_of_scope.append((position, order, OutOfScopeItem(
                raw=spec.raw, reason=category_reason, conditions_raw=list(dict.fromkeys(raws)))))

    items: list[QueryItem] = []
    if not specs:
        # IT-6: no item named -> one item over every category, holding the common conditions
        items.append(_build_item("i1", None, [], common_conditions, common_unsupported, lexicon, gender_aliases, warn))
    for _, _, category, others, spec in sorted(found, key=lambda entry: entry[:2]):
        items.append(_build_item(f"i{len(items) + 1}", category, others, spec.conditions + common_conditions,
                                 spec.unsupported + common_unsupported, lexicon, gender_aliases, warn))
    if not items:
        warn(WARN_ONLY_OUT_OF_SCOPE_ITEMS)

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
        missing_fields=[],  # ASSUMPTION A-2 (same as v1)
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


def structure_query_v2(
    query: str,
    *,
    request_id: str,
    draft_provider: DraftProvider,
    today: date | None = None,
    prompt_version: str = STRUCTURED_PROMPT_VERSION,
) -> StructuredQueryResult:
    """Structure one query with a draft_v4 prompt (or the archived draft_v3 prompt v4). Same input checks and
    fixed-message errors as `structure_query`; the assembled document is a StructuredQuery."""
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise QueryStructuringError(errors.INPUT_INVALID)
    if not isinstance(request_id, str) or not request_id.strip():
        raise QueryStructuringError(errors.INPUT_INVALID)
    if today is not None and not isinstance(today, date):
        raise QueryStructuringError(errors.INPUT_INVALID)
    today = today or date.today()

    bundle = build_prompt(query, prompt_version)
    if bundle.format not in (draft_v3.DRAFT_FORMAT, draft_v4.DRAFT_FORMAT):
        raise QueryConfigError(f"prompt {prompt_version!r} has format {bundle.format!r}; this path needs "
                               f"{draft_v4.DRAFT_FORMAT!r} (or {draft_v3.DRAFT_FORMAT!r})")
    try:
        produced = draft_provider(bundle)
    except Exception:
        raise QueryStructuringError(errors.PROVIDER_FAILED) from None

    written: QueryDraftV3 | QueryDraftV4
    grouping: tuple[str, ...] = ()
    try:
        if bundle.format == draft_v4.DRAFT_FORMAT:
            payload, repairs = repair_draft_v4_labels(_as_object(produced))  # OQ-12′
            written = QueryDraftV4.model_validate(payload)
            grouped = group_draft(written, query)  # FL-1: flat conditions -> per item
            draft, grouping = grouped.draft, grouped.warnings
        else:
            payload, repairs = repair_draft_v3_labels(_as_object(produced))  # OQ-12′
            written = draft = QueryDraftV3.model_validate(payload)
    except ValidationError:
        raise QueryStructuringError(errors.DRAFT_INVALID) from None

    try:
        assembled = assemble_structured_query(draft, request_id=request_id, original_query=query, today=today)
    except Exception:
        raise QueryStructuringError(errors.STRUCTURED_QUERY_INVALID) from None

    warnings = tuple(dict.fromkeys((*repairs, *grouping, *assembled.warnings)))
    return StructuredQueryResult(
        structured_query=assembled.structured_query, draft=written, warnings=warnings, prompt_version=bundle.version,
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
