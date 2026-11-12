"""Utilities for bounded, explicitly untrusted model input data."""

from __future__ import annotations

import json


PROMPT_DATA_MAX_CHARS = 24_000
PROMPT_DATA_FIELD_MAX_CHARS = 2_000


def serialize_untrusted_prompt_data(value: object) -> str:
    """Serialize bounded external values as JSON, never prompt structure.

    JSON escaping prevents embedded newlines, quotes, or control characters
    from creating new prompt sections. This is defense in depth and does not
    guarantee that a generative model cannot be influenced by the content.
    """
    truncated = False

    def bound(item: object, depth: int = 0) -> object:
        nonlocal truncated
        if depth > 8:
            truncated = True
            return "[nested data truncated]"
        if isinstance(item, str):
            if len(item) > PROMPT_DATA_FIELD_MAX_CHARS:
                truncated = True
                return item[:PROMPT_DATA_FIELD_MAX_CHARS] + "…[truncated]"
            return item
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, dict):
            result = {}
            for index, (key, child) in enumerate(item.items()):
                if index >= 100:
                    truncated = True
                    break
                safe_key = str(key)
                if len(safe_key) > 200:
                    safe_key = safe_key[:200] + "…"
                    truncated = True
                result[safe_key] = bound(child, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            values = list(item)
            if len(values) > 100:
                truncated = True
                values = values[:100]
            return [bound(child, depth + 1) for child in values]
        return bound(str(item), depth + 1)

    bounded = bound(value)
    encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > PROMPT_DATA_MAX_CHARS:
        return json.dumps(
            {"truncated": True, "data_preview": encoded[: PROMPT_DATA_MAX_CHARS // 4]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    if truncated:
        # Keep the truncation marker in the JSON structure, not in source prose.
        encoded = json.dumps(
            {"truncated": True, "data": bounded},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(encoded) > PROMPT_DATA_MAX_CHARS:
            return json.dumps(
                {"truncated": True, "data_preview": encoded[: PROMPT_DATA_MAX_CHARS // 4]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
    return encoded
