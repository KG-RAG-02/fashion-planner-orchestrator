# VOGUE 서비스 I/O 규정 (vogue-workflow/0.1)

다섯 서비스가 무엇을 받고 무엇을 돌려주는지 정한 문서입니다. 기준은 각 저장소의 `integration/docker-e2e` 코드입니다.

**이 문서와 함께 보는 자료**
- 스키마 원본: `contracts/agents/**`(registry), `contracts/workflow/*.schema.json`(워크플로 계약 패키지)
- 공통 규칙: `docs/workflow-integration-conventions.md` — 오류 본문, hash, artifact, 환경변수
- 계약 원문: `contracts/workflow/workflow-contract.md`

**검증 근거**
- 각 저장소 테스트 스위트
- 컨테이너 stub end-to-end 결과(`deploy/bin/vogue stub`): 단계 6개, 인증 probe 17개, inbound probe 4개, E2E 검사 18개 모두 통과
- registry 검사(`scripts/export_agent_contracts.py --check`): exit 0

## 흐름

```
UI ─POST /api/workflow/run─► orchestrator(P08)
  1. trend        POST /v1/agents/trend-analysis-agent/runs     vogue-handoff/0.1  → Trend 1.1
  2. stock        POST /v1/agents/stock-agent/runs              vogue-handoff/0.1  (input = Trend result 원문) → Stock 1.0
  3. new-product  POST /v1/agents/new-product-agent/drafts      vogue-workflow/0.1 → planning-draft/1.0
  4. sales-market POST /v1/agents/sales-market-agent/reviews    vogue-workflow/0.1 → sales-market-review/1.0
  5. integration  (P08 내부)  6. final_image (P08, 리뷰 이후 최대 1회)
  → integrated-result/1.0 → UI · MD 채택/수정/보류
```

단계 호출은 모두 순차입니다. 모든 단계는 같은 `request_id`를 씁니다. 각 단계의 원문 응답은 bytes와 hash째 보존합니다(`retained_*`).

## 공통

**인증과 네트워크**
- 서비스끼리는 `Authorization: Bearer $VOGUE_AGENT_SERVICE_BEARER_TOKEN`으로 인증합니다. 토큰은 본문을 읽기 전에 확인하고, 없거나 틀리면 401입니다.
- 컨테이너 사이는 내부 `backend` 네트워크에서 평문 HTTP로 통신합니다. P08은 `VOGUE_AGENT_HTTP_PRIVATE_HOSTS`에 등록된 호스트에만 연결합니다.

**오류 본문**
- 새 워크플로 경로(`/drafts`, `/reviews`, P08 워크플로)는 `common.schema.json#/$defs/problem` 형식으로 답합니다: `{contract_version, request_id|null, status:"error", error{code,message,stage,fatal,retryable}}`.
- `retryable`은 503과 504일 때만 `true`입니다.
- 기존 `/runs` 경로는 각자의 기존 오류 형식을 유지합니다.

**hash** — `python-json-sort/1`을 씁니다.
```
sha256(json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
```

**정직성 규칙**
- `no_results`는 0이 아닙니다.
- 값이 null이면 `missing_reason`을 함께 둡니다.
- 합성·과거·샘플 자료는 반드시 표기합니다.
- 가격·원가·수량·발주·최종 채택은 MD가 판단합니다.

---

## 1. trend-analysis-agent (Trend)

| 항목 | 값 |
|---|---|
| 이미지 · 포트 · 호스트 | `vogue/trend:dev` · 8768 · `trend` |
| 실행 | `python -m trend_agent.api.server --mode service --host 0.0.0.0 --port 8768` (uid 10001, 읽기 전용 rootfs) |
| liveness | `GET /health` (인증 없음). readiness 엔드포인트 없음. 시작 시 Neo4j에 연결하지 못하면 프로세스가 종료됨 |
| 데이터 | Neo4j: rehearsal·live는 Aura, stub은 합성 그래프를 넣은 `neo4j-stub` |

