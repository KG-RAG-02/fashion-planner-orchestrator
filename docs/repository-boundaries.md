# Project_08 repository layout and service boundary

## Folder layout

Project_08 is the common layout reference: `src/`, `tests/`, `docs/`, `data/`, `examples/`, `prompts/`, `scripts/`, and top-level `contracts/`, plus application-specific `configs/` and `web/`. Project_06 and Project_07 now use the same shared roots. Each repository keeps its own contract-owned schemas or adapters; copies are explicitly marked as snapshots rather than imported sibling code.

## API boundary

Project_08 owns orchestration and the client-facing preview API. `POST /v1/orchestrations` starts a pipeline run and returns a trace. It is distinct from the per-agent contract `POST /v1/agents/{route_agent_id}/runs`, which Project_08 calls for each producer. The v0.1 HTTP mode retains each complete response envelope in the trace and forwards the producer result to the next agent without rewriting it.

The v0.1 client requires Trend and Stock base URLs; the post-draft Sales Market review uses `VOGUE_SALES_MARKET_AGENT_URL`. PlanningDraft generation runs in P08 from the P06 Git snapshot documented in [`p06-planning-generator-migration.md`](p06-planning-generator-migration.md). Project_06 remains responsible for product variants. For local development, enable `VOGUE_AGENT_HTTP_DEV_MODE=1` and use loopback URLs. The orchestrator has no automatic retries.

## Independent local operation

Project_08's preview server runs on loopback port `8765` by default and can run in synthetic demo mode without sibling services. The agent HTTP mode requires configured Trend, Stock, and Sales Market services. Project_06 defaults to port `8767` for its remaining product variant routes and Project_07 to `8766`. Health checks establish availability only; they do not prove schema compatibility or source authorization.
