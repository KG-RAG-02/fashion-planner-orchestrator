"""Test double for the LLM step. It is NOT a parser and understands no language.

`FixtureDraftProvider` returns a pre-written draft for an exact, known synthetic query. It lets
the rest of the chain (prompt -> draft validation -> assembly -> contract validation) run
end to end without an LLM. An unknown query raises, which structure_query reports as
PROVIDER_FAILED, exactly like a real provider outage.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .prompt import PromptBundle


class FixtureDraftProvider:
    def __init__(self, drafts_by_query: Mapping[str, Mapping[str, Any]]) -> None:
        self._drafts = {query: copy.deepcopy(dict(draft)) for query, draft in drafts_by_query.items()}

    def __call__(self, bundle: PromptBundle) -> Mapping[str, Any]:
        return copy.deepcopy(self._drafts[bundle.query])
