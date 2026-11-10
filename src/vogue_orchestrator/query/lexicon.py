"""Alias-dictionary normalization: a stdlib-only port of trend-analysis-agent's AliasMap.

Source of truth (read-only): trend-analysis-agent `src/trend_agent/ingestion/normalization/alias_map.py`
and `configs/alias_map/*.yaml`. The dictionary data comes from the generated module `alias_data.py`
(snapshot docs/contracts/alias-map.snapshot.json). The port must behave exactly like the original;
tests/test_query_lexicon.py compares it with outputs of the original code (alias-map.golden.json).
Original behaviour that looks questionable is ported as is (decision H1: follow the dictionary).

Conversion order of `normalize` (same as the original):
  normalization key -> standard value -> alias -> approx -> route -> hint -> null value -> none

This module only reports what the dictionary says. Policy lives in the caller:
- approx results must not become filters (decision H2),
- hints are reported but never applied (decision H5).

Standard library only; no pyyaml, no file access at run time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from . import alias_data

__all__ = [
    "ATTRIBUTES",
    "Lexicon",
    "Normalized",
    "default_lexicon",
    "norm_key",
    "normalize",
    "normalize_color",
    "normalize_tokens",
    "split_tokens",
]

ATTRIBUTES: tuple[str, ...] = alias_data.ATTRIBUTES

# matched_by values, as in the original
MATCHED_BY = ("standard", "alias", "approx", "route", "hint", "null_value", "none")

_NORM_RE = re.compile(r"[\s\-_]")
_SPLIT_RE = re.compile(r"[,/+&:]")
_BLEND_RE = re.compile(r"블렌드|혼방")
_NUMBER_RE = re.compile(r"[\d.]+")
_N_COLOR_RE = re.compile(r"\d+color")
_PLACEHOLDER = "\x00"
_MELANGE_KEYS = ("멜란지", "melange")
_MELANGE_VALUE = "그레이"  # trend V.Color.GREY.value


def norm_key(text: Any) -> str:
    """Lower-case and drop whitespace, `-` and `_` (same as the original `norm_key`)."""
    return _NORM_RE.sub("", str(text)).lower()


@dataclass(frozen=True)
class Normalized:
    """Result of one normalization. Mirrors trend `Normalized`.

    `hint` is stored as sorted (key, value) pairs so the object stays hashable; `hint_dict()` returns
    the original dict form. `matched_by` is one of MATCHED_BY.
    """

    raw: str
    value: Optional[str] = None
    approx: bool = False
    route_to: Optional[str] = None
    hint: tuple[tuple[str, Any], ...] = ()
    matched_by: str = "none"

    def hint_dict(self) -> dict[str, Any]:
        return dict(self.hint)

    def as_dict(self) -> dict[str, Any]:
        """Same keys and values as `dataclasses.asdict` of the original `Normalized`."""
        return {
            "raw": self.raw,
            "value": self.value,
            "approx": self.approx,
            "route_to": self.route_to,
            "hint": self.hint_dict(),
            "matched_by": self.matched_by,
        }


class Lexicon:
    """Index of the alias dictionary, built from `alias_data` (the snapshot)."""

    def __init__(self) -> None:
        self._index: dict[str, dict[str, tuple[str, str]]] = {}  # attr -> key -> (value, kind)
        self._routes: dict[str, dict[str, Mapping[str, Any]]] = {}
        self._hints: dict[str, dict[str, Mapping[str, Any]]] = {}
        self._nulls: dict[str, frozenset[str]] = {}
        for attr in ATTRIBUTES:
            idx: dict[str, tuple[str, str]] = {}
            for value in alias_data.STANDARD_VALUES[attr]:
                idx[norm_key(value)] = (value, "standard")
            for value, raws in alias_data.ALIASES[attr].items():
                for raw in raws:
                    idx.setdefault(norm_key(raw), (value, "alias"))
            for value, raws in alias_data.APPROX[attr].items():
                for raw in raws:
                    idx.setdefault(norm_key(raw), (value, "approx"))
            self._index[attr] = idx
            # the original rebuilds these per call with a dict comprehension: the last spelling wins
            self._routes[attr] = {norm_key(k): v for k, v in alias_data.ROUTES[attr].items()}
            self._hints[attr] = {norm_key(k): v for k, v in alias_data.HINTS[attr].items()}
            self._nulls[attr] = frozenset(norm_key(x) for x in alias_data.NULL_VALUES[attr])
        self._color_modifiers = tuple(norm_key(m) for m in alias_data.COLOR_MODIFIERS)
        self._color_base_keys = tuple(sorted(self._index["color"], key=len, reverse=True))
        self._compound_re = re.compile(
            "|".join(re.escape(c) for c in alias_data.COMPOUND_VALUES), re.IGNORECASE,
        )

    # ---------- lookup helpers (for the candidate scanner) ----------
    def index(self, attribute: str) -> Mapping[str, tuple[str, str]]:
        """Normalization key -> (value, kind) with kind standard | alias | approx. Empty for unknown attributes."""
        return dict(self._index.get(attribute, {}))

    # ---------- single value ----------
    def normalize(self, attribute: str, raw: Any) -> Normalized:
        text = "" if raw is None else str(raw).strip()
        if not text:
            return Normalized(raw=text)
        key = norm_key(text)
        hit = self._index.get(attribute, {}).get(key)
        if hit:
            value, kind = hit
            return Normalized(raw=text, value=value, approx=kind == "approx", matched_by=kind)
        route = self._routes.get(attribute, {}).get(key)
        if route:
            return Normalized(raw=text, value=route.get("value"), route_to=route["to"], matched_by="route")
        hint = self._hints.get(attribute, {}).get(key)
        if hint:
            return Normalized(raw=text, hint=tuple(sorted(dict(hint).items())), matched_by="hint")
        if key in self._nulls.get(attribute, frozenset()):
            return Normalized(raw=text, matched_by="null_value")
        return Normalized(raw=text)

    # ---------- multiple values ----------
    def split_tokens(self, raw: Any) -> list[str]:
        """Split on `,` `/` `+` `&` `:`; the compound standard values with a slash stay whole.
        Lists and tuples are split element by element and concatenated."""
        if isinstance(raw, (list, tuple)):
            out: list[str] = []
            for item in raw:
                out.extend(self.split_tokens(item))
            return out
        text = "" if raw is None else str(raw).strip()
        if not text:
            return []
        protected = self._compound_re.sub(lambda m: m.group(0).replace("/", _PLACEHOLDER), text)
        return [t.strip().replace(_PLACEHOLDER, "/") for t in _SPLIT_RE.split(protected) if t.strip()]

    def normalize_tokens(self, attribute: str, raw: Any) -> list[Normalized]:
        if attribute == "color":
            return self.normalize_color(raw)
        out = []
        for tok in self.split_tokens(raw):
            n = self.normalize(attribute, tok)
            if n.matched_by == "none":  # retry without a "블렌드"/"혼방" tag (e.g. 울 블렌드)
                stripped = _BLEND_RE.sub("", tok).strip()
                if stripped and stripped != tok:
                    n2 = self.normalize(attribute, stripped)
                    if n2.matched_by != "none":
                        n = Normalized(
                            raw=tok, value=n2.value, approx=n2.approx, route_to=n2.route_to,
                            hint=n2.hint, matched_by=n2.matched_by,
                        )
            out.append(n)
        return out

    def normalize_color(self, raw: Any) -> list[Normalized]:
        """Colors: split -> drop modifiers -> base color at the end (approx)."""
        color_index = self._index["color"]
        results = []
        for tok in self.split_tokens(raw):
            key = norm_key(tok)
            if _NUMBER_RE.fullmatch(key) or _N_COLOR_RE.fullmatch(key):
                results.append(Normalized(raw=tok, matched_by="null_value"))
                continue
            n = self.normalize("color", tok)
            if n.value is None and n.matched_by == "none":
                stripped = key
                for modifier in self._color_modifiers:
                    stripped = stripped.replace(modifier, "")
                if stripped and stripped != key and stripped in color_index:
                    value, _ = color_index[stripped]
                    n = Normalized(raw=tok, value=value, matched_by="alias")
                elif key in _MELANGE_KEYS:
                    n = Normalized(raw=tok, value=_MELANGE_VALUE, matched_by="alias")
                else:
                    # base color at the end ("차콜 그레이", "sage green"): marked approx
                    for base in self._color_base_keys:
                        if len(base) >= 2 and stripped.endswith(base):
                            value, _ = color_index[base]
                            n = Normalized(raw=tok, value=value, approx=True, matched_by="approx")
                            break
            results.append(n)
        return results


_DEFAULT: Optional[Lexicon] = None


def default_lexicon() -> Lexicon:
    """Shared instance built from the bundled snapshot (built on first use)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Lexicon()
    return _DEFAULT


def normalize(attribute: str, raw: Any) -> Normalized:
    return default_lexicon().normalize(attribute, raw)


def split_tokens(raw: Any) -> list[str]:
    return default_lexicon().split_tokens(raw)


def normalize_tokens(attribute: str, raw: Any) -> list[Normalized]:
    return default_lexicon().normalize_tokens(attribute, raw)


def normalize_color(raw: Any) -> list[Normalized]:
    return default_lexicon().normalize_color(raw)
