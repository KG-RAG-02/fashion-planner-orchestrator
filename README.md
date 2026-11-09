# Project_08 · VOGUE Orchestrator

Project_08은 VOGUE 중앙 오케스트레이션 계층의 실행 가능한 로컬 코어입니다. 사용자 요청마다 하나의 `request_id`를 발급해 아래 순서로 어댑터를 호출하고, raw 응답과 단계 상태를 실행 기록에 보존합니다.

```text
사용자 쿼리 → trend-analysis-agent → stock-agent
            → Project_07 판매·시장 조사 → Project_06 기획 초안 → MD 검토
```

## 현재 구현 범위

- 동기 callable 기반의 순차 runner와 주입식 단계 어댑터를 제공합니다. 네트워크/API 구현이나 배포 토폴로지는 선택하지 않았습니다.
- 응답 원문을 복사·정규화하지 않고 `StageRecord.raw_response`로 보존합니다. request ID, producer ID, producer schema version, 상태 일치 여부는 별도 검증합니다.
- P07에는 요청·trend·stock raw payload를 전달합니다. P07의 `1.3-proposal`을 포함한 제안 schema는 producer schema version으로만 기록하며 공통 계약 버전으로 사용하지 않습니다.
- 현재 P06 prototype 입력 형식에는 P07 raw 결과 객체를 `sales_market_result`로 전달합니다. 제안 envelope가 오면 전용 경계에서 `result`를 unwrap하고 envelope와 내부 메타데이터의 ID/status/producer version 일치를 확인합니다.
- 실제 계약 대조 결과 trend producer는 `request_id/status/applied_filters/trends/evidence/limitations`를 반환하고 `agent_id/schema_version`과 호출 API는 없습니다. runner는 이를 raw로 보존하고 status를 확인하며, 기록의 producer ID만 저장소 문서에서 확인된 `trend-analysis-agent`로 표시하고 원본 schema version은 미상으로 둡니다.
- trend 검색은 `TrendQuery`의 정규화 입력을 요구하지만 쿼리 파서가 미구현입니다. runner는 P07/P06용 최소 PlanningRequest에 원문 쿼리만 담으며 시즌·카테고리를 추론하지 않습니다. 실제 trend 호출 adapter는 아직 연결할 수 없습니다.
- stock producer의 계약은 `POST /v1/inventory-products`에 TrendAnalysisResult를 그대로 보내는 방식이며 응답에는 `schema_version`, `request_id`, `agent_id`, `status`, `products`, `trend_results`, `evidence`, `errors`가 있습니다. runner는 이 구조를 바꾸지 않고 stock adapter 호출을 주입받습니다. 실제 API는 sample DB/LLM 의존이 있어 여기서 호출하지 않습니다.
- Project_07 CLI는 PlanningRequest와 trend/stock 입력 각각 `schema_version: 1.0`을 요구합니다. trend 원본에는 버전 필드가 없으므로 P07 입력 복사본에만 `1.0`을 추가합니다. StageRecord의 trend raw 응답에는 추가하지 않습니다.
- `partial`과 `no_results`는 다음 단계에 전달하고, `error` 및 계약 검증 실패는 기본적으로 이후 호출을 멈춥니다. 앞선 성공 결과는 실행 기록에 남습니다.
- 자동 재시도와 영속 중복 제거는 기본 비활성입니다. 선택 가능한 idempotency key 생성 함수만 제공합니다. 재시도·중복 저장은 운영 합의 후 외부 구현을 주입해야 합니다.
- 로그, 저장, 인증 전달, 외부 자료원 연결, timeout 값은 포함하지 않습니다.

`vogue-handoff/0.1-proposal`은 Notion 검토 상태의 envelope 제안입니다. 이 저장소도 이를 확정 계약으로 선언하지 않습니다. 현재 검증은 주입된 합성 stage adapter로 수행하며 trend와 stock 서비스에 실제 연결된 것은 아닙니다.

## 실행·합성 검증

외부 의존성 없이:

```bash
PYTHONPATH=src python3 app.py
# 브라우저에서 http://127.0.0.1:8765 열기
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 examples/synthetic_demo.py
```

