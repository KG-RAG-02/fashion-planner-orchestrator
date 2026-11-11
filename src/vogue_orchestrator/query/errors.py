"""Failure type of the query-structuring step. Standard library only.

Messages are fixed strings per code. They never contain the user's query, provider output,
or exception text, so they are safe to store or show (same rule as the orchestrator core's
"details suppressed" stage failures).
"""

from __future__ import annotations

INPUT_INVALID = "INPUT_INVALID"
PROVIDER_FAILED = "PROVIDER_FAILED"
DRAFT_NOT_JSON = "DRAFT_NOT_JSON"
DRAFT_INVALID = "DRAFT_INVALID"
TREND_QUERY_INVALID = "TREND_QUERY_INVALID"
STRUCTURED_QUERY_INVALID = "STRUCTURED_QUERY_INVALID"  # v2 path (structured.py)

_MESSAGES = {
    STRUCTURED_QUERY_INVALID: "Assembled StructuredQuery failed contract validation.",
    INPUT_INVALID: "Query structuring input is invalid.",
    PROVIDER_FAILED: "Draft provider failed; details suppressed.",
    DRAFT_NOT_JSON: "Draft provider did not return a JSON object.",
    DRAFT_INVALID: "Draft does not match the draft schema.",
    TREND_QUERY_INVALID: "Assembled TrendQuery failed contract validation.",
}


class QueryStructuringError(Exception):
    """Structuring failed. `code` is one of the module constants; `message` is a fixed string."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = _MESSAGES[code]
        super().__init__(self.message)
