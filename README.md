# Project_08 · VOGUE Orchestrator

Project_06·07·08의 공통 저장소 레이아웃과 실행 경계는 [`docs/repository-boundaries.md`](docs/repository-boundaries.md)를 참고하세요.

The agreed agent HTTP v0.1 contract, Project_08 client implementation, blockers, and the separate client-facing preview API are documented in [`docs/orchestration-http-api-proposal.md`](docs/orchestration-http-api-proposal.md).

Project_08은 VOGUE 중앙 오케스트레이션 계층의 실행 가능한 로컬 코어입니다. 사용자 요청마다 하나의 `request_id`를 발급해 아래 순서로 어댑터를 호출하고, raw 응답과 단계 상태를 실행 기록에 보존합니다.

```text
사용자 쿼리 → trend-analysis-agent → stock-agent
            → P08 이관 PlanningDraft 생성기·상품 시안
                  → sales-market-agent 사후 검토 → P08 통합 UI → MD 검토
```

## 현재 구현 범위

agent별 현재 구현과 fixture 기반 input/output은 [`docs/agent-io-contracts.md`](docs/agent-io-contracts.md)에 정리했습니다. 공통 HTTP envelope는 팀 합의 `vogue-handoff/0.1`을 사용하며, producer별 필드와 schema는 producer 소유입니다.

PlanningDraft 생성 엔진은 P06 Git commit `07e7d4d`에서 P08로 이관했습니다. P08이 초안과 기획 이미지를 만들고 P07에 리뷰를 한 번 요청합니다. P06은 상품 옵션 생성과 legacy `/api/planning-draft` 호환 경로를 계속 소유합니다.

현재 구현과 합의 v0.1 간 차이, sales-market-agent 결과 및 차트 artifact의 실제 handoff 경로는 [`docs/integration-gap-analysis.md`](docs/integration-gap-analysis.md)에 기록했습니다. 특히 sales-market-agent envelope 전체는 실행 기록에 남지만 downstream 요청에는 `result`만 전달되고 sibling `artifacts`는 전달되지 않습니다.