UI는 로컬 loopback에서만 열리는 미리보기입니다. 기본 `SYNTHETIC PREVIEW`는 합성 adapter로 네 단계를 실행합니다. 저장소 인터페이스가 확인된 단계는 별도 `CONNECTED AGENTS` 모드에서 opt-in으로 호출할 수 있습니다. 결과는 저장하지 않습니다.

UI의 **CONNECTED AGENTS** 모드는 설정이 완료됐을 때만 실행합니다. `/api/connections`는 환경 설정만 보여 주며 페이지 로드 시 agent health/API endpoint를 호출하지 않습니다. 현재 지원 연결 방식:

```bash
VOGUE_TREND_ADAPTER=my_trend_adapter:run
VOGUE_STOCK_AGENT_URL=http://127.0.0.1:8002
VOGUE_PROJECT07_ROOT=/path/to/project_07
VOGUE_PROJECT07_SOURCE_ADAPTER=my_market_sources:build_source
VOGUE_PROJECT06_URL=http://127.0.0.1:8766
```

- trend adapter callable 입력은 `{request_id, query}`이고 출력은 원본 `TrendAnalysisResult`여야 합니다. 실제 저장소에는 query parser/API가 아직 없어 담당 구현이 필요합니다.
- stock adapter는 configured base URL의 `/v1/inventory-products`를 호출합니다. stock-agent가 LLM을 사용하도록 설정돼 있으면 trend 결과가 그 LLM 제공자에 전달될 수 있습니다. UI는 CONNECTED 실행 전에 수신 endpoint를 보여 주고 확인 checkbox를 요구합니다.
- Project_07은 지정한 checkout의 `MarketResearchAgent`를 in-process 호출합니다. `VOGUE_PROJECT07_SOURCE_ADAPTER`는 `research(context)`를 구현한 source factory `module:function`이며, 운영 자료원 설정은 이 factory에서 명시적으로 구성해야 합니다. 설정되지 않으면 연결 상태가 준비 완료가 아니며, 기본 `NoConfiguredSources`는 `no_results`를 반환합니다.
- Project_06은 configured base URL의 `/api/planning-draft`를 호출합니다. Project_06 기본 8765는 이 UI 포트와 겹치므로 별도 포트(예: 8766)로 실행해야 합니다.
- 자동 timeout, retry, 지속성은 설정하지 않습니다. 연결은 실제 요청 때만 실행됩니다.

연결 상태만 확인하려면 `http://127.0.0.1:8765/api/connections`를 엽니다. 이 endpoint는 env 설정과 Project_07 경로만 검사하며 service health 요청을 보내지 않습니다. CONNECTED 모드는 필수 adapter/endpoint가 모두 설정된 경우에만 시작하고, 화면에서 보이는 대상 확인 checkbox를 누른 뒤 실행합니다.

다른 로컬 sibling checkout이 있을 때 실제 Project_07·Project_06 함수에 synthetic payload를 전달하는 검증:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 examples/synthetic_local_integration.py
```

결과와 저장소별 호환 판정은 [`docs/integration-validation.md`](docs/integration-validation.md)에 기록합니다. 이 검증은 trend/stock 서비스 자체를 호출하지 않습니다.

기본 데모와 합성 검증은 fixture만 사용하며 실제 원천에 연결하지 않습니다. 연결 모드는 trend/stock/P06 endpoint와 명시 설정된 Project_07 source factory가 모두 있어야 준비 완료로 표시됩니다.

## 미결정 및 필요한 입력

- trend-analysis-agent와 stock-agent의 sanitized response fixture 및 담당자 승인 mapping
- 중앙 런타임의 배치, HTTP/queue transport, 인증 문맥 전달
- 오류 이후 독립 단계 실행 여부, timeout·retry 한도, 멱등 저장소와 보존 기간
- `vogue-handoff/0.1-proposal` 채택 여부 및 버전 관리 책임
- P07 1.3 상세 proposal과 P06 표시 adapter의 담당자 간 승인. P06은 현재 payload를 opaque하게 보존하며 관측값 표시를 위해 재집계하지 않습니다.

Notion의 `System Architecture`는 Proposed/unverified, `Project Workflow`와 저장소 협업 가이드도 workflow/계약 관련 내용을 포함하지만 unverified 상태로 반환됐습니다. 해당 문서의 업무 순서와 MD 경계는 참고했고, 미확정 구현 결정을 합의사항으로 취급하지 않았습니다.
