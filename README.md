# Project_08 · VOGUE Orchestrator

Project_08은 VOGUE 중앙 오케스트레이션 계층의 실행 가능한 로컬 코어입니다. 사용자 요청마다 하나의 `request_id`를 발급해 아래 순서로 어댑터를 호출하고, raw 응답과 단계 상태를 실행 기록에 보존합니다.

```text
사용자 쿼리 → trend-analysis-agent → stock-agent
            → Project_07 판매·시장 조사 → Project_06 기획 초안 → MD 검토
```

## 현재 구현 범위

agent별 현재 구현과 fixture 기반 input/output은 [`docs/agent-io-contracts.md`](docs/agent-io-contracts.md)에, 필드를 채운 공통 HTTP 계약 초안은 [`docs/agent-io-contract-proposal.md`](docs/agent-io-contract-proposal.md)에 정리했습니다. 후자는 담당자 합의 전 제안입니다.

- 동기 순차 runner는 transport-neutral callable 어댑터를 받습니다. 현재 opt-in 연결은 trend의 사용자 callable, stock의 기존 HTTP API, Project_07 Python 인터페이스 직접 호출, Project_06의 기존 HTTP API를 사용합니다. 이는 확인된 저장소 인터페이스를 잇는 로컬 adapter 선택이며, 최종 배포 토폴로지나 공통 transport 결정을 뜻하지 않습니다.
- 응답 원문을 복사·정규화하지 않고 `StageRecord.raw_response`로 보존합니다. request ID, producer ID, producer schema version, 상태 일치 여부는 별도 검증합니다.
- P07에는 요청·trend·stock raw payload를 전달합니다. P07의 `1.3-proposal`을 포함한 제안 schema는 producer schema version으로만 기록하며 공통 계약 버전으로 사용하지 않습니다.
- 현재 P06 prototype 입력 형식에는 P07 raw 결과 객체를 `sales_market_result`로 전달합니다. 제안 envelope가 오면 전용 경계에서 `result`를 unwrap하고 envelope와 내부 메타데이터의 ID/status/producer version 일치를 확인합니다.
- 실제 계약 대조 결과 trend producer는 `TrendAnalysisResult` v1.1에서 `schema_version`을 반환하지만 `agent_id`는 내보내지 않습니다. 이전 v1.0 결과에는 버전 필드가 없을 수 있습니다. runner는 raw 결과와 producer 버전을 그대로 보존하고, ID는 저장소 정체성으로 별도 기록합니다.
- trend 검색은 `TrendQuery`의 정규화 입력을 요구합니다. 원문 → `TrendQuery` 변환은 독립 모듈 `src/vogue_orchestrator/query/`에 LLM 호출 전 단계(모델·프롬프트·조립·검증)까지 구현돼 있으나, LLM 어댑터가 없고 runner에 연결되지 않았습니다(아래 "질의 구조화 모듈"). runner는 P07/P06용 최소 PlanningRequest에 원문 쿼리만 담으며 시즌·카테고리를 추론하지 않습니다. 실제 trend 호출 adapter는 아직 연결할 수 없습니다.
- stock producer의 계약은 `POST /v1/inventory-products`에 TrendAnalysisResult를 그대로 보내는 방식이며 응답에는 `schema_version`, `request_id`, `agent_id`, `status`, `products`, `trend_results`, `evidence`, `errors`가 있습니다. runner는 이 구조를 바꾸지 않고 stock adapter 호출을 주입받습니다. 실제 API는 sample DB/LLM 의존이 있어 여기서 호출하지 않습니다.
- Project_07 connector는 CLI를 subprocess로 부르지 않고 `MarketResearchAgent`를 in-process 호출하므로 trend 입력을 원본 그대로 넘깁니다. P07의 별도 CLI는 PlanningRequest에 `schema_version: 1.0`을 요구합니다.
- 현재 Project_06 endpoint는 planning request, trend, stock 입력의 schema version이 같아야 합니다. orchestrator가 만드는 planning request는 `1.0`, stock 결과도 `1.0`이지만 최신 trend 결과는 `1.1`이므로 P06 단계에서 멈춥니다. 버전을 바꿔 끼우지 않았으며, P06의 교차 버전 허용 또는 담당자 합의된 adapter mapping이 필요합니다.
- `partial`과 `no_results`는 다음 단계에 전달하고, `error` 및 계약 검증 실패는 기본적으로 이후 호출을 멈춥니다. 앞선 성공 결과는 실행 기록에 남습니다.
- 자동 재시도와 영속 중복 제거는 기본 비활성입니다. 선택 가능한 idempotency key 생성 함수만 제공합니다. 재시도·중복 저장은 운영 합의 후 외부 구현을 주입해야 합니다.
- 로그, 저장, 인증 전달, 외부 자료원 연결, timeout 값은 포함하지 않습니다.

