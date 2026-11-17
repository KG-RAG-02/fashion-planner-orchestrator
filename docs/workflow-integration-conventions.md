# VOGUE workflow integration conventions

Current integration behavior and contract boundaries for the five repositories. Runtime and schema facts are owned by each repository; verify its source fingerprint when a checkout changes. If this guide conflicts with implementation, treat that as a documentation or integration gap and resolve it before claiming compatibility.

## 1. Current proposed version matrix

| Boundary | Endpoint | Transport (`contract_version`) | Producer schema |
|---|---|---|---|
| P08 → Trend | `POST /v1/agents/trend-analysis-agent/runs` | `vogue-handoff/0.1` | Trend `1.1` |
| P08 → Stock | `POST /v1/agents/stock-agent/runs` (input = Trend `result` body, unchanged) | `vogue-handoff/0.1` | Stock `1.0` |
| P08 → P06 | `POST /v1/agents/new-product-agent/runs` | `vogue-handoff/0.3` | P06 result schema `1.0` |
| P08 → P07 | `POST /v1/agents/sales-market-agent/reviews` | `vogue-sales-market-review/0.3-proposal` | `sales-market-review/0.1-internal` |
| UI → P08 | `POST /api/workflow/run`, `POST /v1/orchestrations` (`contract_version: vogue-workflow/0.2`) | `vogue-workflow/0.2` | `integrated-result/2.0` |

- **Contract package:** proposed `workflow-contract/0.5.0`, in `vogue-orchestrator/contracts/workflow/`.
- P06 `/runs` receives exactly `planning_request`, `trend_result`, and `inventory_product_result`. Afterward, P08 builds the P07 request from the Draft's `product_profile`: only values P06 marked `user_requested` or `evidence_observation` are sent (`category`, `product_type`, `gender`, `season`, `material`), each with its provenance in `product_evidence` (`kind`, `source_ref` = `p06.draft.product_profile.<field>`, `evidence_ids`). User-requested values remain distinct and do not acquire research evidence IDs. P07 does not receive the full Draft or attest to its response hash; P08 records the exact P07 request artifact and documents that lineage limit in the integrated result.
  - The schema bytes are pinned by SHA-256 (`snapshot.json`).
  - Byte-identical copies live in `new-product-agent/contracts/workflow-proposed/` and `sales-market-agent/src/market_research_agent/resources/workflow_contract/`.
  - **Never edit pinned schema bytes or the vendored copies.**
- **Unsupported versions** are rejected before any model, DB or network work:
  - new `vogue-workflow` endpoints: 422 `UNSUPPORTED_CONTRACT_VERSION`
  - malformed requests to workflow endpoints: the documented 400 response
- **Trend 1.1** may carry `trends[].style.example_images` and `trends[].style.search_outlook`. Consumers preserve these fields; the UI links example images, and the planning poster (stage 06) downloads up to three of them from allowlisted hosts and embeds them (see Trend photos for the Planning Sheet below). `search_outlook` is a target-season search-interest outlook, not a sales forecast, and is not used for ranking, status or lineage. Stub values are synthetic and remain labelled as such.

## 2. Containers: hostnames, ports, binds, health

| Service | Compose host | Port | Container mode | Liveness |
|---|---|---|---|---|
| orchestrator | `orchestrator` | 8765 | bind `0.0.0.0`; `VOGUE_ORCHESTRATOR_ALLOW_REMOTE_BIND=1`; `VOGUE_ORCHESTRATOR_RUNTIME=container` | `GET /api/health` |
| trend | `trend` | 8768 | `--mode service --host 0.0.0.0` | `GET /health` |
| stock | `stock` | 8002 | `python -m stock_agent.server --host 0.0.0.0` | `GET /health/live`; readiness `GET /health/ready` |
| new-product | `new-product` | 8767 | `VOGUE_HTTP_MODE=private` (new; plain HTTP, any bind, bearer on every route) | `GET /api/health` with bearer |
| sales-market | `sales-market` | 8766 | `--service-mode --host 0.0.0.0` | `GET /api/health` |

- **Every app container:**
  - uid/gid 10001, read-only root filesystem, `HOME=/tmp`
  - writable paths only on declared volumes or tmpfs
  - liveness healthcheck with no external calls
- **Host-mode** (developer laptop) defaults stay loopback-only.

## 3. Authentication

- One shared bearer token: `VOGUE_AGENT_SERVICE_BEARER_TOKEN`.
  - The deployment generates a fresh value; never copy old `.env` values into code, logs or commits.
  - Compare with `hmac.compare_digest`. Check auth before reading the body.
  - 401 on a missing or wrong token. Never echo the token.
- Agent POST routes always require the bearer.

## 4. Outbound HTTP from P08 (S1)