- 동기 순차 runner는 transport-neutral callable 어댑터를 받습니다. Legacy mixed mode는 기존 호환 경로를 유지합니다. `agent_http_v01` mode는 Trend → Stock을 호출하고, P08의 이관 생성기가 PlanningDraft를 만든 뒤 P07 `/reviews`를 한 번 요청합니다.
- 응답 원문을 복사·정규화하지 않고 `StageRecord.raw_response`로 보존합니다. request ID, producer ID, producer schema version, 상태 일치 여부는 별도 검증합니다.
- P07 `/reviews`에는 P08이 생성한 PlanningDraft의 category 등 지원 필드만 전달합니다. Trend/Stock 결과 원본과 evidence를 별도 PlanningDraft trace에 보존합니다.
- P08은 Trend 1.1 원본 payload를 보존하면서 내부 생성기 입력용 1.0 사본으로 변환하고, Stock 1.0을 그대로 사용합니다. 입력 근거·스키마 변환 provenance·생성 제한은 PlanningDraft에 포함됩니다.
- 실제 계약 대조 결과 trend producer는 `TrendAnalysisResult` v1.1에서 `schema_version`을 반환하지만 `agent_id`는 내보내지 않습니다. 이전 v1.0 결과에는 버전 필드가 없을 수 있습니다. runner는 raw 결과와 producer 버전을 그대로 보존하고, ID는 저장소 정체성으로 별도 기록합니다.
- PlanningDraft와 상품 시안은 이관된 P06 생성기가 P08에서 만듭니다. UI는 초안의 근거·제한과 P07 SalesMarketReview의 근거·누락 자료를 나란히 표시합니다.
- P08 runtime은 `VOGUE_SALES_MARKET_AGENT_URL` 및 `VOGUE_AGENT_SERVICE_BEARER_TOKEN`으로 P07 리뷰 endpoint에 연결합니다. 승인 자료가 연결되지 않으면 P07은 명시적인 `no_results`를 반환하고, 실행은 부분 결과로 표시됩니다.
- HTTP mode는 원문 질의를 TrendQuery로 구조화한 뒤 동일 request ID로 Trend `/runs`에 전달합니다. 합성 검증에서는 `VOGUE_QUERY_STRUCTURING_MODE=synthetic_fixture`를 사용합니다. 구조화 실패는 agent 호출 전에 422로 중단합니다.
- Stock `/runs`는 Trend 1.1 source version과 hash를 provenance로 기록하고 내부 요청 변환본을 별도로 처리합니다. Stock 응답 payload는 schema 1.0입니다.
- Sales Market HTTP runtime producer ID와 JSON Schema는 `sales-market-agent`로 정렬했습니다. result 1.0만 downstream으로 보내며 sibling artifacts는 전달하지 않습니다.
- P06 생성기 이관 상세와 남은 runtime 설정은 [`docs/p06-planning-generator-migration.md`](docs/p06-planning-generator-migration.md)에 기록했습니다. P06의 legacy planning/variant 기능은 이전 버전 호환을 위해 남아 있습니다.
- (질의 구조화, 2026-11-11 FW-1) 원문 → `StructuredQuery` → `TrendQuery` 변환은 `src/vogue_orchestrator/query/`에서 처리합니다. HTTP v0.1 실행은 Trend `/runs`에 구조화 결과를 보내고, 명시적으로 요청한 시즌·카테고리만 PlanningDraft 요청 context로 전달합니다.
- `partial`과 `no_results`는 다음 단계에 전달하고, `error` 및 계약 검증 실패는 기본적으로 이후 호출을 멈춥니다. 앞선 성공 결과는 실행 기록에 남습니다.
- 자동 재시도와 영속 중복 제거는 기본 비활성입니다. 선택 가능한 idempotency key 생성 함수만 제공합니다. 재시도·중복 저장은 운영 합의 후 외부 구현을 주입해야 합니다.
- 실행 trace는 UI 응답에 포함되며, 전체 `error` 실행과 P06에서 요청한 상품 이미지·기획 보드가 실패한 실행은 저장하지 않습니다. UI 실행의 최근 20건은 저장소 루트의 `.local/runs/`에 로컬 보관됩니다. `.local/latest-run.json`에서 마지막 저장 실행을 페이지 로드 때 복원하고, 화면의 저장 기록 선택 메뉴에서 과거 실행을 불러오거나 개별 삭제할 수 있습니다. 이 파일은 Git에서 제외되며 UI에서 전체 기록도 삭제할 수 있습니다. Legacy mixed mode에는 공통 HTTP 인증 전달이 없습니다. HTTP v0.1 client는 Bearer token 전달, 3초 connect timeout, 60초 request timeout을 적용하며 자동 재시도는 하지 않습니다. 승인된 외부 자료원은 이 저장소에서 연결하지 않습니다.

2026-11-10 합성 local E2E에서 네 canonical route를 실제 loopback TCP로 호출하고 Project_08 orchestration 응답까지 확인했습니다. Trend·Stock·Sales Market은 유효한 `no_results`를 유지했고 New Product는 `partial` MD 검토 초안을 반환했습니다. HTTP 경로는 통과했지만 business-result 성공이나 운영 readiness를 뜻하지 않습니다. 실제 서비스 readiness 점검에서 Neo4j·Stock DB·Sales Market 승인 자료원·Project_08 URL/LLM 설정이 없어 real-data E2E는 준비되지 않은 것으로 확인했습니다. 세부 결과와 다음 준비 항목은 [`docs/integration-validation.md`](docs/integration-validation.md)에 있습니다.

## 공통 HTTP v0.1 어댑터 (opt-in)