| 메서드 · 경로 | 입력 | 출력 |
|---|---|---|
| `POST /v1/agents/trend-analysis-agent/runs` | `{contract_version:"vogue-handoff/0.1", request_id, input: TrendQuery}`. 본문 최대 1 MB. `input.request_id`는 바깥 값과 같아야 함 → `agents/trend-analysis-agent/input/trend-query.contract.json` | `{contract_version, request_id, producer{agent_id, schema_version:"1.1"}, status, result: TrendAnalysisResult 1.1}` → `agents/trend-analysis-agent/output/trend-analysis-result-1.1.contract.json` |

- **상태**
  - `ok`: trends와 evidence가 있음.
  - `no_results`: 0이 아니라 "찾지 못함"을 뜻함.
  - `partial`은 실제로 만들어지지 않습니다.
- **Evidence ID**: `trend:<article>:<block>:<sha8(quote)>`.
- **`style.example_images`**: 최대 5개 링크입니다. 소비 측은 링크로만 쓰고 이미지를 내려받거나 페이지에 직접 넣지 않습니다(G-10).
- **오류**: envelope이 아닌 `{"error":{code,message}}` 형식입니다.
  - 400: `invalid_json`, `unsupported_contract_version`, `request_id_mismatch`, `invalid_trend_query` 등
  - 401, 404
  - 500: `invalid_search_result`
  - 503: `search_unavailable`
- **환경변수**: `VOGUE_AGENT_SERVICE_BEARER_TOKEN`, `NEO4J_URI/USER/PASSWORD`, `TREND_API_MODE/HOST/PORT`
- **증거**
  - `tests/test_api_config.py`, `tests/test_api_handler.py`, `tests/test_style_example_images.py`
  - stub 요청·결과: `examples/stub/trend-request.json` → `ok` (trend 1개, evidence 4개)

## 2. stock-agent (Stock)

| 항목 | 값 |
|---|---|
| 이미지 · 포트 · 호스트 | `vogue/stock:dev` · 8002 · `stock` |
| 실행 | `python -m stock_agent.server --host 0.0.0.0` (FastAPI/uvicorn, uid 10001) |
| liveness / readiness | `GET /health/live` / `GET /health/ready`(별칭 `/health`, 200 또는 503). readiness는 `db`, `db_version`, `llm_provider`, `synthetic_fixture`를 보고 |
| 데이터 | R2의 DuckDB 캐시(`--prepare-cache`가 sidecar sha256과 함께 채움). stub은 `STOCK_DB_PATH` fixture DB를 씀 |

| 메서드 · 경로 | 입력 | 출력 |
|---|---|---|
| `POST /v1/agents/stock-agent/runs` | `{contract_version:"vogue-handoff/0.1", request_id, input}`. 정의되지 않은 키는 거부(`extra=forbid`). `input`은 **Trend result 원문 그대로**이며 `schema_version`이 `"1.1"`이어야 함 → `agents/stock-agent/input/stock-agent-request-1.0.contract.json` | Stock 1.0 envelope. result의 `products[]`(null slot 허용)는 `trend_results[]`와 순서로 짝지어짐. 그 밖에 `season_basis`, `evidence`, `errors`, `input_provenance.source_payload_sha256`(Trend result body의 hash) → `agents/stock-agent/output/stock-agent-result-1.0.contract.json` |

- **상태**: `ok`, `partial`, `no_results`, `error`.
  - Trend가 `no_results`이면 DB 조회 없이 200 `no_results`를 돌려줍니다.
  - Trend가 `error`이면 400(`reason=upstream_error`)입니다.
- **오류**
  - 400 `INVALID_REQUEST`, 401
  - 500 `INTERNAL_ERROR`
  - 503 `SOURCE_UNAVAILABLE`: DB, `no_llm`, `llm_unavailable`, 이미지
  - 504 `TIMEOUT`: 60초 deadline
  - 처리는 전역 lock 하나로 한 번에 한 건씩입니다.