`vogue-handoff/0.1-proposal`은 Notion 검토 상태의 envelope 제안입니다. 이 저장소도 이를 확정 계약으로 선언하지 않습니다. 현재 검증은 주입된 합성 stage adapter로 수행하며 trend와 stock 서비스에 실제 연결된 것은 아닙니다.

## 공통 HTTP envelope 제안 어댑터 (실험적 opt-in)

사용자 논의안을 반영해 `POST /v1/agents/{agent_id}/runs`와 JSON body envelope를 사용하는 별도 클라이언트를 추가했습니다. 요청에서는 agent별 payload를 `input` 아래 전달하고, 응답의 `result` 원문은 실행 기록에 envelope 그대로 보존하며 다음 단계에는 원래 result body를 넘깁니다. 외부 agent가 endpoint를 아직 제공하지 않아 기본 CONNECTED 모드에는 적용되지 않으며, 담당자 승인된 계약도 아닙니다.

```python
from vogue_orchestrator.connectors import build_proposal_http_orchestrator

runner = build_proposal_http_orchestrator()  # 네 agent URL 환경변수 필요
result = runner.run("합성 쿼리", request_id="synthetic-req-001")
```

필요한 환경변수는 `VOGUE_TREND_AGENT_URL`, `VOGUE_STOCK_AGENT_URL`, `VOGUE_PROJECT07_AGENT_URL`, `VOGUE_PROJECT06_AGENT_URL`입니다. 이 모드는 각 endpoint가 제안 envelope를 실제로 구현한 뒤에만 실행 가능합니다. 도메인 응답의 `error`는 상태와 오류를 그대로 둔 채 다음 HTTP 단계를 시도하며, 전체 결과도 `error`로 표시합니다. HTTP transport/검증 오류는 성공으로 바꾸지 않고 해당 단계에서 중단합니다. 인증, 운영 timeout/retry, 배포 주소는 아직 미정이며 이 클라이언트는 운영 준비 완료를 뜻하지 않습니다.

## 실행·합성 검증

외부 의존성 없이:

```bash
PYTHONPATH=src python3 app.py
# 브라우저에서 http://127.0.0.1:8765 열기
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 examples/synthetic_demo.py
```

Windows에서는 `python3` 대신 `py`를 쓰고, PowerShell에서는 `$env:PYTHONPATH="src"`로 경로를 지정합니다.

**질의 구조화(00 단계)를 UI에서 실행하려면**: `pip install -e ".[query]"`와 LLM SDK(`pip install openai` 또는 `pip install anthropic`)를 설치하고, `.env`에 `LLM_MODEL`·`LLM_API_KEY`를 둡니다. 화면의 "00 질의 구조화" 동의를 체크하면 질의가 외부 LLM으로 전송되어 구조화되고, 결과가 실행 기록 맨 위 카드와 `/api/run` 응답의 `query_structuring`에 나옵니다. trend 단계로는 아직 전달하지 않습니다(미리보기만). 2026-11-10 실제 호출 확인(openai `gpt-4o-mini`). 세부: [docs/query-structuring.md §13](docs/query-structuring.md).