`POST /v1/agents/{route_agent_id}/runs` 호출은 합의한 JSON envelope를 보내며, 전체 응답을 기록하고 producer `result`만 downstream에 전달합니다. UI의 `에이전트 API로 분석` 옵션은 Trend, Stock 호출 후 P08에서 PlanningDraft를 만들고 P07 리뷰 endpoint를 한 번 호출합니다. 질의 구조화는 설정된 LLM을 사용합니다. 텍스트·상품 이미지 provider 설정은 P08 환경을 사용합니다.

```python
from vogue_orchestrator.connectors import build_v01_http_orchestrator

runner = build_v01_http_orchestrator()  # 네 agent URL 환경변수 필요
result = runner.run("synthetic query", request_id="synthetic-req-001",
                    trend_query={"request_id": "synthetic-req-001",
                                 "original_query": "synthetic query", "requested": {}})
```

환경변수와 비밀 없는 local URL 예시는 [`configs/agent-http.env.example`](configs/agent-http.env.example)에 있습니다. 네 canonical `/runs` route는 모두 같은 `VOGUE_AGENT_SERVICE_BEARER_TOKEN`을 검증합니다. 운영은 HTTPS가 필요하며 connect timeout 3초, request timeout 60초, 자동 재시도 없음입니다. loopback HTTP 개발은 `VOGUE_AGENT_HTTP_DEV_MODE=1`일 때만 허용합니다.

## 실행·합성 검증

### 로컬 서버 한 번에 재시작

UI 상단 분석 방식 옆의 **서버 재시작** 버튼을 누르면 Trend, Stock, Sales Market, Project_06, Orchestrator를 다시 시작하고 상태 확인 후 화면을 새로고침합니다. 터미널에서는 `./.venv/bin/python scripts/local_servers.py restart`를 실행할 수도 있습니다. 상태 확인은 `./.venv/bin/python scripts/local_servers.py status`, 종료는 `./.venv/bin/python scripts/local_servers.py stop`을 사용합니다. 시작 로그와 PID는 `.local/services/`에 저장됩니다. 이 실행기는 현재 저장소의 에이전트 프로세스만 종료하며, 다른 프로그램이 해당 포트를 사용하면 종료하지 않고 안내합니다.

외부 의존성 없이:

```bash
PYTHONPATH=src python3 app.py
# 브라우저에서 http://127.0.0.1:8765 열기
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 examples/synthetic_demo.py
```

### Docker로 로컬 preview 실행

Docker Compose는 Python 의존성 설치 없이 합성 preview UI를 빌드하고 실행합니다. 컨테이너는 non-root/read-only로 실행되며 host port는 loopback에만 공개됩니다. 실행 기록은 `run-data` named volume에 저장되어 컨테이너를 다시 만들어도 유지됩니다.

```bash
docker compose up --build
# http://127.0.0.1:8765
docker compose down
```

Health endpoint는 `http://127.0.0.1:8765/api/health`입니다. 이 컨테이너는 기본 합성 모드이며 agent 서비스나 실제 자료원에 연결하지 않습니다.

Windows에서는 `python3` 대신 `py`를 쓰고, PowerShell에서는 `$env:PYTHONPATH="src"`로 경로를 지정합니다.

**질의 구조화**는 UI 실행에서 기본 수행됩니다. HTTP v0.1 실행에서는 구조화된 TrendQuery가 Trend 단계로 전달됩니다. 오프라인 검증은 `VOGUE_QUERY_STRUCTURING_MODE=synthetic_fixture`를 사용하고 실제 LLM 설정과 분리합니다.

UI 기본값은 `에이전트 API로 분석`이며, `미리보기 · 샘플 결과`는 실제 agent endpoint나 자료원을 호출하지 않습니다. `기존 연결로 분석`은 기존 adapter 조합이며 `에이전트 API로 분석`은 네 HTTP route를 순차 호출하고 이미지 생성을 요청합니다. UI에서 실행한 결과는 위 로컬 기록에 저장됩니다. 브라우저 밖에서 실행한 결과는 자동으로 기록되지 않습니다. 요약만 저장된 과거 기록은 전체 단계 응답과 초안 내용을 복원할 수 없습니다.

