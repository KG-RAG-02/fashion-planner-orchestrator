"""Small shared helpers for the current structured-query pipeline."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from .config import CONFIG
from .errors import QueryStructuringError
from . import errors

MAX_QUERY_CHARS = CONFIG.max_query_chars
DraftProvider = Callable[[Any], Mapping[str, Any] | str]


def as_object(produced: Any) -> Mapping[str, Any]:
    """Parse a provider object or JSON response without accepting scalar/array values."""
    if isinstance(produced, Mapping):
        return produced
    if isinstance(produced, str):
        text = produced.strip()
        if text.startswith("```"):
            lines = text.splitlines()[1:]
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