UI는 로컬 loopback에서만 열리는 미리보기입니다. 기본 `SYNTHETIC PREVIEW`는 합성 adapter로 네 단계를 실행합니다. 저장소 인터페이스가 확인된 단계는 별도 `CONNECTED AGENTS` 모드에서 opt-in으로 호출할 수 있습니다. 결과는 저장하지 않습니다.

UI의 **CONNECTED AGENTS** 모드는 설정이 완료됐을 때만 실행합니다. `/api/connections`는 환경 설정만 보여 주며 페이지 로드 시 agent health/API endpoint를 호출하지 않습니다. 현재 지원 연결 방식:

```bash
VOGUE_TREND_ADAPTER=my_trend_adapter:run
VOGUE_STOCK_AGENT_URL=http://127.0.0.1:8002
VOGUE_PROJECT07_ROOT=/path/to/project_07
VOGUE_PROJECT07_SOURCE_ADAPTER=my_market_sources:build_source
VOGUE_PROJECT06_URL=http://127.0.0.1:8766
```

- trend adapter callable 입력은 `{request_id, query}`이고 출력은 원본 `TrendAnalysisResult`여야 합니다. 실제 trend repository에는 사용자 query parser와 service API가 없어 현재 연결은 호출자가 제공하는 adapter에 달려 있습니다. 독립 query structuring 모듈도 아직 LLM provider 및 runner와 연결되지 않았습니다.
- stock adapter는 configured base URL의 `/v1/inventory-products`를 호출합니다. stock-agent가 LLM을 사용하도록 설정돼 있으면 trend 결과가 그 LLM 제공자에 전달될 수 있습니다. UI는 CONNECTED 실행 전에 수신 endpoint를 보여 주고 확인 checkbox를 요구합니다.
- Project_07은 지정한 checkout의 `MarketResearchAgent`를 in-process 호출합니다. `VOGUE_PROJECT07_SOURCE_ADAPTER`는 `research(context)`를 구현한 source factory `module:function`이며, 운영 자료원 설정은 이 factory에서 명시적으로 구성해야 합니다. 설정되지 않으면 연결 상태가 준비 완료가 아니며, 기본 `NoConfiguredSources`는 `no_results`를 반환합니다.
- Project_06은 configured base URL의 `/api/planning-draft`를 호출합니다. Project_06 기본 8765는 이 UI 포트와 겹치므로 별도 포트(예: 8766)로 실행해야 합니다.
- 자동 timeout, retry, 지속성은 설정하지 않습니다. 연결은 실제 요청 때만 실행됩니다.

이 연결 방식은 저장소에서 확인된 인터페이스에 맞춘 것입니다. stock과 Project_06은 기존 HTTP endpoint를 사용하고, Project_07은 현재 Python `MarketResearchAgent` 진입점을 같은 프로세스에서 부릅니다. trend는 아직 API가 없어 callable adapter가 필요합니다. 각 저장소를 별도 서비스로 배포할지, transport를 표준화할지는 팀 결정이 남아 있습니다.

연결 상태만 확인하려면 `http://127.0.0.1:8765/api/connections`를 엽니다. 이 endpoint는 env 설정과 Project_07 경로만 검사하며 service health 요청을 보내지 않습니다. CONNECTED 모드는 필수 adapter/endpoint가 모두 설정된 경우에만 시작하고, 화면에서 보이는 대상 확인 checkbox를 누른 뒤 실행합니다.

