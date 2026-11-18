"""Closed value sets of the StructuredQuery 1.0 contract that go beyond trend's TrendQuery snapshot.

vocabulary.py is generated from the TrendQuery snapshot and stays as it is (the v1 path and
TrendQuery use it). StructuredQuery adds:
  - two length kinds (decision OQ-9′, 2026-11-10): `body_length` and `sleeve_length`. Their standard
    values are trend's own (`BodyLength`, `SleeveLength`, trend docs/schemas/
    vocabulary.md §6) and are taken from the alias dictionary snapshot (alias_data.py), so no new
    standard value is created here (D-29). tests/test_query_structured_models.py pins them.
  - the closed list of `unsupported.type` (decision OQ-3′), shared by the LLM draft (draft.py) and
    the contract (models.py).
"""

from __future__ import annotations

from . import vocabulary as vocab
from .alias_data import STANDARD_VALUES as _DICTIONARY_STANDARD_VALUES

LENGTH_TYPES: tuple[str, ...] = ("body_length", "sleeve_length")

# attribute kinds of StructuredQuery, in contract order: the five TrendQuery kinds, then the lengths
ATTRIBUTE_TYPES: tuple[str, ...] = (*vocab.ATTRIBUTE_TYPES, *LENGTH_TYPES)

ATTRIBUTE_VALUES: dict[str, tuple[str, ...]] = {
    **vocab.ATTRIBUTE_VALUES,
    **{kind: tuple(_DICTIONARY_STANDARD_VALUES[kind]) for kind in LENGTH_TYPES},
}

# Conditions the contract can not express as a standard value. The first four have fixed reason texts in
# configs/query_structuring.json (unsupported.reasons); the rest get unsupported.generic_reason.
# Gender never goes here (decision G-3: gender has its own include/exclude lists).
UNSUPPORTED_TYPES: tuple[str, ...] = (
    "brand", "category", "attribute", "abstract", "price", "market", "channel", "age", "other",
)