- **Private-host allowlist.** `VOGUE_AGENT_HTTP_PRIVATE_HOSTS` is a comma list of exact single-label hostnames (`^[a-z][a-z0-9-]{0,62}$`).
  - Plain `http://` is allowed only to those hosts, and only with a non-empty bearer token.
  - Everything else keeps today's policy: HTTPS with CA verification, or plain HTTP to loopback in dev mode.
  - The allowlist is independent of `VOGUE_AGENT_HTTP_DEV_MODE`.
- **Base URLs in compose:**
  - `VOGUE_TREND_AGENT_URL=http://trend:8768`
  - `VOGUE_STOCK_AGENT_URL=http://stock:8002`
  - `VOGUE_NEW_PRODUCT_AGENT_URL=http://new-product:8767`
  - `VOGUE_SALES_MARKET_AGENT_URL=http://sales-market:8766`
- **Trend photos for the Planning Sheet:** with the `planning_sheet` provider, stage 06 downloads up to three example photos of the poster's focus Trend style (`planning_sheet.focus_trend`: the style P06's `draft.trend_basis.source_style_id` names; for runs without it, the style the Draft cites most), from `style.example_images`, then its items' `image_urls`.
  - HTTPS with CA verification only, to hosts in `VOGUE_TREND_PHOTO_HOSTS` (default `vogue.co.kr` and its subdomains); at most two redirects, each on the allowlist.
  - At most 5 MiB and 6 megapixels per photo (the pixel count is read from the header before decoding), PNG/JPEG/WebP only, with the same decode checks as uploaded images; 10-second timeout; no retries.
  - A rejected, unreachable or malformed-URL photo is skipped and never fails the run. The photos and the SWOT draft are gathered before stage 06 records that it started, so a crash while waiting for them does not stop a retry from composing the sheet. The photos are kept in the P08 store under `trend-photos:<request_id>`; the integrated envelope does not change.
  - `GET /api/workflow/runs/{id}/planning-sheet.png` renders from the stored photos and makes no network call. `VOGUE_TREND_PHOTOS_ENABLED=0` turns the download off.
- **SWOT and promotion draft for the Planning Sheet:** with the `planning_sheet` provider and `LLM_MODEL`/`LLM_API_KEY` configured, stage 06 makes one call to the query-structuring LLM provider (`swot.py`, prompt `prompts/swot/v3`).
  - The request holds a compact summary of the stored run: the focus Trend style (as on the poster), the stock product matched to it with stock-agent's indicators (sell-through, stock value, recent trend, score and their data nature), new-product-agent's concept and specification, sales-market-agent's price, fabric cost, margin, target, channel/content hypotheses and merchandising actions, and the run limitations. It does not hold the user's raw request text or any image.
  - The answer must be 0–4 items per SWOT quadrant, 0–3 promotion ideas (title and one line) and 0–3 differentiation points (new product vs the matched stock product and the trend), each with its basis (`trend`, `stock`, `product`, `market`); anything else is discarded. The prompt forbids invented numbers (discount rates, budgets, targets) and price decisions. The valid draft is kept under `swot:<request_id>` and the poster labels it as an LLM hypothesis for MD review. The differentiation points appear only while new-product-agent's `draft.differentiation` is empty.
  - A failed or invalid answer leaves the SWOT slot empty and never fails the run; a stage-06 retry asks again only when no valid draft was kept. `GET /api/workflow/runs/{id}/planning-sheet.png` renders the stored draft without calling the model. `VOGUE_SWOT_ENABLED=0` turns the call off.
- **Timeouts:** `VOGUE_WORKFLOW_CONNECT_TIMEOUT_SECONDS` (default 3) and `VOGUE_WORKFLOW_READ_TIMEOUT_SECONDS` (default 75). No automatic retries.
- **Safe latency telemetry:** P08 emits one JSON log event per Agent call, workflow stage, and completed run. Events include `request_id`, stage/Agent, status, elapsed time, retryable/fatal flags, and (for HTTP calls) upstream transport time versus P08 call overhead. They must never include payloads, raw responses, prompts, credentials, or artifact contents.
- **Retry and idempotency:** P08 does not automatically repeat Agent calls. A repeated workflow request with the same ID replays the stored outcome; a checkpoint marked started but without a known result fails closed as `STAGE_OUTCOME_UNKNOWN`. `retryable` reports a transient dependency condition; it does not mean reposting the same request will issue another Agent call. Image generation uses its separate idempotent retry key and never reruns upstream Agents.
- **Partial outcomes:** Producer `partial` and nonfatal source errors are retained verbatim in `sources.*.raw_response`; P08 copies their error code/message/retryable flag into the integrated error list and keeps the overall status `partial`. A producer `error`, invalid contract, authentication failure, or failed integrity check is terminal and stops later Agents. A `no_results` outcome follows the stage-specific workflow rules below; it is never coerced to an empty-success result.
- **Failure mapping:**

  | Failure | Result |
  |---|---|
  | Timeout | 504 `UPSTREAM_TIMEOUT` |
  | Connection refused or reset | 503 `UPSTREAM_UNAVAILABLE` |
  | Upstream 401/403 | `UPSTREAM_AUTH` |
  | Other non-2xx | 502 |
  | Contract or parse failure | 502 `CONTRACT_VIOLATION` |