- **재고 값**: H&M 기반 가상 재고 스냅샷(2020-09-22, `data_nature: sample`)입니다. 현재 자사 재고가 아닙니다.
- **환경변수**
  - `VOGUE_AGENT_SERVICE_BEARER_TOKEN`
  - `STOCK_DB_PATH`: local-file 모드
  - `STOCK_LLM_PROVIDER`: `policy` 또는 `openai`. 알 수 없는 값이면 실패로 닫힘
  - `LLM_API_KEY`, `LLM_MODEL`
  - `STOCK_CACHE_DIR`, `STOCK_IMAGE_CACHE_DIR`
  - `R2_*`
- **기존 경로**: `POST /v1/inventory-products`(bearer 필요).
- **증거**
  - 테스트 453건
  - stub 결과: `examples/stub/stock-request.json` → `ok`, 상품 1개, provenance hash 일치

## 3. new-product-agent (P06 · 기획 초안)

| 항목 | 값 |
|---|---|
| 이미지 · 포트 · 호스트 | `vogue/new-product:dev` · 8767 · `new-product` |
| 실행 | `VOGUE_HTTP_MODE=private`: 평문 HTTP이며 모든 경로에 bearer 필요. TLS가 필요하면 `service` 모드를 씀 |
| liveness | `GET /api/health`. private·service 모드에서는 bearer 필요 |
| 쓰기 경로 | `/app/data/local`(초안 revision ledger SQLite). 이 경로가 없으면 시작하지 않음 |

| 메서드 · 경로 | 입력 | 출력 |
|---|---|---|
| `POST /v1/agents/new-product-agent/drafts` | `vogue-workflow/0.1`. 본문 최대 8 MiB. `input{mode:"draft_only", user_input, planning_request, trend_response, stock_response, stock_call_context}`. 보존된 Trend·Stock 원문 bytes를 `/artifacts/p08/blobs`에서 크기·sha256으로 검증 → `agents/new-product-agent/input/planning-draft-request-1.0.contract.json` | `planning-draft/1.0`. `draft_identity{draft_id, revision, content_sha256}`, `draft{title, category, season, claims[evidence_refs], open_questions, md_decision_required, limitations}`, `source_request`와 그 sha, `image_generation_calls:0`, `review_calls:0` → `.../output/planning-draft-response-1.0.contract.json` |

- **상태**: 200 응답은 **항상 `partial`**입니다. 규칙 기반 초안이라 한계 문구가 항상 붙습니다. `ok`·`no_results`는 아직 만들어지지 않습니다.
- **revision**: 같은 입력을 다시 보내면 저장된 결과를 그대로 돌려줍니다. 입력이 바뀌면 revision이 1 올라갑니다.
- **오류**
  - 400: `INVALID_JSON`, `INVALID_CONTENT_LENGTH`, `INCOMPLETE_BODY`
  - 401
  - 405: `METHOD_NOT_ALLOWED`, `Allow: POST` 헤더 포함
  - 413, 415
  - 422: `UNSUPPORTED_CONTRACT_VERSION`, `SCHEMA_VIOLATION`, `INVALID_ARTIFACT_PATH`, `ARTIFACT_SIZE_MISMATCH`, `ARTIFACT_HASH_MISMATCH`, `UPSTREAM_STOP_REQUIRED`
  - 503: `PROPOSED_CONTRACT_DISABLED`, `SERVICE_BUSY`, `ARTIFACT_READER_REQUIRED`, `ARTIFACT_UNAVAILABLE`
  - 504, 500
- **계약 고정**: `contracts/workflow-proposed/snapshot.json`이 schema bytes를 sha256으로 고정합니다.
- **환경변수**: `VOGUE_HTTP_MODE`, `VOGUE_HOST/PORT`, `VOGUE_AGENT_SERVICE_BEARER_TOKEN`, `VOGUE_EXECUTION_PROFILE=draft_only`, `VOGUE_WORKFLOW_DRAFT_ENABLED=true`, `VOGUE_UPSTREAM_ARTIFACT_ROOT=/artifacts`
- **기존 경로**
  - `POST /v1/agents/new-product-agent/runs`(`vogue-handoff/0.3`) → `.../input/planning-draft-input-0.3.contract.json`
  - `/api/*` 프로토타입 경로
  - 모두 private 모드에서 bearer가 필요합니다.