다른 로컬 sibling checkout이 있을 때 synthetic trend/stock fixture와 실제 Project_07 코드, Project_06 HTTP handler를 연결하는 검증:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 examples/synthetic_local_integration.py
```

결과와 저장소별 호환 판정은 [`docs/integration-validation.md`](docs/integration-validation.md)에 기록합니다. 이 검증은 trend/stock 서비스 자체를 호출하지 않으며, Project_07은 명시적 합성 source만 사용합니다.

기본 데모와 합성 검증은 fixture만 사용하며 실제 원천에 연결하지 않습니다. 연결 모드는 trend/stock/P06 endpoint와 명시 설정된 Project_07 source factory가 모두 있어야 준비 완료로 표시됩니다.

## 질의 구조화 모듈 (LLM 호출 전 단계)

사용자 원문 질의를 trend-analysis-agent의 검색 입력 `TrendQuery`로 바꾸는 모듈입니다. 모듈 자체는 네트워크·LLM 호출과 키가 없고, 실제 LLM 호출은 모듈 밖 `llm_provider.py`가 맡습니다. UI 서버(`server.py`)만 이 모듈을 함수로 호출하며, 코어(core·connectors·demo)와는 서로 import하지 않습니다. 이 모듈만 pydantic이 필요합니다(optional extra `query`). 코어와 기존 테스트는 계속 표준 라이브러리만으로 실행되고, pydantic이 없으면 질의 테스트는 skip됩니다. 다음 출력 계약 `StructuredQuery` 1.0(이 저장소 소유) 모델, 값 확정 코드, 프롬프트 v2는 만들어졌고 실행 흐름 연결은 다음 단계입니다.

```bash
pip install -e ".[query]"
PYTHONPATH=src python3 -m unittest discover -s tests -v          # 전체 211 (질의 176 + LLM 호출·UI 연결 15). 이 PC에서는 test_connectors 1개 실패: configs/README F-7
PYTHONPATH=src python3 examples/query_structuring_demo.py        # 합성 사례 6개 결과
PYTHONPATH=src python3 examples/query_structuring_demo.py --show-prompt "가을 여성 코트"
```

| 폴더 | 내용 |
|---|---|
| [`prompts/`](prompts/README.md) | LLM 프롬프트 원본, 버전별 텍스트 (실행 버전 `v1`, 다음 버전 `v2` 작성됨). 실제 전송 텍스트는 각 버전 폴더의 `rendered_system.md` |
| [`configs/`](configs/README.md) | 질의 모듈 설정(`query_structuring.json`)과 저장소 전체 하드코딩 값·종속 관계 목록 |
| [`data/`](data/README.md) | 예상 사용자 질의 30개와 기대 출력 (silver, 사용자 확인 전) |

사용자와 합의한 결정·작업 규칙·별칭 사전 정책은 [`docs/query-decisions.md`](docs/query-decisions.md)가 기준입니다(먼저 읽기). 확정된 다음 설계는 받는 쪽용 계약 정의서 [`docs/contracts/structured-query.md`](docs/contracts/structured-query.md)(`StructuredQuery` 1.0)와 구현 계획 [`docs/structured-query-plan.md`](docs/structured-query-plan.md)에 있습니다. 구현 내용, 코드 지도, 프롬프트, 가정, 한계와 향후 과제, 다음 할 일은 [`docs/query-structuring.md`](docs/query-structuring.md)에 있고, 설계 배경은 [`docs/query-preprocessing.md`](docs/query-preprocessing.md)에 있습니다.

## 미결정 및 필요한 입력

- trend-analysis-agent와 stock-agent의 sanitized response fixture 및 담당자 승인 mapping
- 중앙 런타임의 배치, HTTP/queue transport, 인증 문맥 전달
- 오류 이후 독립 단계 실행 여부, timeout·retry 한도, 멱등 저장소와 보존 기간
- `vogue-handoff/0.1-proposal` 채택 여부 및 버전 관리 책임
- P07 1.3 상세 proposal과 P06 표시 adapter의 담당자 간 승인. P06은 현재 payload를 opaque하게 보존하며 관측값 표시를 위해 재집계하지 않습니다.

Notion의 `System Architecture`는 Proposed/unverified, `Project Workflow`와 저장소 협업 가이드도 workflow/계약 관련 내용을 포함하지만 unverified 상태로 반환됐습니다. 해당 문서의 업무 순서와 MD 경계는 참고했고, 미확정 구현 결정을 합의사항으로 취급하지 않았습니다.
