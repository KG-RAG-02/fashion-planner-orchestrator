"""Natural-language query flow: draft generation and validation, then deterministic assembly.

The resolver-to-StructuredQuery mapping lives in :mod:`query.assembly`; this module owns the
provider call, prompt selection, draft validation and warning aggregation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError

from . import errors, draft_v3, draft_v4
from .assembly import (
    ITEM_WORD_ATTRIBUTE_PRIORITY, StructuredAssembly, WARN_CATEGORY_CONDITION_MOVED_TO_ITEM,
    WARN_ITEM_WORD_IS_CONDITION, WARN_LEADING_GENDER_SHARED, WARN_ONLY_OUT_OF_SCOPE_ITEMS,
    WARN_STRAY_CATEGORY_VALUE, WARN_UNSUPPORTED_CATEGORY_RESOLVED, assemble_structured_query,
)
from .config import QueryConfigError
from .draft_v3 import QueryDraftV3, repair_draft_v3_labels
from .draft_v4 import QueryDraftV4, group_draft, repair_draft_v4_labels
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


def structure_query(
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
            payload, repairs = repair_draft_v4_labels(as_object(produced))  # OQ-12′
            written = QueryDraftV4.model_validate(payload)
            grouped = group_draft(written, query)  # FL-1: flat conditions -> per item
            draft, grouping = grouped.draft, grouped.warnings
        else:
            payload, repairs = repair_draft_v3_labels(as_object(produced))  # OQ-12′
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
