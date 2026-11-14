# P08 workflow contract — proposed 0.5.0

현재 요청·응답의 기준은 실행 코드, [`baseline.json`](baseline.json), [`snapshot.json`](snapshot.json)입니다. baseline 상태는 `proposed`이며 producer/consumer 독립 검증을 완료한 frozen 계약으로 간주하지 않습니다. 계약 동결·갱신은 [`contracts README`](../README.md)와 `scripts/freeze_workflow_contract.py` 절차를 따릅니다.

## 현재 호출 흐름

모든 단계는 P08이 같은 `request_id`로 순차 호출합니다. P06과 P07은 서로 직접 호출하지 않습니다.

| 순서 | 호출 | 계약 |
| --- | --- | --- |
| 1 | P08 → Trend `POST /v1/agents/trend-analysis-agent/runs` | `vogue-handoff/0.1`, Trend result `1.1` |
| 2 | P08 → Stock `POST /v1/agents/stock-agent/runs` | `vogue-handoff/0.1`, Trend result body를 입력, Stock result `1.0` |
| 3 | P08 → P06 `POST /v1/agents/new-product-agent/runs` | `vogue-handoff/0.3`, P06 result `1.0` |
| 4 | P08 → P06 review bridge `POST /v1/agents/new-product-agent/sales-market-reviews` | `vogue-sales-market-review/0.1-proposal`; Draft를 검증하고 P07 입력 projection을 반환 |
| 5 | P08 → P07 `POST /v1/agents/sales-market-agent/reviews` | `vogue-sales-market-review/0.2-proposal`, native response `sales-market-review/0.1-internal` |
| 6 | P08 내부 통합·Planning Sheet 생성 → UI | `vogue-workflow/0.2`, 통합 결과 `integrated-result/2.0` |

P06은 상품 및 착장 이미지를 생성할 수 있고, P08은 검증된 실행 결과로 Planning Sheet PNG를 합성합니다. P08의 Planning Sheet 합성은 이미지 모델 호출이 아닙니다.

## 데이터 보존 및 연결 한계

- P08은 Agent 원 응답, request ID, 응답 hash, 단계 기록과 검증된 artifact 참조를 저장합니다. 원 응답의 status, 오류, evidence, `missing_data`, limitations를 성공 상태로 바꾸거나 임의로 채우지 않습니다.
- P07에는 P06 Draft 전체가 아니라 P06 review bridge가 만든 허용 필드 projection만 전달됩니다. P08은 projection 요청과 P06 원 응답을 보존해 연결하지만, P07 응답은 Draft 전체나 Draft response hash를 증명하지 않습니다. 통합 결과에서 이 제한을 완전한 Draft hash attestation으로 표현하지 않습니다.
- artifact는 소유자와 허용 namespace를 확인하고 실제 bytes의 크기·SHA-256을 검증한 뒤 사용합니다. 원문에 없는 provenance나 연구 근거를 추가하지 않습니다.
- `ok`, `partial`, `no_results`, `error`의 집계는 각 producer 의미와 `workflow_contract.py`의 단계 규칙을 따릅니다. 부분 결과와 비치명 오류는 보존하고, 계약·식별자·무결성 위반은 후속 호출을 중단합니다.

## 계약 산출물

- 호출·통합 JSON Schema: 이 디렉터리의 `*.schema.json`
- producer schema 출처 및 fingerprint: [`../agents/SOURCES.md`](../agents/SOURCES.md)
- source/consumer 및 검증 범위: [`source-consumer-matrix.json`](source-consumer-matrix.json)
- 통합 규칙: [`../../docs/workflow-integration-conventions.md`](../../docs/workflow-integration-conventions.md)
- 현재 흐름 개요: [`../../docs/current-state.md`](../../docs/current-state.md)

이 문서는 과거 `/drafts`, P07 `/runs`, full-Draft 직접 전달, 이전 workflow 버전의 설계·마이그레이션 기록을 현재 계약으로 취급하지 않습니다. P06/P07의 계약은 각 소유 저장소와 해당 source fingerprint를 기준으로 확인합니다.
