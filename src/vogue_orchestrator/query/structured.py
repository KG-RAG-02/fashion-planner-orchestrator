"""Natural-language query flow: draft generation and validation, then deterministic assembly.

The resolver-to-StructuredQuery mapping lives in :mod:`query.assembly`; this module owns the
provider call, prompt selection, draft validation and warning aggregation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError

from . import errors
from .assembly import (
    ITEM_WORD_ATTRIBUTE_PRIORITY, StructuredAssembly, WARN_CATEGORY_CONDITION_MOVED_TO_ITEM,
    WARN_ITEM_WORD_IS_CONDITION, WARN_LEADING_GENDER_SHARED, WARN_ONLY_OUT_OF_SCOPE_ITEMS,
    WARN_STRAY_CATEGORY_VALUE, WARN_UNSUPPORTED_CATEGORY_RESOLVED, assemble_structured_query,
)
from .draft import QueryDraft, group_draft, repair_draft_labels
from .errors import QueryStructuringError
from .models import StructuredQuery
from .prompt import build_prompt
from .shared import MAX_QUERY_CHARS, DraftProvider, as_object

STRUCTURED_PROMPT_VERSION = "v6"  # active prompt in configs/query_structuring.json
__all__ = [
    "ITEM_WORD_ATTRIBUTE_PRIORITY",
    "STRUCTURED_PROMPT_VERSION",
    "StructuredAssembly",
    "StructuredQueryResult",
    "WARN_CATEGORY_CONDITION_MOVED_TO_ITEM",
    "WARN_ITEM_WORD_IS_CONDITION",
    "WARN_LEADING_GENDER_SHARED",
    "WARN_ONLY_OUT_OF_SCOPE_ITEMS",
    "WARN_STRAY_CATEGORY_VALUE",
    "WARN_UNSUPPORTED_CATEGORY_RESOLVED",
    "assemble_structured_query",
    "structure_query",
]


@dataclass(frozen=True)
class StructuredQueryResult:
    structured_query: StructuredQuery
    draft: QueryDraft  # what the model wrote, before grouping
    warnings: tuple[str, ...]
    prompt_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_version": self.prompt_version,
            "structured_query": self.structured_query.model_dump(mode="json"),
            "draft": self.draft.model_dump(mode="json"),
            "warnings": list(self.warnings),
        }


def structure_query(
    query: str,
    *,
    request_id: str,
    draft_provider: DraftProvider,
    today: date | None = None,
    prompt_version: str = STRUCTURED_PROMPT_VERSION,
) -> StructuredQueryResult:
    """Structure one query: validate the input, call the draft provider with the current prompt, repair and
    validate the draft, group its conditions per item and assemble the StructuredQuery. Fixed-message errors."""
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise QueryStructuringError(errors.INPUT_INVALID)
    if not isinstance(request_id, str) or not request_id.strip():
        raise QueryStructuringError(errors.INPUT_INVALID)
    if today is not None and not isinstance(today, date):
        raise QueryStructuringError(errors.INPUT_INVALID)
    today = today or date.today()

    bundle = build_prompt(query, prompt_version)
    try:
        produced = draft_provider(bundle)
    except Exception:
        raise QueryStructuringError(errors.PROVIDER_FAILED) from None

    try:
        payload, repairs = repair_draft_labels(as_object(produced))
        written = QueryDraft.model_validate(payload)
        grouped = group_draft(written, query)  # flat conditions -> per item
        draft, grouping = grouped.draft, grouped.warnings
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
