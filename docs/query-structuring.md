# Query structuring

This module turns user text into a validated `StructuredQuery`, then converts the first item and first season into the `TrendQuery` sent to Trend Agent.

```text
user_input.text
  → server.structure_query_for_ui
  → query.structured.structure_query (prompt v6)
  → query.to_trend.to_trend_query
  → workflow.dispatch
  → Trend Agent /runs
```

If structuring fails or yields no searchable item, the workflow returns an input error before calling an agent. Conditions unsupported by TrendQuery are retained as warnings/unsupported details rather than silently treated as filters. Other workflow stages receive the PlanningRequest and prior producer results defined by `contracts/workflow/`.

## Code map

| Path | Responsibility |
|---|---|
| `src/fashion_planner_orchestrator/query/structured.py` | Parse and assemble `StructuredQuery` using the v6 prompt |
| `src/fashion_planner_orchestrator/query/models.py` | Pydantic model and schema for `StructuredQuery` 1.0 |
| `src/fashion_planner_orchestrator/query/resolve.py`, `lexicon.py` | Resolve requested terms using the generated vocabulary and alias snapshot |
| `src/fashion_planner_orchestrator/query/to_trend.py` | Convert only the first item and season into `TrendQuery` |
| `prompts/query_structuring/v6/` | Active system/user prompt and examples |
| `configs/query_structuring.json` | Query length, aliases and fixed unsupported-condition reasons |
| `data/` | Synthetic labeled and holdout sets plus historical evaluation records |
| `scripts/evaluate_query_structuring.py` | Reproduce query-structuring evaluations |

Prompt versions v2–v5 and their evaluation files remain for reproducible historical measurements. They do not select a server execution path. The UI and `/v1/orchestrations` use the same workflow input and structuring function.

Install the `query` optional dependency with `pip install -e '.[query]'`. For offline exact-match provider fixtures, set `VOGUE_QUERY_STRUCTURING_MODE=synthetic_fixture`; normal requests use the configured provider in `llm_provider.py`.

The workflow may return `partial` or `no_results` from downstream producers while preserving the query and stage trace. Query structuring does not access networks, data sources, or agent services by itself.
