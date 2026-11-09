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
- trend/stock의 실제 schema fixture가 제공되지 않아 그 필드 매핑은 하지 않습니다. 현재 runner는 공통 응답 최소 메타데이터만 요구합니다.
- `partial`과 `no_results`는 다음 단계에 전달하고, `error` 및 계약 검증 실패는 기본적으로 이후 호출을 멈춥니다. 앞선 성공 결과는 실행 기록에 남습니다.
- 자동 재시도와 영속 중복 제거는 기본 비활성입니다. 선택 가능한 idempotency key 생성 함수만 제공합니다. 재시도·중복 저장은 운영 합의 후 외부 구현을 주입해야 합니다.
- 로그, 저장, 인증 전달, 외부 자료원 연결, timeout 값은 포함하지 않습니다.

`vogue-handoff/0.1-proposal`은 Notion 검토 상태의 envelope 제안입니다. 이 저장소도 이를 확정 계약으로 선언하지 않습니다.

## 실행·합성 검증

외부 의존성 없이:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 examples/synthetic_demo.py
```

합성 fixture만 사용합니다. 실제 트렌드·재고·판매/시장 원천에는 연결하지 않습니다.

## 미결정 및 필요한 입력

- trend-analysis-agent와 stock-agent의 sanitized response fixture 및 담당자 승인 mapping
- 중앙 런타임의 배치, HTTP/queue transport, 인증 문맥 전달
- 오류 이후 독립 단계 실행 여부, timeout·retry 한도, 멱등 저장소와 보존 기간
- `vogue-handoff/0.1-proposal` 채택 여부 및 버전 관리 책임
- P07 1.3 상세 proposal과 P06 표시 adapter의 담당자 간 승인. P06은 현재 payload를 opaque하게 보존하며 관측값 표시를 위해 재집계하지 않습니다.

Notion의 `System Architecture`는 Proposed/unverified, `Project Workflow`와 저장소 협업 가이드도 workflow/계약 관련 내용을 포함하지만 unverified 상태로 반환됐습니다. 해당 문서의 업무 순서와 MD 경계는 참고했고, 미확정 구현 결정을 합의사항으로 취급하지 않았습니다.
