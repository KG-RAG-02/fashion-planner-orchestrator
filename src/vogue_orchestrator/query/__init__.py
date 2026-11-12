"""Query structuring: natural-language query -> StructuredQuery 1.0 (this repository's contract,
contracts/agents/vogue-orchestrator/output/structured-query.md), converted to TrendQuery for the trend-analysis-agent (first item and season
only, to_trend.py). The archived v1 path (structure_query) produces a TrendQuery directly.

Called in-process by the UI server only (server.structure_query_for_ui, structured.structure_query_v2);
not wired into the orchestrator pipeline: nothing is sent to an agent yet. Read docs/query-structuring.md first.
Needs pydantic (`pip install -e ".[query]"`); `vocabulary`, `season` and `errors` are stdlib-only,
so this package does not import pydantic until a name that needs it is requested.
"""

from __future__ import annotations

import importlib
from typing import Any

# public name -> submodule that defines it (resolved lazily, so a missing pydantic only fails on use)
_EXPORTS = {
    "structure_query": "structure",
    "QueryStructuringResult": "structure",
    "DraftProvider": "structure",
    "QueryStructuringError": "errors",
    "TrendQuery": "trend_query",
    "StructuredQuery": "models",
    "SCHEMA_VERSION": "models",
    # The draft the running path validates: v1 (legacy_v1) until plan U4 switches to draft v2 (draft.py).
    "QueryDraft": "legacy_v1",
    "assemble_trend_query": "assemble",
    "build_prompt": "prompt",
    "load_prompt": "prompt",
    "PromptBundle": "prompt",
    "PROMPT_VERSION": "prompt",
    "CONFIG": "config",
    "load_config": "config",
    "FixtureDraftProvider": "synthetic",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{module_name}"), name)
