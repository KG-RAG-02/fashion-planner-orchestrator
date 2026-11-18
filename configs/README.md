# orchestrator runtime configuration

This folder holds non-secret settings for orchestrator query structuring, LLM calls, and local HTTP development. Secrets belong in the process environment or an ignored local `.env` file; do not store or commit secret values here.

## Query structuring

[`query_structuring.json`](query_structuring.json) names the prompt folder (`prompts/query_structuring/`) and defines input/year limits, unsupported-condition explanations, and gender aliases. `query/structured.py` validates the model draft and assembles the structured query; the Trend request uses the first requested item and season. Prompt files are under [`../prompts/query_structuring/`](../prompts/query_structuring/).

For a nonstandard checkout or packaged install, `ORCHESTRATOR_QUERY_CONFIG` and `ORCHESTRATOR_QUERY_PROMPTS_DIR` can point to alternate config and prompt roots. The module reads and validates these settings at import time.

[`llm.json`](llm.json) sets query-structuring timeout, output token limit, retry count, and temperature. The provider and model are configured with `LLM_PROVIDER`, `LLM_MODEL`, and `LLM_API_KEY`; the API key is never stored in these JSON files. A model name beginning with `claude` selects Anthropic when `LLM_PROVIDER` is unset; other model names default to OpenAI.

[`agent-http.env.example`](agent-http.env.example) is a loopback development example only; it is not loaded automatically. Compose has its own service URLs and private-host allowlist in `deploy/compose.yaml`.

## Workflow service settings

The sequential API reads these environment variables:

| Variable | Purpose |
|---|---|
| `ORCHESTRATOR_TREND_URL` | Trend Agent base URL |
| `ORCHESTRATOR_INVENTORY_URL` | Stock Agent base URL |
| `ORCHESTRATOR_NOVELTY_URL` | New Product Agent base URL |
| `ORCHESTRATOR_RESEARCH_URL` | Sales Market Agent base URL |
| `FASHION_PLANNER_SERVICE_TOKEN` | Shared outbound service token |
| `ORCHESTRATOR_HTTP_PRIVATE_HOSTS` | Exact private host allowlist for bearer-authenticated HTTP |
| `FASHION_PLANNER_HTTP_DEV_MODE` | Enables loopback HTTP during local development |
| `ORCHESTRATOR_WORKFLOW_CONNECT_TIMEOUT_SECONDS` | Connect timeout; default 3 seconds |
| `ORCHESTRATOR_WORKFLOW_READ_TIMEOUT_SECONDS` | General read timeout; default 75 seconds |
| `ORCHESTRATOR_WORKFLOW_INVENTORY_READ_TIMEOUT_SECONDS` | Stock-specific read timeout; otherwise uses the general read timeout |
| `ORCHESTRATOR_WORKFLOW_DRAFT_READ_TIMEOUT_SECONDS` | novelty Draft read timeout; Compose sets 600 seconds for visual generation |
| `ORCHESTRATOR_WORKFLOW_DRAFT_MODE` | `draft_with_visuals` or `draft_only` |
| `ORCHESTRATOR_WORKFLOW_STORE_DIR` | Private artifact and checkpoint store; default `.local/workflow` |
| `ORCHESTRATOR_PEER_ARTIFACT_ROOT` | Read-only root for verified novelty/research artifacts |
| `ORCHESTRATOR_WORKFLOW_IMAGES_ENABLED` | Enables local Planning Sheet composition |
| `ORCHESTRATOR_WORKFLOW_IMAGE_PROVIDER` | `planning_sheet` or deterministic `fixture`; neither calls an image model from orchestrator |
| `ORCHESTRATOR_TREND_PHOTOS_ENABLED` | `0` turns off the Trend article photos on the Planning Sheet; any other value or blank keeps them on |
| `ORCHESTRATOR_SWOT_ENABLED` | `0` turns off the LLM SWOT and promotion draft on the Planning Sheet; otherwise it is written when `LLM_MODEL` and `LLM_API_KEY` are configured (the same settings as query structuring) |
| `ORCHESTRATOR_TREND_PHOTO_HOSTS` | Comma list of hosts the Planning Sheet may download Trend photos from (each also admits its subdomains); blank means `vogue.co.kr` |

See [`../docs/workflow-integration-conventions.md`](../docs/workflow-integration-conventions.md) and [`../deploy/README.md`](../deploy/README.md) for security and deployment requirements.
