# 워크플로 계약 (workflow-contract)

요청·응답의 기준은 실행 코드, [`baseline.json`](baseline.json), [`snapshot.json`](snapshot.json)입니다.
`baseline.json`의 상태가 `frozen`일 때만 워크플로가 실행됩니다(`proposed`이면 개발 모드에서만 실행하고 결과에
그 사실을 표시합니다). 동결과 갱신은 `scripts/freeze_workflow_contract.py`로만 합니다.

식별자 이름에는 버전을 넣지 않습니다. 계약 버전은 응답의 `schema_version`(`"1.0.0"`) 한 곳에만 둡니다.

## 호출 흐름

모든 단계는 orchestrator가 같은 `request_id`로 차례로 호출합니다. novelty와 research는 서로 부르지 않습니다.

| 순서 | 호출 | 전송(`contract_version`) | 응답 `schema_version` |
| --- | --- | --- | --- |
| 1 | orchestrator → trend `POST /v1/agents/trend/runs` (입력: TrendQuery) | `trend-handoff` | `1.0.0` |
| 2 | orchestrator → inventory `POST /v1/agents/inventory/runs` (입력: 받은 trend 결과 그대로) | `inventory-handoff` | `1.0.0` |
| 3 | orchestrator → novelty `POST /v1/agents/novelty/runs` (입력: 기획 요청 + trend·inventory 결과의 등록된 투영) | `novelty-handoff` | `1.0.0` |
| 4 | orchestrator → research `POST /v1/agents/research/reviews` (입력: 허용 상품 필드 + 필드별 출처) | `research-handoff` | `1.0.0` |
| 5 | orchestrator 내부 통합과 기획서 생성 → UI | `fashion-planner-workflow` | `1.0.0` (integrated result) |

novelty는 상품·착장 이미지를 만들 수 있고, orchestrator는 검증된 실행 기록으로 기획서 PNG를 그립니다. 기획서를
그릴 때 이미지 모델은 부르지 않습니다.

## 보존과 연결의 한계

- orchestrator는 서비스 응답 원문, request ID, 응답 해시(`python-json-sort`), 단계 기록, 검증된 산출물 참조를
  저장합니다. 원문의 status, 오류, 근거, `missing_data`, limitations를 성공으로 바꾸거나 임의로 채우지 않습니다.
- research에는 novelty 초안 전체가 아니라 초안의 `product_profile`에서 뽑은 허용 필드와 필드별 출처(`kind`,
  `source_ref` = `novelty.draft.product_profile.<field>`, `evidence_ids`)만 보냅니다. research 응답은 초안 전체나
  초안 응답 해시를 증명하지 않으므로, 통합 결과도 이를 초안 전체의 증명으로 표현하지 않습니다.
- 산출물은 소유자와 허용 네임스페이스(`artifact://novelty/…`, `artifact://research/…`)를 확인하고, 실제 바이트의
  크기와 SHA-256을 검증한 뒤 씁니다. orchestrator가 만든 blob은 `artifact://orchestrator/blobs/<sha256>`입니다.
- `ok`, `partial`, `no_results`, `error`는 각 서비스의 의미와 `workflow_contract.py`의 단계 규칙을 따릅니다.
  부분 결과와 치명적이지 않은 오류는 보존하고, 계약·식별자·무결성 위반은 뒤 단계 호출을 멈춥니다.

## 계약 산출물

- 호출·통합 JSON Schema: 이 디렉터리의 `*.schema.json`(`$id` = `https://contracts.fashion-planner.invalid/<소유 서비스>/<파일>`)
- 서비스 계약 사본과 출처: [`../agents/SOURCES.md`](../agents/SOURCES.md)
- 호출 행렬: [`source-consumer-matrix.json`](source-consumer-matrix.json)
- 기준 검증기와 합성 픽스처: [`../../tools/workflow_contract/`](../../tools/workflow_contract/)