- **증거**: 테스트 142건(`tests/test_draft_only.py`, `tests/test_private_mode.py`, `tests/test_healthcheck.py`)

## 4. sales-market-agent (P07 · 시장 검토)

| 항목 | 값 |
|---|---|
| 이미지 · 포트 · 호스트 | `vogue/sales-market:dev` · 8766 · `sales-market` |
| 실행 | `vogue-market-research-http --service-mode --host 0.0.0.0 --port 8766` |
| liveness | `GET /api/health`. 인증 없음, `{"status":"ok","mode":"service"}` 반환 |
| 쓰기 경로 | `/data/reviews`(review artifact), `/data/hm-cache`(R2 H&M DuckDB 캐시, data 프로필에서만) |

| 메서드 · 경로 | 입력 | 출력 |
|---|---|---|
| `POST /v1/agents/sales-market-agent/reviews` | `vogue-workflow/0.1`. 본문 최대 8 MiB. `input{planning_draft: 보존된 Draft 전체, derived_view\|null}`. Draft의 원문 bytes를 `/artifacts/p08/blobs`에서 검증 → `agents/sales-market-agent/input/sales-market-review-request-1.0.contract.json` | `sales-market-review/1.0`. `review_identity`와 `review{reviewed_draft, draft_response_sha256, findings, evidence, metrics, limitations, observation}`. artifact는 `artifact://p07/reviews/<sha>.json` → `.../output/sales-market-review-response-1.0.contract.json` |

- **상태**: `partial`·`no_results`는 200, `error`는 503/504입니다. **`ok`는 만들어지지 않습니다.**
- **metric**: `current_company_sales`는 항상 `null`이고 `missing_reason`이 붙습니다.
- **오류**
  - 400, 401, 405(`Allow: POST`)
  - 409: `DRAFT_REVISION_CONFLICT`
  - 413, 415
  - 422: `UNSUPPORTED_CONTRACT_VERSION`, `DRAFT_HASH_MISMATCH` 등
  - 502
  - 503: `ARTIFACT_READER_REQUIRED`, source 오류
  - 504, 500
  - `retryable`은 503·504일 때만 `true`입니다.
- **합성 source**: `VOGUE_P07_REVIEW_SOURCE=synthetic_local_fixture`이면 고정된 합성 발췌를 씁니다. 결과는 `SYNTHETIC TEST DATA ONLY`로 표기되며, Draft의 카테고리·시즌과 연결되지 않습니다.
- **계약 고정**: `resources/workflow_contract/snapshot.json`. 불일치하면 import 단계에서 `SCHEMA_SNAPSHOT_CHANGED`로 막힙니다.
- **환경변수**: `VOGUE_AGENT_SERVICE_BEARER_TOKEN`, `VOGUE_UPSTREAM_ARTIFACT_ROOT`, `VOGUE_P07_REVIEW_ARTIFACT_DIR`, `VOGUE_R2_HM_DB_CACHE_DIR`, `VOGUE_P07_REVIEW_SOURCE`, `R2_*`, `VOGUE_PROJECT07_DISABLE_DOTENV=1`
- **기존 경로**
  - `/v1/agents/sales-market-agent/runs`(`vogue-handoff/0.1`) → `.../input/sales-market-run-request-0.1.contract.json`
  - 폐지된 별칭 하나
  - `/structured-runs`
- **증거**: 테스트 191 / 17 / 19건(DuckDB 포함, skip 0)

## 5. vogue-orchestrator (P08 · 오케스트레이터와 UI)