UI의 **CONNECTED AGENTS** 모드는 설정이 완료됐을 때만 실행합니다. `/api/connections`는 환경 설정만 보여 주며 페이지 로드 시 agent health/API endpoint를 호출하지 않습니다. 아래는 legacy 혼합 연결 설정입니다.

```bash
VOGUE_TREND_ADAPTER=my_trend_adapter:run
VOGUE_STOCK_AGENT_URL=http://127.0.0.1:8002
VOGUE_PROJECT07_ROOT=/path/to/project_07
VOGUE_PROJECT06_URL=http://127.0.0.1:8767
```

- legacy trend adapter callable 입력은 `{request_id, query}`이고 출력은 원본 `TrendAnalysisResult`여야 합니다. Canonical HTTP mode는 query-structuring 결과를 Trend route input으로 보냅니다.
- legacy stock adapter는 configured base URL의 `/v1/inventory-products`를 호출합니다. stock-agent가 LLM을 사용하도록 설정돼 있으면 trend 결과가 그 LLM 제공자에 전달될 수 있습니다. UI는 CONNECTED 실행 전에 수신 endpoint를 보여 주고 확인 checkbox를 요구합니다.
- sales-market-agent는 지정한 checkout의 `MarketResearchAgent`를 in-process 호출합니다. 기본 adapter는 Project_07의 내장 `configured_agent()`를 사용해 읽기 전용 H&M R2 과거자료와 합성 fixture를 연결합니다. `VOGUE_PROJECT07_SOURCE_ADAPTER`를 지정하면 `research(context)` source factory `module:function`으로 이를 대체할 수 있습니다. H&M 사용 권한 flag는 필요하지 않습니다. Project_07 기본 source는 cloud R2를 요청 시 읽으므로 R2 접근·스키마 상태는 연결 상태에서 확인되지 않습니다.
- 이 설정은 기존 혼합 CONNECTED 모드입니다. canonical HTTP v0.1 mode에는 별도 URL 변수 네 개가 필요합니다. Project_06의 local `/runs` 기본 포트는 8767, Project_07은 8766, Stock은 8002, Trend는 8768이며 Project_08 UI는 8765입니다.
- 혼합 연결은 기존 endpoint를 사용하며, HTTP v0.1 timeout/retry 규칙은 별도 v0.1 client에 적용됩니다. 연결은 실제 요청 때만 실행됩니다.

이 혼합 연결 방식은 저장소의 legacy 인터페이스를 사용합니다. `examples/synthetic_local_integration.py`는 legacy mixed 검증이며 canonical HTTP E2E와는 별도입니다. 네 route와 전체 orchestration 검증은 [`docs/integration-validation.md`](docs/integration-validation.md)에 기록했습니다.

연결 상태는 `/api/connections`에서 확인합니다. `connection_modes.mixed_connectors`와 `connection_modes.agent_http_v01`을 분리해 표시합니다. URL 설정, endpoint health, schema compatibility, 자료 권한, 운영 승인은 별개이며 이 endpoint는 remote health를 호출하지 않습니다. 실행 E2E가 성공해도 `ready`는 운영 승인을 뜻하지 않습니다.