## 5. Inbound trust at P08 (S1)

- `_request_is_trusted_local` passes only if all three hold:
  - the peer is loopback, **or** the peer IP is in `VOGUE_ORCHESTRATOR_TRUSTED_PEER_CIDRS`;
  - **and** the `Host` header is in `VOGUE_ORCHESTRATOR_ALLOWED_HOSTS` (default `127.0.0.1:<port>`, `localhost:<port>`);
  - **and** `Origin` is absent, or exactly `http://<Host>`, port included.
- **Container mode** (`VOGUE_ORCHESTRATOR_RUNTIME=container`):
  - The check applies to every route except `GET /api/health` and static assets, including mutating workflow routes.
  - `/api/local-services/*` returns 404.
- **Host mode** keeps today's loopback-only gating for workflow routes, and the Origin check now includes the port.

## 6. Error bodies and HTTP codes (new `vogue-workflow` endpoints)

- **Problem body** (`common.schema.json#/$defs/problem`):
  `{"contract_version":"vogue-workflow/0.2","request_id":<id|null>,"status":"error","error":{"code","message","stage","fatal","retryable"}}`
  - `request_id` is null when unknown.
  - Messages never contain secrets, headers or raw upstream bodies.
- **Codes:**

  | Code | Meaning |
  |---|---|
  | 400 | Malformed JSON or body |
  | 401 | Missing or invalid bearer |
  | 403 | Untrusted origin or peer |
  | 404 | Unknown route |
  | 405 | Method not allowed |
  | 409 | State conflict: contract not frozen, stage outcome unknown, revision conflict |
  | 413 | Too large: request > 8,388,608 B, retained response > 16,777,216 B |
  | 415 | Wrong media type |
  | 422 | Semantic contract violation or unsupported version |
  | 502 | Upstream contract violation or non-2xx |
  | 503 | Dependency unavailable (fail closed) |
  | 504 | Timeout |

  `retryable` is true only for 503 and 504.
- **Producer outcomes** `partial` and `no_results` return HTTP 200 with the producer's own status. `no_results` never means zero.

## 7. Canonical hash: `python-json-sort/1`

```
sha256(json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
```

- Parse strictly: reject duplicate keys, NaN/Infinity and non-string keys.
- Raw-bytes artifacts use the plain SHA-256 of the exact bytes.
- Test vectors (from `tools/workflow_contract/validate_contracts.fingerprint`):

| Value | Digest |
|---|---|
| `{}` | `44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a` |
| `{"b":1,"a":"한"}` | `67f2fd255e411935d63adb1352ddb23efd6054c61d3131965ab2809e0c10d61f` |
| `{"request_id":"req-1","n":[1,2.5,null,true]}` | `3bd2f324eddc3ee90c8ddbafe3968bef3a7b1670f1e8750a83a66ad530315f0c` |

## 8. Artifact naming and resolution

- **URI grammar:** `artifact://<namespace>/<relative-path>`, matching `^artifact://[A-Za-z0-9_./-]+$`.
  - No empty, `.` or `..` segments.
  - A resolver maps `artifact://X` to `<root>/X`, refuses escapes and symlinks, verifies `size_bytes` and the bytes' `sha256`, and fails closed.
- **P08** mints content-addressed `artifact://p08/blobs/<sha256>`.
  - The same bytes always give the same URI.
  - Bytes live at `<store>/blobs/<sha256>`.
  - Store root is `VOGUE_WORKFLOW_STORE_DIR` (default `<repo>/.local/workflow`; container `/app/.local/workflow`).
- **P07** mints `artifact://p07/reviews/<sha256>.json` under `VOGUE_P07_REVIEW_ARTIFACT_DIR` (container `/data/reviews`).
- **P06** owns its native artifact contract. The current P08 `/runs` input does not request P08 visual references.
- **Read-only mounts in compose:**

  | Reader | Env | Mount |
  |---|---|---|
  | P06 and P07 | `VOGUE_UPSTREAM_ARTIFACT_ROOT=/artifacts` | P08 blobs at `/artifacts/p08/blobs` |
  | P08 | `VOGUE_PEER_ARTIFACT_ROOT=/peer` | P07 reviews at `/peer/p07/reviews` |

  P08 imports peer artifacts through the store's `import_verified`.