| 항목 | 값 |
|---|---|
| 이미지 · 포트 | `vogue/orchestrator:dev` · 8765. 공개 주소는 `127.0.0.1:8765`, stub은 `18765` |
| 인바운드 정책 | loopback 또는 `VOGUE_ORCHESTRATOR_TRUSTED_PEER_CIDRS`(edge 서브넷)에서 오고, Host와 Origin이 허용 목록과 정확히 일치해야 함. 컨테이너 모드에서는 `/api/health`와 정적 파일을 뺀 모든 경로에 적용 |
| 저장소 | `/app/.local/workflow`. checkpoint SQLite와 내용주소 blob `artifact://p08/blobs/<sha256>`을 저장 |

| 메서드 · 경로 | 용도 |
|---|---|
| `POST /api/workflow/run`, `POST /v1/orchestrations`(`contract_version: vogue-workflow/0.1`) | 6단계 실행. 입력 `{contract_version, request_id, input{user_input{text≤8000, images≤1}, trend_query?, planning_request?}}`. `trend_query`가 없으면 질의 구조화를 거침(LLM 또는 `synthetic_fixture`) |
| `POST /v1/input-images` | PNG·JPEG·WebP, 최대 10 MiB. `X-Request-ID` 헤더 필요. 201과 private artifact ref 반환 |
| `GET /api/workflow/runs?limit=N` · `GET /api/workflow/runs/{rid}` | 실행 목록(최신순)과 보존된 integrated envelope |
| `GET`/`POST /api/workflow/runs/{rid}/md-decision` | append-only MD 결정 → `agents/vogue-orchestrator/input/md-decision-request-1.0.contract.json`. GET은 `integrated_response_sha256`과 결정 이력을 반환 |
| `GET /api/workflow/artifacts/{id}` | 최종 이미지 등 private artifact |
| `GET /api/workflow/runs/{rid}/planning-sheet.png` | 끝난 실행의 기획서 PNG(3200 px 폭, `image/png`, attachment). 저장된 integrated envelope, 검증된 최종 이미지, MD 결정 이력을 근거 연결 기획 보드 양식(`new-product-agent/docs/design/evidence-linked-planning-board.md`)으로 그림. 표시 전용이며 agent를 부르지 않고 저장값을 바꾸지 않음. 진행 중 409 `RUN_NOT_FINISHED`, 없음 404, 한글 글꼴 없음 503 `PLANNING_SHEET_FONT_MISSING` |
| `POST /v1/orchestrations/image-retries` | 이미지 단계만 다시 시도. `VOGUE_WORKFLOW_IMAGE_RETRY_ENABLED=1`일 때만 동작, 기본은 409 |
| `GET /api/system/status` · `GET /api/health` | agent liveness 집계, 계약 gate 상태, 이미지·구조화 설정 / liveness |

**출력** — `integrated-result/1.0` → `agents/vogue-orchestrator/output/integrated-result-1.0.contract.json`
- `sources`: 원문 응답, bytes artifact, `content_sha256`, `observation`
- `stages[6]`: 각 단계의 입력·응답 sha
- `integration`
- `final_image{status: generated|skipped|failed, provider_calls≤1, lineage}`
- `limitations`, `errors`
- `md_review{status: pending, choices: adopt|revise|hold}`

**호출자로서의 정책**
- **연결 대상**: `VOGUE_AGENT_HTTP_PRIVATE_HOSTS`에 있는 호스트에만 평문 HTTP로 연결합니다.
- **timeout**: connect 3초, read 75초. 자동 재시도는 없습니다.
- **실패 매핑**

  | 상황 | 결과 |
  |---|---|
  | timeout | 504 `UPSTREAM_TIMEOUT` |
  | 연결 실패 | 503 `UPSTREAM_UNAVAILABLE` |
  | upstream이 503/504로 응답 | 의미를 그대로 유지 |
  | upstream이 401/403으로 응답 | `UPSTREAM_AUTH` |
  | 그 밖의 non-2xx, 계약 위반 | 502 |

- **중단 규칙**
  - Trend나 Draft가 `no_results` → 그 자리에서 멈추고 결과를 `no_results`로 둡니다.
  - 어느 단계든 `error` → 즉시 멈추고 이미지는 0회입니다.
  - Review가 `no_results` → 결과는 `partial`이고 이미지는 0회입니다.