다른 로컬 sibling checkout이 있을 때 synthetic trend/stock fixture와 실제 sales-market-agent 코드, new-product-agent HTTP handler를 연결하는 검증:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 examples/synthetic_local_integration.py
```

이 스크립트는 legacy mixed 경로를 검증합니다. v0.1 HTTP 결과와 현재 경계는 [`docs/integration-validation.md`](docs/integration-validation.md)에 기록합니다.

기본 데모와 합성 검증은 fixture만 사용하며 실제 원천에 연결하지 않습니다. 혼합 연결 모드의 설정 여부와 v0.1 route의 호환성/health/운영 승인은 별도로 표시됩니다.

## 질의 구조화 모듈 (LLM 호출 전 단계)

사용자 원문 질의를 trend-analysis-agent의 검색 입력 `TrendQuery`로 바꾸는 모듈입니다. 모듈 자체는 네트워크·LLM 호출과 키가 없고, 실제 LLM 호출은 모듈 밖 `llm_provider.py`가 맡습니다. UI 서버(`server.py`)만 이 모듈을 함수로 호출하며, 코어(core·connectors·demo)와는 서로 import하지 않습니다. 이 모듈만 pydantic이 필요합니다(optional extra `query`). 코어와 기존 테스트는 계속 표준 라이브러리만으로 실행되고, pydantic이 없으면 질의 테스트는 skip됩니다. 다음 출력 계약 `StructuredQuery` 1.0(이 저장소 소유)은 **2026-11-11 확정**되었고(받는 Agent용 요약: [`contracts/structured-query.md`](contracts/structured-query.md) §0), 이를 만드는 경로(`query/structured.py` `structure_query_v2`, 프롬프트 v6)도 구현되어 있습니다. 화면 서버(`server.py`)도 2026-11-11부터 이 경로를 쓰고, trend가 받을 입력은 첫 품목·첫 시즌만 담은 `TrendQuery`로 변환합니다(`query/to_trend.py`). HTTP v0.1 실행에서는 이 `TrendQuery`가 Trend로 실제 전송되고(위 HTTP mode 설명), demo·connected 모드에서는 미리보기입니다. (팀원 정리, `main`) UI는 이 모듈을 기본 실행하고, HTTP v0.1 mode는 결과를 Trend route에 전달합니다. `VOGUE_QUERY_STRUCTURING_MODE=synthetic_fixture`는 로컬 합성 E2E 전용 exact-match provider입니다(합성 질의 fixture의 `structured_draft`를 씀). 일반 모드에서는 설정된 LLM provider를 사용합니다.

```bash
pip install -e ".[query]"
PYTHONPATH=src python3 -m unittest discover -s tests -v          # 전체 307 (질의 248 + LLM 호출·UI 연결 17 + 오케스트레이터·팀원). 2026-11-11 기준. 이 PC에서는 test_connectors 2개 실패: configs/README F-7
PYTHONPATH=src python3 examples/query_structuring_demo.py        # 합성 사례 6개 결과
PYTHONPATH=src python3 examples/query_structuring_demo.py --show-prompt "가을 여성 코트"
```

| 폴더 | 내용 |
|---|---|
| [`prompts/`](prompts/README.md) | LLM 프롬프트 원본, 버전별 텍스트 (현행 `v6`, 화면 서버도 사용. `v1`~`v5`는 보관). 실제 전송 텍스트는 각 버전 폴더의 `rendered_system.md` |
| [`configs/`](configs/README.md) | 질의 모듈 설정(`query_structuring.json`)과 저장소 전체 하드코딩 값·종속 관계 목록 |
| [`data/`](data/README.md) | 예상 사용자 질의 30개(dev)·holdout과 기대 출력 (silver), 평가 결과 `eval_runs/` |

사용자와 합의한 결정·작업 규칙·별칭 사전 정책은 [`docs/query-decisions.md`](docs/query-decisions.md)가 기준입니다(먼저 읽기). 확정된 다음 설계는 받는 쪽용 계약 정의서 [`contracts/structured-query.md`](contracts/structured-query.md)(`StructuredQuery` 1.0)와 구현 계획 [`docs/structured-query-plan.md`](docs/structured-query-plan.md)에 있습니다. 구현 내용, 코드 지도, 프롬프트, 가정, 한계와 향후 과제, 다음 할 일은 [`docs/query-structuring.md`](docs/query-structuring.md)에 있고, 설계 배경은 [`docs/query-preprocessing.md`](docs/query-preprocessing.md)에 있습니다.

## 미결정 및 필요한 입력

- 운영 HTTPS 주소·service identity·token 발급/회전 및 network/readiness 승인
- 실제 자료원 권한과 실제 데이터에 대한 별도 검증
- HTTP response size, persistent idempotency, execution retention, audit/redaction 정책
- sales-market-agent 1.3 상세 proposal과 new-product-agent 표시 adapter의 담당자 간 승인. new-product-agent는 현재 payload를 opaque하게 보존하며 관측값 표시를 위해 재집계하지 않습니다.

Notion의 `System Architecture`는 Proposed/unverified, `Project Workflow`와 저장소 협업 가이드도 workflow/계약 관련 내용을 포함하지만 unverified 상태로 반환됐습니다. 해당 문서의 업무 순서와 MD 경계는 참고했고, 미확정 구현 결정을 합의사항으로 취급하지 않았습니다.

## Sequential workflow prototype (P08)

The target UI mode uses `workflow_v01`: Trend → Stock → P06 `/drafts` → direct P07 `/reviews` → integration → one final image. The handler delegates to `Workflow`; it does not generate a Draft itself. `legacy_migrated` (and the historical `agent_http_v01` API alias) retain the old P08 generator for compatibility. Demo/connected modes remain available.

Install the `workflow` and `query` extras for the target API. `POST /v1/orchestrations` accepts `contract_version: vogue-workflow/0.1`, `request_id`, and `input.user_input: {text, images}`; callers may also provide `input.trend_query` and `input.planning_request`. Otherwise the configured query structurer supplies the first item/season search view. `POST /api/workflow/run` serves the same loopback UI flow. The target network builder fails closed until the coordinator marks the matching contract snapshot frozen and producer/consumer validated. Current fixture success is not live readiness.

Upload one PNG/JPEG/WebP (10 MiB maximum) to `POST /v1/input-images` with its MIME and `X-Request-ID`; use the returned private artifact reference with that request ID. Arbitrary URLs, local paths and another request's uploads are rejected. The original bytes are kept; images are reference inputs, not proof of visual analysis. Text is 1–8000 characters; the current UI retains its 1000-character limit. Trend remains a first item/first season text search, with the limitation visible in results.

`.local/workflow` contains private immutable response/request bytes, SQLite checkpoints and all attempt traces. Duplicate requests reuse checkpoints across process restarts. An interrupted call with unknown outcome stops automatically to avoid repeating a paid operation. `POST /v1/orchestrations/image-retries` with `{request_id,retry_key}` retries only the final image; replaying the key or retrying an already generated image reuses the saved result. Prior failed attempts remain stored. Image provider invocation requires `VOGUE_WORKFLOW_IMAGES_ENABLED=1` and a configured key; it is never part of Draft generation. An image failure preserves Draft and Review and returns partial. Oversized upstream transport is explicitly rejected; the bounded prefix artifact is marked `application/vnd.vogue.truncated-response`, never a complete raw response.

The target artifact/run endpoints are loopback only. A distributed deployment still needs an agreed private artifact resolver/mount for P06/P07; the fixture probe injects a reader and does not establish cross-container access. Source HTTP authentication, current data and paid image generation are separate integration checks.

`scripts/local_servers.py` consumes `_workflow/infra/service-manifest.json` (`VOGUE_SERVICE_MANIFEST` overrides its path). It reads Infra's service mapping, ports, health paths, command and Python versions. Host commands bind loopback; container environment addresses are not copied into the host. Per-service `VOGUE_PYTHON_<SERVICE>` overrides select a matching runtime (for example `VOGUE_PYTHON_SALES_MARKET`). Missing or mismatched runtimes stop launch. `start` does not terminate an existing listener. No services are started by tests.

Offline evidence and limitations are recorded in `_workflow/handoffs/orchestrator/report.md` outside this repository. `tests/test_workflow*.py` use disposable stores and fake transports. They exercise order, terminal stops, exact raw/object hashes, concurrency, crash checkpoints, uploads, image retry and legacy error history; they do not call remote services or image providers.
