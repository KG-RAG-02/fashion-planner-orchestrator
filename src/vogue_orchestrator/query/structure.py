"""Entry point of query structuring: natural-language query -> contract-valid TrendQuery.

    query --build_prompt--> bundle --DraftProvider (LLM adapter, NOT part of this step)--> draft JSON
          --QueryDraft--> --assemble_trend_query--> TrendQuery (+ warning codes)

The step is standalone: it is not wired into the orchestrator pipeline, imports nothing from the
orchestrator core, makes no network call and holds no key. The LLM is reached only through the
injected `draft_provider`; tests use a fixture-backed double (synthetic.py).

Failures raise QueryStructuringError with a fixed message; the query text, provider output and
exception text are never echoed (`from None` also drops the chained traceback text).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping, Union

from pydantic import ValidationError

from . import errors
from .assemble import assemble_trend_query
from .config import CONFIG
from .draft import QueryDraft
from .errors import QueryStructuringError
from .models import TrendQuery
from .prompt import PromptBundle, build_prompt

# configs/query_structuring.json input.max_query_chars (1000, same bound as the demo UI server)
MAX_QUERY_CHARS = CONFIG.max_query_chars

DraftProvider = Callable[[PromptBundle], Union[Mapping[str, Any], str]]


@dataclass(frozen=True)
class QueryStructuringResult:
    trend_query: TrendQuery
    draft: QueryDraft
    warnings: tuple[str, ...]
    prompt_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_version": self.prompt_version,
            "trend_query": self.trend_query.model_dump(mode="json"),
            "draft": self.draft.model_dump(mode="json"),
            "warnings": list(self.warnings),
        }


def structure_query(
    query: str,
    *,
    request_id: str,
    draft_provider: DraftProvider,
    today: date | None = None,
) -> QueryStructuringResult:
    """Structure one query. `request_id` must be the ID the orchestrator issued for the request."""
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise QueryStructuringError(errors.INPUT_INVALID)
    if not isinstance(request_id, str) or not request_id.strip():
        raise QueryStructuringError(errors.INPUT_INVALID)
    if today is not None and not isinstance(today, date):
        raise QueryStructuringError(errors.INPUT_INVALID)
    today = today or date.today()

    bundle = build_prompt(query)
    try:
        produced = draft_provider(bundle)
    except Exception:
        raise QueryStructuringError(errors.PROVIDER_FAILED) from None

    payload = _as_object(produced)
    try:
        draft = QueryDraft.model_validate(payload)
    except ValidationError:
        raise QueryStructuringError(errors.DRAFT_INVALID) from None

    try:
        assembled = assemble_trend_query(
            draft, request_id=request_id, original_query=query, today=today,
        )
    except Exception:
        raise QueryStructuringError(errors.TREND_QUERY_INVALID) from None

    return QueryStructuringResult(
        trend_query=assembled.trend_query,
        draft=draft,
        warnings=assembled.warnings,
        prompt_version=bundle.version,
    )


def _as_object(produced: Any) -> Mapping[str, Any]:
    """Accept a dict or the JSON text a model returned (tolerates a ```json fence)."""
    if isinstance(produced, Mapping):
        return produced
    if isinstance(produced, str):
        text = produced.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:]  # opening fence, optionally with a language tag
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            raise QueryStructuringError(errors.DRAFT_NOT_JSON) from None
        if isinstance(value, Mapping):
            return value
    raise QueryStructuringError(errors.DRAFT_NOT_JSON)
