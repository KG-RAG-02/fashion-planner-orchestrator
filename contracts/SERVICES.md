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
                  (mode draft_with_visuals: 트렌드 반영 신상품 이미지 + 착장 이미지를 P06이 생성)
  4. sales-market POST /v1/agents/sales-market-agent/reviews    vogue-workflow/0.1 → sales-market-review/1.0
  5. integration  (P08 내부)  6. final_image (P08이 기획안 사진을 합성, 이미지 모델 호출 없음)
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
- **`style.search_outlook`** (2026-11-13 trend 작업 트리, 미커밋. `schema_version`은 1.1 그대로): 객체 또는 `null`입니다.
  - 내용: 그 트렌드를 이루는 키워드(아이템 명사, 속성 × 하분류)의 **목표 시즌 검색 전망**입니다. 네이버 검색에서 같은 카테고리 안 상대 관심의 방향이고 판매량 예측이 아닙니다.
  - 그래프에 키워드·전망이 없으면 `null`입니다(지금의 Aura 그래프가 이 상태입니다). stub 그래프에는 합성 값이 들어 있고 `source`가 `synthetic_fixture`입니다.
  - 방향 확률(`p_up`·`p_down`)은 trend 쪽 백테스트를 통과한 지평에만 있습니다. 그 전에는 계절 달력과 "현재 수준 유지" 배율만 있습니다.
  - 소비 측은 이 값을 받아들이되 순위·상태·lineage 판단에 쓰지 않습니다. 하위 객체에는 소비 측이 이름만 보고 특별 취급하는 키(`request_id`·`evidence_id(s)`·`errors`·`kind`·`status`·`mode`·`limitations`·`missing_*` 등, 전체 목록은 trend `schemas/trend_analysis_result.py`의 `RESERVED_NESTED_KEYS`)가 없고 NaN이 없습니다.
  - 구조: `trend-analysis-agent/docs/schemas/trend_analysis_result.md`의 `SearchOutlook`. 이 저장소의 사본(`agents/trend-analysis-agent/output/`)은 trend 커밋 뒤에 다시 만듭니다. 그때까지 `scripts/export_agent_contracts.py --check`는 STALE입니다.
- **오류**: envelope이 아닌 `{"error":{code,message}}` 형식입니다.
  - 400: `invalid_json`, `unsupported_contract_version`, `request_id_mismatch`, `invalid_trend_query` 등
  - 401, 404
  - 500: `invalid_search_result`
  - 503: `search_unavailable`
- **환경변수**: `VOGUE_AGENT_SERVICE_BEARER_TOKEN`, `NEO4J_URI/USER/PASSWORD`, `TREND_API_MODE/HOST/PORT`
- **증거**
  - `tests/test_api_config.py`, `tests/test_api_handler.py`, `tests/test_style_example_images.py`, `tests/test_search_market.py`
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
  - 504 `TIMEOUT`: 요청 전체 deadline. 기본 60초, `STOCK_HANDOFF_TIMEOUT_SECONDS`로 조정하며 통합 배포는 180초입니다(2026-11-13: 트렌드 11개 요청이 60초를 넘어 504 → 90초 → 사용자 결정으로 3분). 트렌드는 차례로 처리하고 LLM 사용 시 트렌드당 약 10초입니다(live 실측 8.8·11.0초)
  - 처리는 전역 lock 하나로 한 번에 한 건씩입니다.
- **재고 값**: H&M 기반 가상 재고 스냅샷(2020-09-22, `data_nature: sample`)입니다. 현재 자사 재고가 아닙니다.
- **환경변수**
  - `VOGUE_AGENT_SERVICE_BEARER_TOKEN`
  - `STOCK_DB_PATH`: local-file 모드
  - `STOCK_LLM_PROVIDER`: `policy` 또는 `openai`. 알 수 없는 값이면 실패로 닫힘
  - `LLM_API_KEY`, `LLM_MODEL`
  - `STOCK_CACHE_DIR`, `STOCK_IMAGE_CACHE_DIR`
  - `STOCK_HANDOFF_TIMEOUT_SECONDS`: 요청 전체 deadline(기본 60, 0 초과 600 이하)
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
| 쓰기 경로 | `/app/data/local`(초안 revision ledger SQLite, 시안 이미지 `p06-artifacts/images/<sha256>.<ext>`). 이 경로가 없으면 시작하지 않음 |

| 메서드 · 경로 | 입력 | 출력 |
|---|---|---|
| `POST /v1/agents/new-product-agent/drafts` | `vogue-workflow/0.1`. 본문 최대 8 MiB. `input{mode:"draft_only"\|"draft_with_visuals", user_input, planning_request, trend_response, stock_response, stock_call_context}`. 보존된 Trend·Stock 원문 bytes를 `/artifacts/p08/blobs`에서 크기·sha256으로 검증 → `agents/new-product-agent/input/planning-draft-request-1.0.contract.json` | `planning-draft/1.0`. `draft_identity{draft_id, revision, content_sha256}`, `draft{title, category, season, claims[evidence_refs], open_questions, md_decision_required, limitations, concept?, visuals?}`, `source_request`와 그 sha, `image_generation_calls`(0–2, visuals의 provider_calls 합), `review_calls:0` → `.../output/planning-draft-response-1.0.contract.json` |