- **Error codes:**

  | Code | HTTP | Meaning |
  |---|---|---|
  | `ARTIFACT_READER_REQUIRED` | 503 | No resolver configured |
  | `INVALID_ARTIFACT_URI`, `INVALID_ARTIFACT_PATH` | 422 | Malformed or escaping URI |
  | `ARTIFACT_UNAVAILABLE` | 503 | Bytes cannot be read |
  | `ARTIFACT_SIZE_MISMATCH`, `ARTIFACT_HASH_MISMATCH` | 422 | Bytes don't match the reference |

## 9. Deterministic offline switches (stub profile)

| Service | Env | Meaning |
|---|---|---|
| P08 | `VOGUE_QUERY_STRUCTURING_MODE=synthetic_fixture` | Offline query structuring (existing) |
| P08 | `VOGUE_WORKFLOW_IMAGE_PROVIDER=planning_sheet\|fixture` (default `planning_sheet`) | `planning_sheet` composes the planning poster from the run; `fixture` renders a deterministic local PNG. Any other value is refused with 409 `IMAGE_PROVIDER_INVALID`. Images still need `VOGUE_WORKFLOW_IMAGES_ENABLED=1` |
| P08 | `VOGUE_WORKFLOW_IMAGE_RETRY_ENABLED=0\|1` (default 0) | Gates `POST /v1/orchestrations/image-retries` |
| P08 | `VOGUE_WORKFLOW_ALLOW_PROPOSED=1` | Run with a `proposed` contract baseline; adds a limitation to the result |
| P08 | `VOGUE_WORKFLOW_CONTRACT_BASELINE=<path>` | Overrides `contracts/workflow/baseline.json` |
| stock | `STOCK_LLM_PROVIDER=policy\|openai` (default `openai`) | `policy` = the existing deterministic `PolicyLLM` |
| stock | `STOCK_DB_PATH=/path/file.duckdb` | Local-file DB mode: skips R2 HEAD; readiness reports `db: local-file` |
| trend | `NEO4J_URI/USER/PASSWORD` | The stub uses a seeded `neo4j-stub` |
| P07 | `VOGUE_P07_REVIEW_SOURCE=synthetic_local_fixture` | Bundled synthetic sources; output labelled synthetic |
| P06 | `VOGUE_EXECUTION_PROFILE=draft_only`, `VOGUE_WORKFLOW_DRAFT_ENABLED=true` | Draft-only producer (rule-based) |

**Contract gate** (P08, `contracts/workflow/baseline.json`):
- Run when `state == "frozen"`, `producer_consumer_validated` is true, `snapshot_sha256` matches the current `snapshot.json` bytes, the files map matches, and evidence shows 0 failures.
- Otherwise, with `VOGUE_WORKFLOW_ALLOW_PROPOSED=1`, run and add the limitation `"workflow-contract/0.5.0 not frozen; development run"`.
- Otherwise return 409 `CONTRACT_NOT_FROZEN`.
- No code may read anything under `../_workflow`.

## 10. Tests

- **Rules:**
  - No network. Bind ephemeral ports (`127.0.0.1:0`). Use `tempfile` for paths, never hard-coded `/private/tmp`.
  - Every skip states its reason.
  - The full suite must be green before each commit.
- **Environment:**
  - Venvs live outside iCloud: `~/.cache/vogue-venvs/<trend|stock|new-product|sales-market|orchestrator>`.
  - Export `PYTHONPYCACHEPREFIX=~/.cache/vogue-pycache`.
  - Never create `.venv`, `build/` or `*.egg-info` inside the repos.
- **Suites:**

  | Repo | Command |
  |---|---|
  | trend | `pytest -q -m "not neo4j" -p no:cacheprovider` |
  | stock | `pytest -q -rs -p no:cacheprovider tests` |
  | P06 | `PYTHONPATH=src python -B scripts/run_offline_tests.py` |
  | P07 | `PYTHONPATH=src python scripts/run_tests.py` |
  | P08 | `PYTHONPATH=src python -m unittest discover -s tests` |

## 11. Data honesty (unchanged business rules)

- `no_results` ≠ 0. A null slot stays null with a missing reason. Synthetic and historical data stay labelled.
- `observation.as_of` is preserved and never replaced with "now".
- Price, cost, quantity, ordering and final adoption are MD decisions.
- Raw producer responses are retained, not rewritten.

## 12. Never

- Edit pinned schema bytes or vendored contract copies.
- Change the Trend or Stock wire formats.
- Add or print secrets.
- Push.
- Touch `_workflow/`, `_team/`, `_shared/`, `_vault/`.
- Remove Docker volumes or images.
- Make paid API calls.
