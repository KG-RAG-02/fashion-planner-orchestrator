"""Value resolver (decision Q3 = gamma): turn LLM-extracted conditions into standard values. Stdlib only.

Input: one `Condition` per expression the LLM extracted from the query ({raw, kind, polarity,
suggestion}, draft v2). The LLM never sees the alias dictionary and never sets `match`.
Output: `ResolvedValue`s (with `match`), `UnsupportedItem`s and `ResolutionWarning`s. This module is
independent of the pydantic models; the assembler maps the output into StructuredQuery.

Rules (docs/structured-query-plan.md §2.1, contracts/structured-query.md §4):
  1. The dictionary always wins. Look `raw` up in the alias dictionary of the attribute(s) of `kind`:
     standard value -> `exact`, same-meaning alias -> `alias`, approximate entry -> `approx`.
     A suggestion that differs from the dictionary answer is dropped (`llm_suggestion_overridden`).
  2. Only when the dictionary has nothing, a suggestion inside that kind's standard list -> `llm`;
     otherwise value None -> `none` (with `suggestion_not_in_vocabulary` if one was given).
  3. Lookup targets: detail -> dictionary detail, then neckline, then collar (the contract's detail
     holds all three); color -> `normalize_color`; category -> the 4 major names first (`exact`; the
     dictionary only has hints for them), then the dictionary category (= minor) table;
     gender -> the fixed table in configs/query_structuring.json (decision G-1).
  4. Within-span fallback: if the whole `raw` gives nothing, retry with its whitespace tokens. The
     query sentence itself is never scanned (that caused "겨울" -> 울). Each distinct value found in
     one span becomes its own output with the same `raw`.
  5. Routes are followed (H5): the expression is re-resolved in the target attribute; a target that
     is not in the contract (shape, lengths) -> unsupported type `attribute`. Hints are never used.
  6. Categories (decision G-2): a minor the dictionary resolves is kept and never widened to its
     major. Without a dictionary answer only a *minor* suggestion is accepted (`llm`); a major
     suggestion is rejected (a major name in `raw` was already matched in step 3). Out-of-scope
     items (dictionary null value, e.g. 가방) and unresolved categories -> unsupported type `category`.
  7. A dictionary "null value" (no standard value on purpose) is a dictionary answer: no LLM fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

from . import vocabulary as vocab
from .lexicon import Lexicon, Normalized, default_lexicon, norm_key

__all__ = [
    "Condition",
    "KINDS",
    "POLARITIES",
    "Resolution",
    "ResolutionWarning",
    "ResolvedValue",
    "UnsupportedItem",
    "include_exclude_conflicts",
    "resolve_condition",
    "resolve_conditions",
]

KINDS: tuple[str, ...] = ("category", "color", "material", "pattern", "detail", "fit", "gender")
POLARITIES: tuple[str, ...] = ("include", "exclude")

# match values (contract §4)
MATCH_EXACT = "exact"
MATCH_ALIAS = "alias"
MATCH_APPROX = "approx"
MATCH_LLM = "llm"
MATCH_NONE = "none"
_MATCH_BY_DICTIONARY = {"standard": MATCH_EXACT, "alias": MATCH_ALIAS, "approx": MATCH_APPROX}

# unsupported types and reason keys (the assembler maps keys to the fixed texts in configs/)
UNSUPPORTED_CATEGORY = "category"
UNSUPPORTED_ATTRIBUTE = "attribute"
REASON_NULL_VALUE = "null_value"            # dictionary says: no standard value (out of scope, e.g. 가방)
REASON_NOT_IN_DICTIONARY = "not_in_dictionary"  # dictionary has nothing and no usable suggestion
REASON_ROUTE_NOT_IN_CONTRACT = "route_not_in_contract"  # dictionary routes to shape / a length

# warning codes (developer-facing, no user text)
WARN_LLM_SUGGESTION_OVERRIDDEN = "llm_suggestion_overridden"
WARN_SUGGESTION_NOT_IN_VOCABULARY = "suggestion_not_in_vocabulary"
WARN_CATEGORY_MAJOR_SUGGESTION_REJECTED = "category_major_suggestion_rejected"
WARN_ATTRIBUTE_ROUTED = "attribute_routed"
WARN_MULTIPLE_VALUES_IN_SPAN = "multiple_values_in_span"
WARN_DICTIONARY_VALUE_NOT_IN_VOCABULARY = "dictionary_value_not_in_vocabulary"
WARN_INCLUDE_EXCLUDE_CONFLICT = "include_exclude_conflict"

# contract kind -> dictionary attributes to try, in order
_DICTIONARY_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "category": ("category",),
    "color": ("color",),
    "material": ("material",),
    "pattern": ("pattern",),
    "detail": ("detail", "neckline", "collar"),
    "fit": ("fit",),
}
# dictionary attribute -> contract kind (attributes missing here are not in the contract)
_CONTRACT_KIND: dict[str, str] = {
    "category": "category", "color": "color", "material": "material", "pattern": "pattern",
    "detail": "detail", "neckline": "detail", "collar": "detail", "fit": "fit",
}
_MAX_ROUTE_HOPS = 3

# lookup source labels (ResolvedValue.looked_up_in) that are not dictionary attributes
SOURCE_CATEGORY_MAJOR = "category_major"
SOURCE_GENDER_STANDARD = "gender_standard"
SOURCE_GENDER_TABLE = "gender_table"


# ---------------------------------------------------------------------------- data types
@dataclass(frozen=True)
class Condition:
    """One condition expression from the LLM draft (v2)."""

    raw: str
    kind: str
    polarity: str
    suggestion: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.raw, str) or not self.raw.strip():
            raise ValueError("condition raw must be a non-empty string")
        if self.kind not in KINDS:
            raise ValueError(f"condition kind must be one of {KINDS}")
        if self.polarity not in POLARITIES:
            raise ValueError(f"condition polarity must be one of {POLARITIES}")
        if self.suggestion is not None and not isinstance(self.suggestion, str):
            raise ValueError("condition suggestion must be a string or None")


@dataclass(frozen=True)
class ResolvedValue:
    """A resolved (or unresolvable, value None) condition.

    `kind` is the contract kind after routing (category, gender, color, material, pattern, detail,
    fit); it can differ from the input kind (e.g. a "detail" 스트라이프 routed to pattern).
    For category, `major`/`minor` are set and `value` is the minor, or the major when no minor.
    `matched_text` is the text the dictionary/table matched (the whole raw or one token);
    `looked_up_in` names the table (dictionary attribute, "category_major", "gender_table", ...).
    Both are None for `llm` and `none`.
    """

    kind: str
    polarity: str
    raw: str
    value: Optional[str]
    match: str
    major: Optional[str] = None
    minor: Optional[str] = None
    matched_text: Optional[str] = None
    looked_up_in: Optional[str] = None


@dataclass(frozen=True)
class UnsupportedItem:
    """A condition the contract cannot express. `reason` is a REASON_* key; `detail` e.g. the route target."""

    type: str
    raw: str
    polarity: str
    reason: str
    detail: Optional[str] = None


@dataclass(frozen=True)
class ResolutionWarning:
    code: str
    raw: Optional[str] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class Resolution:
    values: tuple[ResolvedValue, ...] = ()
    unsupported: tuple[UnsupportedItem, ...] = ()
    warnings: tuple[ResolutionWarning, ...] = ()


# ---------------------------------------------------------------------------- internal outcomes
@dataclass(frozen=True)
class _Hit:
    kind: str           # contract kind
    value: str          # standard value (category: minor or major)
    matched_by: str     # dictionary kind: standard | alias | approx
    text: str           # text that matched
    attribute: str      # dictionary attribute or source label
    major: Optional[str] = None
    minor: Optional[str] = None
    routed_from: Optional[str] = None


@dataclass(frozen=True)
class _RoutedOut:
    """Routed to an attribute the contract does not have (shape, lengths)."""

    target: str
    text: str
    routed_from: str


@dataclass(frozen=True)
class _RoutedNoValue:
    """Routed to a contract kind, but the route carries no value and the target has none either."""

    kind: str
    text: str
    routed_from: str


_NULL = "null"  # dictionary null value marker (no standard value on purpose)

_Outcome = object  # _Hit | _RoutedOut | _RoutedNoValue | _NULL


# ---------------------------------------------------------------------------- public API
def resolve_conditions(
    conditions: Iterable[Condition],
    *,
    lexicon: Optional[Lexicon] = None,
    gender_aliases: Optional[Mapping[str, Sequence[str]]] = None,
) -> Resolution:
    """Resolve every condition (in order) and add `include_exclude_conflict` warnings.

    No de-duplication across conditions (the assembler does it); conflicts are computed on the
    resolved values (see `include_exclude_conflicts`).
    """
    values: list[ResolvedValue] = []
    unsupported: list[UnsupportedItem] = []
    warnings: list[ResolutionWarning] = []
    for condition in conditions:
        result = resolve_condition(condition, lexicon=lexicon, gender_aliases=gender_aliases)
        values.extend(result.values)
        unsupported.extend(result.unsupported)
        warnings.extend(result.warnings)
    warnings.extend(include_exclude_conflicts(values))
    return Resolution(tuple(values), tuple(unsupported), tuple(warnings))


def resolve_condition(
    condition: Condition,
    *,
    lexicon: Optional[Lexicon] = None,
    gender_aliases: Optional[Mapping[str, Sequence[str]]] = None,
) -> Resolution:
    """Resolve one condition (no conflict check)."""
    if condition.kind == "gender":
        return _resolve_gender(condition, _gender_table(gender_aliases))
    return _Resolver(lexicon or default_lexicon(), condition).run()


def include_exclude_conflicts(values: Iterable[ResolvedValue]) -> tuple[ResolutionWarning, ...]:
    """`include_exclude_conflict` for each value that is both included and excluded AT THE SAME LEVEL.

    Category: same major and minor ("하의 include + 팬츠 exclude" is narrowing, not a conflict; a minor
    exclusion never removes its major). Other kinds: same kind and value. Values None are ignored.
    """
    seen: dict[tuple, set[str]] = {}
    order: list[tuple] = []
    for item in values:
        if item.value is None:
            continue
        key = (item.kind, item.major, item.minor) if item.kind == "category" else (item.kind, item.value)
        if key not in seen:
            seen[key] = set()
            order.append(key)
        seen[key].add(item.polarity)
    warnings = []
    for key in order:
        if seen[key] >= set(POLARITIES):
            detail = f"{key[0]}:{key[1]}/{key[2]}" if key[0] == "category" else f"{key[0]}:{key[1]}"
            warnings.append(ResolutionWarning(WARN_INCLUDE_EXCLUDE_CONFLICT, detail=detail))
    return tuple(warnings)


# ---------------------------------------------------------------------------- gender (G-1)
def _gender_table(aliases: Optional[Mapping[str, Sequence[str]]]) -> dict[str, str]:
    if aliases is None:
        from .config import CONFIG  # read at import of config; imported lazily to keep this module light
        aliases = CONFIG.gender_aliases
    return {norm_key(spelling): value for value, spellings in aliases.items() for spelling in spellings}


def _resolve_gender(condition: Condition, table: Mapping[str, str]) -> Resolution:
    standard = {norm_key(g): g for g in vocab.GENDERS}

    def lookup(text: str) -> Optional[tuple[str, str, str]]:
        key = norm_key(text)
        if key in standard:
            return standard[key], MATCH_EXACT, SOURCE_GENDER_STANDARD
        if key in table:
            return table[key], MATCH_ALIAS, SOURCE_GENDER_TABLE
        return None

    raw = condition.raw.strip()
    hits = [(raw, hit)] if (hit := lookup(raw)) else []
    if not hits:
        hits = [(tok, h) for tok in _tokens(raw) if (h := lookup(tok))]
    warnings: list[ResolutionWarning] = []
    suggestion = _clean(condition.suggestion)
    if hits:
        values = _unique(
            ResolvedValue("gender", condition.polarity, condition.raw, value, match,
                          matched_text=text, looked_up_in=source)
            for text, (value, match, source) in hits
        )
        if suggestion is not None and suggestion not in {v.value for v in values}:
            warnings.append(ResolutionWarning(WARN_LLM_SUGGESTION_OVERRIDDEN, condition.raw))
        if len(values) > 1:
            warnings.append(ResolutionWarning(WARN_MULTIPLE_VALUES_IN_SPAN, condition.raw, str(len(values))))
        return Resolution(tuple(values), (), tuple(warnings))
    if suggestion in vocab.GENDERS:
        return Resolution((ResolvedValue("gender", condition.polarity, condition.raw, suggestion, MATCH_LLM),))
    if suggestion is not None:
        warnings.append(ResolutionWarning(WARN_SUGGESTION_NOT_IN_VOCABULARY, condition.raw, "gender"))
    return Resolution(
        (ResolvedValue("gender", condition.polarity, condition.raw, None, MATCH_NONE),), (), tuple(warnings),
    )


# ---------------------------------------------------------------------------- dictionary kinds
class _Resolver:
    def __init__(self, lexicon: Lexicon, condition: Condition) -> None:
        self.lx = lexicon
        self.c = condition
        self.suggestion = _clean(condition.suggestion)
        self.values: list[ResolvedValue] = []
        self.unsupported: list[UnsupportedItem] = []
        self.warnings: list[ResolutionWarning] = []

    # -- driver
    def run(self) -> Resolution:
        c = self.c
        outcomes = self._lookup_span(c.raw.strip())
        found = [o for o in outcomes if o is not _NULL]
        if found:
            self._apply(found)
        elif outcomes:  # only dictionary null values: the dictionary says "no standard value"
            if self.suggestion is not None:
                self._warn(WARN_LLM_SUGGESTION_OVERRIDDEN)
            if c.kind == "category":
                self._unsupported(UNSUPPORTED_CATEGORY, REASON_NULL_VALUE)
            else:
                self._value(c.kind, None, MATCH_NONE)
        else:
            self._fallback_to_suggestion(c.kind)
        if len(self.values) > 1:
            self._warn(WARN_MULTIPLE_VALUES_IN_SPAN, str(len(self.values)))
        return Resolution(tuple(self.values), tuple(self.unsupported), tuple(self.warnings))

    def _lookup_span(self, raw: str) -> list[_Outcome]:
        """Whole span first; if it gives nothing, its whitespace tokens (never the whole query)."""
        outcomes = self._lookup_text(raw)
        if outcomes:
            return outcomes
        tokens = _tokens(raw)
        if len(tokens) < 2:
            return []
        per_token = [o for tok in tokens for o in self._lookup_text(tok)]
        found = [o for o in per_token if o is not _NULL]
        return found or per_token  # null values only count when no token found anything

    def _lookup_text(self, text: str) -> list[_Outcome]:
        kind = self.c.kind
        if kind == "category":
            major = _major_name(text)
            if major is not None:  # the dictionary only returns a hint for these (never used, H5)
                return [_Hit("category", major, "standard", text, SOURCE_CATEGORY_MAJOR, major=major)]
        if kind == "color":
            return self._from_color(text)
        null_seen = False
        for attribute in _DICTIONARY_ATTRIBUTES[kind]:
            outcome = self._lookup_attribute(attribute, text, hops=0)
            if outcome is _NULL:
                null_seen = True
            elif outcome is not None:
                return [outcome]
        return [_NULL] if null_seen else []

    def _from_color(self, text: str) -> list[_Outcome]:
        outcomes: list[_Outcome] = []
        for n in self.lx.normalize_color(text):
            if n.value is not None and n.matched_by in _MATCH_BY_DICTIONARY:
                outcomes.append(_Hit("color", n.value, n.matched_by, text, "color"))
            elif n.matched_by == "null_value":
                outcomes.append(_NULL)
        return outcomes

    def _lookup_attribute(self, attribute: str, text: str, *, hops: int) -> Optional[_Outcome]:
        n: Normalized = self.lx.normalize(attribute, text)
        if n.value is not None and n.matched_by in _MATCH_BY_DICTIONARY:
            return self._hit(attribute, n.value, n.matched_by, text)
        if n.matched_by == "route" and n.route_to is not None:
            return self._follow_route(attribute, n, text, hops=hops)
        if n.matched_by == "null_value":
            return _NULL
        return None  # none, or a hint (hints are never applied, H5)

    def _hit(self, attribute: str, value: str, matched_by: str, text: str,
             routed_from: Optional[str] = None) -> _Hit:
        kind = _CONTRACT_KIND[attribute]
        if kind == "category":
            return _Hit(kind, value, matched_by, text, attribute, major=vocab.CATEGORY_MINOR_TO_MAJOR.get(value),
                        minor=value, routed_from=routed_from)
        return _Hit(kind, value, matched_by, text, attribute, routed_from=routed_from)

    def _follow_route(self, source: str, n: Normalized, text: str, *, hops: int) -> Optional[_Outcome]:
        target = n.route_to
        assert target is not None
        if target not in _CONTRACT_KIND:
            return _RoutedOut(target, text, source)
        if hops < _MAX_ROUTE_HOPS:
            sub = self._lookup_attribute(target, text, hops=hops + 1)
            if isinstance(sub, _Hit):
                return _Hit(sub.kind, sub.value, sub.matched_by, sub.text, sub.attribute, sub.major, sub.minor,
                            routed_from=sub.routed_from or source)
            if isinstance(sub, (_RoutedOut, _RoutedNoValue)) and n.value is None:
                return sub  # otherwise fall back to the value the route itself names (below)
        if n.value is not None:  # the route itself names the target value
            matched_by = "standard" if norm_key(n.value) == norm_key(text) else "alias"
            return self._hit(target, n.value, matched_by, text, routed_from=source)
        return _RoutedNoValue(_CONTRACT_KIND[target], text, source)

    # -- turning outcomes into output
    def _apply(self, outcomes: list[_Outcome]) -> None:
        c = self.c
        resolved: set[str] = set()
        routed_kind: Optional[str] = None
        for outcome in outcomes:
            if isinstance(outcome, _Hit):
                if outcome.routed_from is not None and outcome.kind != c.kind:
                    # routes inside the contract's detail (detail/neckline/collar) are not reported
                    self._warn(WARN_ATTRIBUTE_ROUTED, f"{outcome.routed_from}->{outcome.attribute}")
                if not _in_vocabulary(outcome.kind, outcome.value):
                    self._warn(WARN_DICTIONARY_VALUE_NOT_IN_VOCABULARY, outcome.kind)
                    if outcome.kind == "category":
                        self._unsupported(UNSUPPORTED_CATEGORY, REASON_NOT_IN_DICTIONARY)
                    else:
                        self._value(outcome.kind, None, MATCH_NONE)
                    continue
                self._value(outcome.kind, outcome.value, _MATCH_BY_DICTIONARY[outcome.matched_by],
                            major=outcome.major, minor=outcome.minor,
                            matched_text=outcome.text, looked_up_in=outcome.attribute)
                resolved.add(outcome.value)
            elif isinstance(outcome, _RoutedOut):
                self._warn(WARN_ATTRIBUTE_ROUTED, f"{outcome.routed_from}->{outcome.target}")
                self._unsupported(UNSUPPORTED_ATTRIBUTE, REASON_ROUTE_NOT_IN_CONTRACT, outcome.target)
            elif isinstance(outcome, _RoutedNoValue):
                if outcome.kind != c.kind:
                    self._warn(WARN_ATTRIBUTE_ROUTED, f"{outcome.routed_from}->{outcome.kind}")
                routed_kind = outcome.kind
        if routed_kind is not None and not resolved:
            # routed into a contract kind without a value: the suggestion may still name one there
            self._fallback_to_suggestion(routed_kind)
            return
        if self.suggestion is not None and self.suggestion not in resolved:
            self._warn(WARN_LLM_SUGGESTION_OVERRIDDEN)

    def _fallback_to_suggestion(self, kind: str) -> None:
        """The dictionary has nothing: use the LLM suggestion if it is in `kind`'s standard list."""
        s = self.suggestion
        if kind == "category":
            if s in vocab.CATEGORY_MINOR:
                self._value("category", s, MATCH_LLM, major=vocab.CATEGORY_MINOR_TO_MAJOR[s], minor=s)
                return
            if s in vocab.CATEGORY_MAJOR:
                # a major name in raw is matched before the dictionary (exact), so a major suggestion
                # here would widen a subtype to its major: rejected (decision G-2)
                self._warn(WARN_CATEGORY_MAJOR_SUGGESTION_REJECTED)
            elif s is not None:
                self._warn(WARN_SUGGESTION_NOT_IN_VOCABULARY, kind)
            self._unsupported(UNSUPPORTED_CATEGORY, REASON_NOT_IN_DICTIONARY)
            return
        if s is not None and _in_vocabulary(kind, s):
            self._value(kind, s, MATCH_LLM)
            return
        if s is not None:
            self._warn(WARN_SUGGESTION_NOT_IN_VOCABULARY, kind)
        self._value(kind, None, MATCH_NONE)

    def _value(self, kind: str, value: Optional[str], match: str, *, major: Optional[str] = None,
               minor: Optional[str] = None, matched_text: Optional[str] = None,
               looked_up_in: Optional[str] = None) -> None:
        if kind == "category" and value is not None and major is None:
            major = vocab.CATEGORY_MINOR_TO_MAJOR.get(value)
        item = ResolvedValue(kind, self.c.polarity, self.c.raw, value, match, major, minor, matched_text, looked_up_in)
        # one output per distinct value (first match wins when several tokens give the same value)
        if not any((v.kind, v.value, v.major, v.minor) == (kind, value, major, minor) for v in self.values):
            self.values.append(item)

    def _unsupported(self, type_: str, reason: str, detail: Optional[str] = None) -> None:
        item = UnsupportedItem(type_, self.c.raw, self.c.polarity, reason, detail)
        if item not in self.unsupported:
            self.unsupported.append(item)

    def _warn(self, code: str, detail: Optional[str] = None) -> None:
        warning = ResolutionWarning(code, self.c.raw, detail)
        if warning not in self.warnings:
            self.warnings.append(warning)


# ---------------------------------------------------------------------------- helpers
def _tokens(raw: str) -> list[str]:
    return raw.split()


def _clean(suggestion: Optional[str]) -> Optional[str]:
    if suggestion is None:
        return None
    suggestion = suggestion.strip()
    return suggestion or None


def _major_name(text: str) -> Optional[str]:
    key = norm_key(text)
    for major in vocab.CATEGORY_MAJOR:
        if norm_key(major) == key:
            return major
    return None


def _in_vocabulary(kind: str, value: str) -> bool:
    if kind == "category":
        return value in vocab.CATEGORY_MINOR or value in vocab.CATEGORY_MAJOR
    if kind == "gender":
        return value in vocab.GENDERS
    return value in vocab.ATTRIBUTE_VALUES.get(kind, ())


def _unique(values: Iterable[ResolvedValue]) -> list[ResolvedValue]:
    out: list[ResolvedValue] = []
    for v in values:
        if not any((o.kind, o.value) == (v.kind, v.value) for o in out):
            out.append(v)
    return out