- **mode `draft_with_visuals`** (workflow-contract 0.2.0, P08 기본값)
  - `draft.concept`: 규칙 기반 상품 사양(product_definition, attributes, risks, questions). 텍스트 모델은 쓰지 않으며, 근거는 evidence_id 코드입니다.
  - `draft.visuals.product`: Trend 근거로 만든 신상품 이미지(`concept_image` prompt, Images API generations)
  - `draft.visuals.outfit`: 같은 신상품을 입은 착장 이미지(Images API edits, 신상품 이미지가 참고 이미지)
    - 착장 역할 규칙 `outfit-role-pairing-v2`가 Stock 상품을 상의·하의로 짝지으면, Stock 응답에 들어 있는 그 상품 사진을 함께 참고합니다. 짝이 없으면 단독 착용입니다.
  - 이미지는 `artifact://p06/images/<sha256>.<ext>`로 참조합니다. P08이 `/peer/p06` 읽기 전용 마운트에서 크기·sha256을 검증해 가져갑니다.
  - 같은 요청은 저장된 응답을 그대로 돌려줍니다. 재시도해도 다시 과금되지 않습니다.
  - 공급자: `VOGUE_DRAFT_IMAGE_PROVIDER=fixture`(오프라인 결정적 PNG, stub·rehearsal) 또는 `openai`(live 전용, `OPENAI_API_KEY`, 모델 `VOGUE_DRAFT_IMAGE_MODEL`, 기본 `gpt-image-2.5-sunburst`). 공급자가 없으면 503 `VISUALS_PROVIDER_REQUIRED`입니다.
- **상태**: 맡은 산출물 기준입니다(2026-11-13 결정). 그 mode의 산출물(초안, visuals 모드면 두 이미지)을 모두 만들고 Stock이 `ok`이면 `ok`입니다. 데이터 공백은 limitations에 남깁니다. 이미지 하나라도 실패·생략이면 `partial`입니다.
- **revision**: 같은 입력을 다시 보내면 저장된 결과를 그대로 돌려줍니다. 입력이 바뀌면 revision이 1 올라갑니다.
- **오류**
  - 400: `INVALID_JSON`, `INVALID_CONTENT_LENGTH`, `INCOMPLETE_BODY`
  - 401
  - 405: `METHOD_NOT_ALLOWED`, `Allow: POST` 헤더 포함
  - 413, 415
  - 422: `UNSUPPORTED_CONTRACT_VERSION`, `SCHEMA_VIOLATION`, `INVALID_ARTIFACT_PATH`, `ARTIFACT_SIZE_MISMATCH`, `ARTIFACT_HASH_MISMATCH`, `UPSTREAM_STOP_REQUIRED`
  - 503: `PROPOSED_CONTRACT_DISABLED`, `SERVICE_BUSY`, `ARTIFACT_READER_REQUIRED`, `ARTIFACT_UNAVAILABLE`, `VISUALS_PROVIDER_REQUIRED`
  - 504, 500
- **계약 고정**: `contracts/workflow-proposed/snapshot.json`이 schema bytes를 sha256으로 고정합니다.
- **환경변수**: `VOGUE_HTTP_MODE`, `VOGUE_HOST/PORT`, `VOGUE_AGENT_SERVICE_BEARER_TOKEN`, `VOGUE_EXECUTION_PROFILE=draft_only`, `VOGUE_WORKFLOW_DRAFT_ENABLED=true`, `VOGUE_UPSTREAM_ARTIFACT_ROOT=/artifacts`, `VOGUE_DRAFT_IMAGE_PROVIDER`, `VOGUE_DRAFT_ARTIFACT_DIR=/app/data/local/p06-artifacts`, (live) `OPENAI_API_KEY`, `VOGUE_DRAFT_IMAGE_MODEL`
- **기존 경로**
  - `POST /v1/agents/new-product-agent/runs`(`vogue-handoff/0.3`) → `.../input/planning-draft-input-0.3.contract.json`
  - `/api/*` 프로토타입 경로
  - 모두 private 모드에서 bearer가 필요합니다.
- **증거**: 테스트 155건(`tests/test_draft_only.py`, `tests/test_draft_visuals.py`, `tests/test_private_mode.py`, `tests/test_healthcheck.py`)

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

