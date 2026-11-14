# VOGUE Orchestrator (Project 08)

Project 08 (P08) is the coordinator and UI for the VOGUE product-planning workflow. It structures the user's request, calls the producer agents in sequence, validates and records their responses, then presents one integrated result. Agents do not call one another directly.

```text
사용자 요청(텍스트·이미지)
  → 질의 구조화
  → Trend Agent
  → Stock Agent
  → P06 New Product Agent: Draft와 상품·착장 시각 자료 생성
  → P06 review bridge: Draft를 검증하고 P07용 허용 필드 projection 반환
  → P08이 projection 검증 후 P07 Sales Market Agent `/reviews` 호출
  → 결과·근거 통합
  → Planning Sheet 생성
  → 검증된 응답과 실행 기록 반환
```

## 구성

- `src/vogue_orchestrator/server.py`: 정적 UI와 HTTP 진입점
- `src/vogue_orchestrator/workflow_http.py`: 요청 검증, 에이전트 URL 설정, workflow API
- `src/vogue_orchestrator/workflow.py`: 순차 실행 상태 머신과 단계별 실패 처리
- `src/vogue_orchestrator/workflow_store.py`: 요청·응답 아티팩트, 해시, SQLite 체크포인트와 실행 기록
- `src/vogue_orchestrator/workflow_contract.py`, `contracts/workflow/`: 요청·응답 계약 검증과 P08이 소유한 schema snapshot
- `src/vogue_orchestrator/query/`, `prompts/query_structuring/`: 사용자 질의를 구조화해 Trend 검색 입력으로 변환
- `src/vogue_orchestrator/planning_sheet.py`: 검증된 결과와 P06 시각 자료를 Planning Sheet PNG로 구성
- `web/`: 단일 workflow API를 사용하는 UI
- `deploy/`: Compose 배포, stub 기반 E2E, 사전 점검과 런처

현재 실행 구조는 [`docs/current-state.md`](docs/current-state.md), 호출 계약은 [`contracts/workflow/workflow-contract.md`](contracts/workflow/workflow-contract.md)에서 확인하세요. 계약 baseline은 현재 `proposed` 상태이며, producer/consumer 독립 검증이 끝난 frozen 계약으로 취급하지 않습니다.

## 로컬 UI

Docker 실행 진입점은 아래 명령입니다.

```bash
deploy/bin/vogue stub
```

stub UI 주소는 `http://127.0.0.1:18765`입니다. rehearsal/live는 `http://127.0.0.1:8765`를 사용합니다. 종료 방법과 프로필 차이는 [`deploy/README.md`](deploy/README.md)를 참고하세요. UI는 `POST /api/workflow/run`으로 실행하며 health 경로는 `GET /api/health`입니다.

**현재 통합 상태:** P08 Compose에는 P07 healthcheck가 추가되어 있지만, 확인된 P07 `main`의 기본 컨테이너 실행은 HTTP 서버를 시작하지 않습니다. P07 서비스 진입점·bind 변경이 적용되기 전에는 전체 Docker workflow가 준비 상태에 도달하지 못합니다. 최신 상태와 담당 저장소별 조치는 [`deploy/README.md`](deploy/README.md)를 참고하세요.

## API

- `POST /api/workflow/run`: UI와 같은 계약 기반 실행
- `POST /v1/orchestrations`: 외부 호출용 `vogue-workflow/0.2` envelope 실행
- `POST /v1/input-images`: 요청 이미지 업로드
- `POST /v1/orchestrations/image-retries`: 최종 Planning Sheet 이미지 재시도
- `GET /api/workflow/runs` 및 `/api/workflow/runs/{request_id}`: 실행 기록 조회
- `GET|POST /api/workflow/runs/{request_id}/md-decision`: MD 결정 기록
- `GET /api/workflow/runs/{request_id}/planning-sheet.png`: 완료된 실행의 Planning Sheet
- `GET /api/system/status`: 비밀 값을 노출하지 않는 연결·계약 상태 확인

Workflow와 artifact 경로는 신뢰된 로컬 요청만 받습니다. 운영 호출은 서비스 bearer token 및 배포 설정이 필요합니다. 환경변수와 네트워크 정책은 [`docs/workflow-integration-conventions.md`](docs/workflow-integration-conventions.md)를 따릅니다.

## 계약과 실행 기록

워크플로 schema bytes는 `contracts/workflow/snapshot.json`으로 검사하며, `baseline.json`에는 계약 상태와 검증 근거가 기록됩니다. 현재 상태는 `proposed`입니다. 각 Agent의 native 계약은 해당 저장소가 소유합니다. P08 계약 목록과 검증 도구는 [`contracts/README.md`](contracts/README.md)를 참고하세요.

기본 실행 기록 경로는 `.local/workflow`이며 설정으로 변경할 수 있습니다. 응답 원문과 아티팩트는 해시를 확인해 보관하고, 단계 checkpoint는 중단된 작업의 유료 호출을 임의 재실행하지 않도록 합니다. Trend 검색에는 현재 첫 품목과 첫 시즌만 전달되며, 제한 사항은 응답에 표시됩니다.

## 개발

```bash
pip install -e '.[workflow,query]'
PYTHONPATH=src python3 app.py
```

서버는 `127.0.0.1:8765`에서 UI와 API를 제공합니다. 실제 query-structuring LLM 호출에는 사용할 provider extra(`.[llm-openai]` 또는 `.[llm-anthropic]`)와 `LLM_MODEL`, `LLM_API_KEY` 설정이 추가로 필요합니다. 계약 및 workflow 변경을 검증하려면 다음을 실행합니다.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --test web/tests/model.test.mjs
```

외부 에이전트에 연결하지 않는 합성 검증과 실제 서비스 연결 검증은 별개입니다. 현재 구성과 검증 범위는 [`docs/current-state.md`](docs/current-state.md)를 참고하세요.
