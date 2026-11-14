# P08 runtime configuration

This folder holds non-secret settings for P08 query structuring, LLM calls, and local HTTP development. Secrets belong in the process environment or an ignored local `.env` file; do not store or commit secret values here.

## Query structuring

[`query_structuring.json`](query_structuring.json) selects the current prompt (`query_structuring/v6`) and defines input/year limits, unsupported-condition explanations, and gender aliases. `query/structured.py` validates the model draft and assembles the structured query; the Trend request uses the first requested item and season. Prompt files are under [`../prompts/query_structuring/`](../prompts/query_structuring/).

For a nonstandard checkout or packaged install, `VOGUE_QUERY_CONFIG` and `VOGUE_QUERY_PROMPTS_DIR` can point to alternate config and prompt roots. The module reads and validates these settings at import time.

[`llm.json`](llm.json) sets query-structuring timeout, output token limit, retry count, and temperature. The provider and model are configured with `LLM_PROVIDER`, `LLM_MODEL`, and `LLM_API_KEY`; the API key is never stored in these JSON files. A model name beginning with `claude` selects Anthropic when `LLM_PROVIDER` is unset; other model names default to OpenAI.

[`agent-http.env.example`](agent-http.env.example) is a loopback development example only; it is not loaded automatically. Compose has its own service URLs and private-host allowlist in `deploy/compose.yaml`.

## Workflow service settings

The sequential API reads these environment variables:

| Variable | Purpose |
|---|---|
| `VOGUE_TREND_AGENT_URL` | Trend Agent base URL |
| `VOGUE_STOCK_AGENT_URL` | Stock Agent base URL |
| `VOGUE_NEW_PRODUCT_AGENT_URL` | New Product Agent base URL |
| `VOGUE_SALES_MARKET_AGENT_URL` | Sales Market Agent base URL |
| `VOGUE_AGENT_SERVICE_BEARER_TOKEN` | Shared outbound service token |
| `VOGUE_AGENT_HTTP_PRIVATE_HOSTS` | Exact private host allowlist for bearer-authenticated HTTP |
| `VOGUE_AGENT_HTTP_DEV_MODE` | Enables loopback HTTP during local development |
| `VOGUE_WORKFLOW_CONNECT_TIMEOUT_SECONDS` | Connect timeout; default 3 seconds |
| `VOGUE_WORKFLOW_READ_TIMEOUT_SECONDS` | General read timeout; default 75 seconds |
| `VOGUE_WORKFLOW_STOCK_READ_TIMEOUT_SECONDS` | Stock-specific read timeout; otherwise uses the general read timeout |
| `VOGUE_WORKFLOW_DRAFT_READ_TIMEOUT_SECONDS` | P06 Draft read timeout; Compose sets 600 seconds for visual generation |
| `VOGUE_WORKFLOW_DRAFT_MODE` | `draft_with_visuals` or `draft_only` |
| `VOGUE_WORKFLOW_STORE_DIR` | Private artifact and checkpoint store; default `.local/workflow` |
| `VOGUE_PEER_ARTIFACT_ROOT` | Read-only root for verified P06/P07 artifacts |
| `VOGUE_WORKFLOW_IMAGES_ENABLED` | Enables local Planning Sheet composition |
| `VOGUE_WORKFLOW_IMAGE_PROVIDER` | `planning_sheet` or deterministic `fixture`; neither calls an image model from P08 |

See [`../docs/workflow-integration-conventions.md`](../docs/workflow-integration-conventions.md) and [`../deploy/README.md`](../deploy/README.md) for security and deployment requirements.