- **상태**: `ok`·`no_results`는 200, `error`는 503/504입니다. 소견이나 지표를 만들면 `ok`입니다(2026-11-13 결정, 맡은 산출물 기준). 데이터 공백은 limitations와 `missing_reason`에 남깁니다.
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
| `GET /api/workflow/artifacts/{sha256}` | 저장된 private artifact(기획안 사진, 가져온 P06 시안 이미지 등) |
| `GET /api/workflow/runs/{rid}/planning-sheet.png` | 끝난 실행의 기획서 PNG(3200 px 폭, `image/png`, attachment). 저장된 integrated envelope, 검증된 P06 시안 이미지(0.2.0 이전 실행은 최종 이미지), MD 결정 이력을 근거 연결 기획 보드 양식(`new-product-agent/docs/design/evidence-linked-planning-board.md`)으로 그림. 표시 전용이며 agent를 부르지 않고 저장값을 바꾸지 않음. 진행 중 409 `RUN_NOT_FINISHED`, 없음 404, 한글 글꼴 없음 503 `PLANNING_SHEET_FONT_MISSING` |
| `POST /v1/orchestrations/image-retries` | 6단계(기획안 사진 합성)만 다시 실행. 무료이며 P06 이미지는 다시 만들지 않음. `VOGUE_WORKFLOW_IMAGE_RETRY_ENABLED=1`일 때만 동작, 기본은 409 |
| `GET /api/system/status` · `GET /api/health` | agent liveness 집계, 계약 gate 상태, 이미지·구조화 설정 / liveness |

**출력** — `integrated-result/1.0` → `agents/vogue-orchestrator/output/integrated-result-1.0.contract.json`
- `sources`: 원문 응답, bytes artifact, `content_sha256`, `observation`
- `stages[6]`: 각 단계의 입력·응답 sha
- `integration`
- `final_image{status: generated|skipped|failed, provider_calls≤1, lineage}`: 0.2.0부터 **기획안 사진(기획서 PNG)** 입니다. P08이 실행 기록으로 결정적으로 합성하고, `provider_calls: 1`은 합성 함수 호출 1회입니다(유료 호출 아님).
- `limitations`, `errors`
- `md_review{status: pending, choices: adopt|revise|hold}`

**호출자로서의 정책**
- **연결 대상**: `VOGUE_AGENT_HTTP_PRIVATE_HOSTS`에 있는 호스트에만 평문 HTTP로 연결합니다.
- **timeout**: connect 3초, read 75초. P06 Draft 호출은 read 600초(`VOGUE_WORKFLOW_DRAFT_READ_TIMEOUT_SECONDS`, P06이 이미지 2장을 만드는 시간), Stock 호출은 read 210초(`VOGUE_WORKFLOW_STOCK_READ_TIMEOUT_SECONDS`, Stock deadline 180초보다 길게)입니다. 자동 재시도는 없습니다.
- **peer artifact**: `artifact://p07/…`(Review)와 `artifact://p06/…`(Draft 시안 이미지)를 `/peer/<ns>`에서 크기·sha256으로 검증해 저장소로 가져옵니다.
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
- **기획안 사진 (6단계, `VOGUE_WORKFLOW_IMAGE_PROVIDER=planning_sheet`)** (2026-11-13 결정)
  - P08의 이미지 코드는 기획안 사진만 만듭니다. 이미지 모델을 부르지 않습니다. 트렌드 반영 신상품 이미지와 착장 이미지는 P06이 만듭니다.
  - 리뷰가 끝난 실행 기록(1–5단계)과 가져온 P06 시안 이미지로 `planning_sheet.py`가 PNG를 그립니다.
  - lineage·hash 규약은 0.1.1과 같습니다.
  - 이전의 OpenAI 최종 이미지 경로(`image_request.py`, prompt `reviewed_image`)는 제거했습니다. `openai` 값은 409 `IMAGE_PROVIDER_INVALID`로 거부합니다.
- **계약 gate**: `contracts/workflow/baseline.json`이 frozen이어야 실행합니다. 아니면 409 `CONTRACT_NOT_FROZEN`입니다. stub은 `VOGUE_WORKFLOW_ALLOW_PROPOSED=1`로 development 모드로 돌며, 이때 결과에 limitation이 붙습니다.

**증거**
- 테스트 470건과 UI 테스트 15건
- `deploy/e2e/run_e2e.py` 검사
  - 단계 순서와 6단계 모두 `ok`
  - P06 신상품·착장 이미지 생성, 가져오기와 sha256 검증
  - 6단계가 3200 px 기획안 사진인지
  - P08 이미지 모델 호출 0회
  - replay 때 upstream 호출 0회
  - schema 검증
  - MD 결정
- `deploy/e2e/probe_auth.py` 21개 검사

## 6. 버전 표

| 경계 | transport | producer schema | 상태 |
|---|---|---|---|
| P08 → Trend | `vogue-handoff/0.1` | Trend `1.1` | 기존 계약 유지 |
| P08 → Stock | `vogue-handoff/0.1` | Stock `1.0` | 기존 계약 유지 |
| P08 → P06 | `vogue-workflow/0.1` | `planning-draft/1.0` | workflow-contract 0.2.0 |
| P08 → P07 | `vogue-workflow/0.1` | `sales-market-review/1.0` | workflow-contract 0.2.0 |
| UI → P08 | `vogue-workflow/0.1` | `integrated-result/1.0` | workflow-contract 0.2.0 |

이 표에 없는 버전은 모델 호출이나 DB 조회 전에 거부합니다. 새 경로는 422, 기존 경로는 400을 돌려줍니다.