- **최종 이미지 요청** (P08 → OpenAI Images API, `src/vogue_orchestrator/image_request.py`, prompt `reviewed_image` v2 = `p08-reviewed-image/2`)
  - 원천 값은 원문 그대로 보내고, 요약·재작성·잘라내기를 하지 않습니다.
  - 섹션 순서는 `1_request → 2_draft → 3_trend → 4_stock → 5_review → 6_limitations`입니다.
  - 근거는 ID 코드로 보냅니다(evidence_id, product_id, similar_product_codes).
  - P07 원본 지표·근거는 이미지 요청에서만 제외합니다. P07 응답, 실행 기록, 기획서 PNG는 그대로입니다.
  - 완전 중복과 무결성 메타데이터(해시, artifact 참조, lineage)는 prompt에 넣지 않고 저장 기록에 둡니다.
  - 트렌드는 Trend 응답 순서로 앞의 3개만 보냅니다. 나머지는 이름·코드를 기록에 남깁니다.
  - 전송 경로
    - Stock 상품 사진과 사용자 업로드 이미지는 base64 텍스트가 아니라 참고 이미지로 보냅니다(`/v1/images/edits`, data URL, 최대 16장).
    - 참고 이미지가 없으면 `/v1/images/generations`로 보냅니다.
    - 공통 옵션: `size=1536x1024`, `quality=high`, `output_format=png`.
  - prompt가 32,000자(문서상 한도)를 넘으면 HTTP 호출 없이 `IMAGE_PROMPT_TOO_LONG`으로 실패합니다.
  - 진단 정보는 checkpoint에만 남고 `final_image`에는 넣지 않습니다.
    - 남기는 것: 발신 prompt artifact, 길이, endpoint, `usage`(`text_tokens`), `x-request-id`, 오류 본문의 code·type·param(message는 저장하지 않음)
    - `text_tokens`가 64 미만이면 prompt가 무시된 것으로 보고 `IMAGE_PROMPT_IGNORED`로 실패 처리합니다. 이때 이미지는 private로만 보관합니다.
  - 측정(2026-11-13)
    - live: 4,026,589자 → 13,968자(참고 이미지 1장)
    - rehearsal(트렌드 7개): 26,337자(트렌드 3개)
    - stub: 10,113자
  - 확인: `python -m vogue_orchestrator.image_request --input <envelope.json>`, `--diagnostics --request-id <id> --store-dir <dir>`(읽기 전용)
- **계약 gate**: `contracts/workflow/baseline.json`이 frozen이어야 실행합니다. 아니면 409 `CONTRACT_NOT_FROZEN`입니다. stub은 `VOGUE_WORKFLOW_ALLOW_PROPOSED=1`로 development 모드로 돌며, 이때 결과에 limitation이 붙습니다.

**증거**
- 테스트 452건과 UI 테스트 13건
- `deploy/e2e/run_e2e.py` 18개 검사: 단계 순서, 이미지 최대 1회, replay 때 upstream 호출 0회, schema 검증, MD 결정
- `deploy/e2e/probe_auth.py` 21개 검사

## 6. 버전 표

| 경계 | transport | producer schema | 상태 |
|---|---|---|---|
| P08 → Trend | `vogue-handoff/0.1` | Trend `1.1` | 기존 계약 유지 |
| P08 → Stock | `vogue-handoff/0.1` | Stock `1.0` | 기존 계약 유지 |
| P08 → P06 | `vogue-workflow/0.1` | `planning-draft/1.0` | workflow-contract 0.1.1 |
| P08 → P07 | `vogue-workflow/0.1` | `sales-market-review/1.0` | workflow-contract 0.1.1 |
| UI → P08 | `vogue-workflow/0.1` | `integrated-result/1.0` | workflow-contract 0.1.1 |

이 표에 없는 버전은 모델 호출이나 DB 조회 전에 거부합니다. 새 경로는 422, 기존 경로는 400을 돌려줍니다.
