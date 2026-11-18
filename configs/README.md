# 설정

이 폴더에는 비밀값이 아닌 설정만 둔다. 키와 토큰은 프로세스 환경변수나 git에서 제외된 `.env`에 둔다.

| 파일 | 내용 |
|---|---|
| [`query_structuring.json`](query_structuring.json) | 질의 구조화: 프롬프트 폴더 이름(`prompts/query_structuring/`), 입력 길이·연도 한도, 지원하지 않는 조건의 이유 문장, 성별 고정 표 |
| [`llm.json`](llm.json) | 질의 구조화·SWOT LLM 호출의 타임아웃, 출력 토큰 한도, 재시도 횟수, temperature |
| [`agent-http.env.example`](agent-http.env.example) | 레포 다섯 개를 loopback에서 따로 띄울 때 쓰는 예시(자동으로 읽지 않는다). Compose는 `deploy/compose.yaml`의 값을 쓴다 |

설정 파일은 import할 때 한 번 읽고 검증한다. 다른 위치의 설정·프롬프트를 쓰려면 `ORCHESTRATOR_QUERY_CONFIG`,
`ORCHESTRATOR_QUERY_PROMPTS_DIR`을 지정한다.

## LLM

| 변수 | 뜻 |
|---|---|
| `LLM_MODEL` | 모델 이름. `claude`로 시작하면 Anthropic, 아니면 OpenAI SDK를 쓴다 |
| `LLM_API_KEY` | API 키. 설정 파일에 두지 않는다 |
| `LLM_PROVIDER` | 선택. `anthropic` 또는 `openai`로 강제 |
| `ORCHESTRATOR_QUERY_STRUCTURING_MODE` | `synthetic_fixture`면 LLM 대신 `examples/fixtures/query/`의 합성 초안을 쓴다(오프라인) |

## 서버

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `ORCHESTRATOR_HOST`, `ORCHESTRATOR_PORT` | `127.0.0.1`, `8765` | 바인드 주소와 포트 |
| `ORCHESTRATOR_ALLOW_REMOTE_BIND` | 없음 | `1`이어야 loopback 밖에 바인드한다 |
| `ORCHESTRATOR_RUNTIME` | `host` | `container`면 정적 파일과 `/api/health`를 뺀 모든 경로에 인바운드 신뢰 검사를 적용한다 |
| `ORCHESTRATOR_TRUSTED_PEER_CIDRS` | 없음 | loopback 밖에서 받을 상대 주소 범위(쉼표 목록) |
| `ORCHESTRATOR_ALLOWED_HOSTS` | `127.0.0.1:<포트>`, `localhost:<포트>` | 받을 `Host` 헤더 |

## 서비스 호출

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `ORCHESTRATOR_TREND_URL`, `ORCHESTRATOR_INVENTORY_URL`, `ORCHESTRATOR_NOVELTY_URL`, `ORCHESTRATOR_RESEARCH_URL` | 없음 | 네 서비스의 기본 URL |
| `FASHION_PLANNER_SERVICE_TOKEN` | 없음 | 다섯 서비스가 함께 쓰는 bearer 토큰 |
| `ORCHESTRATOR_HTTP_PRIVATE_HOSTS` | 없음 | 토큰과 함께 평문 HTTP를 허용할 한 단어 호스트 이름(쉼표 목록) |
| `FASHION_PLANNER_HTTP_DEV_MODE` | 없음 | `1`이면 loopback 평문 HTTP를 허용한다(개발용) |
| `ORCHESTRATOR_WORKFLOW_CONNECT_TIMEOUT_SECONDS` | 3 | 연결 타임아웃 |
| `ORCHESTRATOR_WORKFLOW_READ_TIMEOUT_SECONDS` | 75 | 읽기 타임아웃 |
| `ORCHESTRATOR_WORKFLOW_INVENTORY_READ_TIMEOUT_SECONDS` | 일반 값 | inventory 호출 읽기 타임아웃(Compose 210초) |
| `ORCHESTRATOR_WORKFLOW_RESEARCH_READ_TIMEOUT_SECONDS` | 일반 값 | research 호출 읽기 타임아웃(Compose 210초, research 안의 R2·환율·LLM 호출 포함) |
| `ORCHESTRATOR_WORKFLOW_DRAFT_READ_TIMEOUT_SECONDS` | 일반 값 | novelty 호출 읽기 타임아웃(Compose 600초, 이미지 생성 포함) |
| `ORCHESTRATOR_WORKFLOW_DRAFT_MODE` | `draft_with_visuals` | `draft_with_visuals` 또는 `draft_only` |

## 실행 기록과 계약

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `ORCHESTRATOR_WORKFLOW_STORE_DIR` | `.local/workflow` | 실행 기록 저장소 |
| `ORCHESTRATOR_PEER_ARTIFACT_ROOT` | 없음 | 다른 서비스 산출물을 읽을 읽기 전용 루트(Compose `/peer`) |
| `ORCHESTRATOR_WORKFLOW_CONTRACT_BASELINE` | `contracts/workflow/baseline.json` | 계약 기준선 파일 |
| `ORCHESTRATOR_WORKFLOW_ALLOW_PROPOSED` | 없음 | `1`이면 `proposed` 기준선에서도 실행한다(개발용, 결과에 한계 표시) |

## 6단계 기획서

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `ORCHESTRATOR_WORKFLOW_IMAGES_ENABLED` | 없음 | `1`이어야 6단계를 실행한다 |
| `ORCHESTRATOR_WORKFLOW_IMAGE_PROVIDER` | `planning_sheet` | `planning_sheet`(실행 기록으로 기획서 합성) 또는 `fixture`(결정적 테스트 이미지). 둘 다 이미지 모델을 부르지 않는다 |
| `ORCHESTRATOR_WORKFLOW_IMAGE_RETRY_ENABLED` | `0` | `1`이면 `POST /v1/orchestrations/image-retries`를 받는다 |
| `ORCHESTRATOR_TREND_PHOTO_HOSTS` | 비어 있음 | 기획서에 넣을 기사 사진을 받을 호스트(하위 도메인 포함, 쉼표 목록). 비어 있으면 받지 않는다. 켜는 것은 배포자 책임이고, 사진이 들어간 기획서는 다시 배포하지 않는다([docs/data.md](../docs/data.md#3-기사-사진)) |
| `ORCHESTRATOR_TREND_PHOTOS_ENABLED` | `1` | `0`이면 호스트가 있어도 기사 사진을 받지 않는다 |
| `ORCHESTRATOR_SWOT_ENABLED` | 켜짐 | `0`이면 SWOT·프로모션 초안을 만들지 않는다. 켜져 있어도 LLM 설정이 있어야 만든다 |
| `ORCHESTRATOR_PLANNING_SHEET_FONT`, `ORCHESTRATOR_PLANNING_SHEET_FONT_BOLD` | 자동 탐색 | 기획서 한글 글꼴 파일 경로 |

빈 값(빈 문자열이나 공백)은 설정하지 않은 것으로 본다. 배포 도구가 쓰는 `FASHION_PLANNER_*` 변수는
[deploy/README.md](../deploy/README.md)에 있다.
