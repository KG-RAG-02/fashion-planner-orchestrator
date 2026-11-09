# configs/ — 설정 파일과 하드코딩·종속 관계 목록

조사 기준: 2026-11-09, 브랜치 `feature/orchestrator-core` 작업 트리.

이 폴더에는 두 가지가 있다.
1. **실제로 코드가 읽는 설정 파일**: 지금은 질의 구조화 모듈용 `query_structuring.json` 하나다.
2. **저장소 전체의 하드코딩 값과 종속 관계 목록**: 이 문서 §3~§5다.

기존 오케스트레이터 코드(core, connectors, server, demo, web)의 값은 **목록만 만들고 옮기지 않았다.** "질의 모듈은 기존 구현과 충돌하지 않는다"는 원칙 때문이다. 옮길 후보에는 표시를 해 두었다.

## 1. 설정 파일
| 파일 | 읽는 코드 | 읽는 시점 | 경로를 바꾸는 방법 |
|---|---|---|---|
| [`query_structuring.json`](query_structuring.json) | `src/vogue_orchestrator/query/config.py` | 패키지 import 때 한 번. 형식이 틀리면 `QueryConfigError`로 바로 실패한다 | 환경변수 `VOGUE_QUERY_CONFIG` |

### `query_structuring.json` 키
| 키 | 현재 값 | 쓰는 곳 | 뜻 |
|---|---|---|---|
| `prompt.name` | `query_structuring` | `prompt.py` | `prompts/` 아래 프롬프트 폴더 이름 |
| `prompt.version` | `v1` | `prompt.py` | 사용할 프롬프트 버전 폴더. 결과의 `prompt_version`은 `query-structuring-prompt/v1` |
| `input.max_query_chars` | `1000` | `structure.py` | 질의 최대 길이. 넘으면 `INPUT_INVALID` |
| `draft.year_min` / `draft.year_max` | `2000` / `2100` | `draft.py` | LLM이 준 연도의 허용 범위. 밖이면 `DRAFT_INVALID` |
| `unsupported.reasons` | brand·category·attribute·abstract 4개 | `assemble.py` | `unsupported.reason`에 들어갈 고정 문장. `{major_count}`/`{minor_count}`는 어휘 개수로 채운다 |
| `unsupported.generic_reason` | "현재 검색에서 지원하지 않는 조건" | `assemble.py` | 표에 없는 종류의 사유 |

프롬프트 폴더 위치는 환경변수 `VOGUE_QUERY_PROMPTS_DIR`로 바꾼다. 기본값은 저장소 루트의 `prompts/`다.

## 2. 질의 모듈: 설정으로 옮긴 것과 코드에 남긴 것

### 2.1 이번에 옮긴 값
| 값 | 이전 위치 | 지금 위치 |
|---|---|---|
| 프롬프트 문장 전체(역할, 출력 형식, 규칙 12개, 마무리) | `prompt.py` 문자열 상수 | `prompts/query_structuring/v1/system_template.md` |
| 사용자 메시지 틀 | `prompt.py` | `prompts/query_structuring/v1/user_template.md` |
| few-shot 예시 6개 | `prompt_examples.py` (삭제됨) | `prompts/query_structuring/v1/examples.json` |
| 프롬프트 버전 `0.1` | `prompt.py` | 설정 `prompt.version` (`v1`로 이름 변경, 내용은 같음) |
| 질의 최대 길이 1000 | `structure.py` | 설정 `input.max_query_chars` |
| 연도 범위 2000~2100 | `draft.py` | 설정 `draft.year_min/max` |
| `unsupported` 사유 문장 5개 | `assemble.py` | 설정 `unsupported.*` |

옮긴 뒤 LLM에 가는 프롬프트는 이전과 **글자 단위로 같다** (옮기기 전 출력과 비교해 확인). `tests/test_query_config.py`는 옮긴 문장이 코드에 다시 생기지 않았는지 검사한다.

### 2.2 코드에 남긴 값과 이유
| 값 | 위치 | 남긴 이유 |
|---|---|---|
| 시즌 월 경계 (3~8월 → 같은 해 FW 등) | `season.next_fashion_season` | trend 계약(스냅샷 §3.5)의 규칙이다. 바꾸면 계약 변경이라 설정으로 열어 두지 않는다 |
| 연도 없는 시즌의 연도 규칙 (가정 A-1) | `season.resolve_season_year` | 소유자 확인 전 가정이다. 확정되면 그때 설정 여부를 정한다 |
| 표준 어휘 (카테고리, 속성, 성별, 시즌) | `vocabulary.py` (생성 파일) | 계약의 일부다. 손으로 고치지 않고 스냅샷에서 생성한다 (`scripts/generate_query_vocabulary.py`) |
| `missing_fields = []` (가정 A-2) | `assemble.py` | 계약에 필드 이름 정의가 없다 |
| 경고 코드 8개, 오류 코드 5개와 고정 메시지 | `assemble.py`, `errors.py` | 조정 값이 아니라 인터페이스 식별자다. 메시지는 원문 비노출 규칙 때문에 고정이다 |
| 프롬프트 ID 접두어 `query-structuring-prompt`, 자리 표시자 `{{VOCABULARY}}` `{{EXAMPLES}}` `{{QUERY}}` | `prompt.py` | 템플릿 파일 형식의 일부다 |
| 질의 안 `<질의>` 태그를 전각 문자로 바꾸는 처리 | `prompt.py` | `user_template.md`의 경계 표시와 짝이다. 템플릿의 경계 표시를 바꾸면 함께 고쳐야 한다 |
| 기본 경로 (`configs/`, `prompts/` = 저장소 루트) | `config.py` (`parents[3]`) | src 레이아웃 checkout 기준이다. 다른 위치는 환경변수로 지정한다 |

## 3. 기존 오케스트레이터 코드의 하드코딩 값 (옮기지 않음)
"후보" 열: ◎ 설정으로 옮길 가치가 큼, ○ 옮길 수 있음, — 코드에 두는 것이 맞음(계약·식별자).

### `src/vogue_orchestrator/core.py`
| 값 | 내용 | 후보 |
|---|---|---|
| 단계 이름·순서 | `trend → stock → project_07 → project_06` (`StageName`, `run()`의 고정 순서) | — (D2로 질의 단계를 넣으면 바뀜) |
| 상태 값 | 공통 `ok/partial/no_results/error`, trend `ok/no_results/error` | — |
| `CONTRACT_VERSION` | `vogue-handoff/0.1-proposal` | — |
| `P07_INPUT_SCHEMA_VERSION` | `1.0` (trend 결과에 P07용으로 붙이는 버전) | ○ |
| 생산자 ID | 검증용 `stock-agent`, `market-research-agent`, 기록용 `trend-analysis-agent`, `project-06-planning-draft-api` | ○ (connectors·demo·web과 중복) |
| request ID 생성 | `uuid4` | — |
| 오류 코드·문구 | `CONTRACT_ERROR`, `STAGE_FAILURE`, "Stage adapter failed; details suppressed." | — |

### `src/vogue_orchestrator/connectors.py`
| 값 | 내용 | 후보 |
|---|---|---|
| 서비스 경로 | stock `/v1/inventory-products`, Project_06 `/api/planning-draft` | ○ |
| 환경변수 이름 | `VOGUE_TREND_ADAPTER`, `VOGUE_STOCK_AGENT_URL`, `VOGUE_PROJECT07_ROOT`, `VOGUE_PROJECT06_URL` | — |
| Project_07 기본 경로 | `<저장소의 상위 폴더>/project_07` (`parents[3]`). **현재 작업 공간(`1103_shopping`)에는 이 폴더가 없다** | ◎ |
| Project_07 패키지 | `src/market_research_agent`, `MarketResearchAgent().run(...).to_proposal_v1_3_dict()` (1.3 proposal로 고정) | ○ |
| 전송 규칙 | loopback이 아니면 HTTPS 필수, 경로·자격 증명이 있는 URL 거부 | — |
| timeout | 없음 (프로세스 기본값). 미결정 사항 | ◎ (결정 후) |
| 연결 상태 설명 문구 | `connection_status()`의 한국어 문장 | ○ |

### `src/vogue_orchestrator/server.py`
| 값 | 내용 | 후보 |
|---|---|---|
| 바인드 주소·포트 | `127.0.0.1`/`localhost`만 허용, 포트 `8765`. Project_06 기본 포트와 겹친다 (README) | ◎ (포트) |
| `MAX_BODY_BYTES` | `32_000` | ○ |
| `MAX_QUERY_CHARS` | `1_000`. **질의 모듈 설정 `input.max_query_chars`, `web/app.js`의 "/ 1,000" 표시와 같은 값을 세 곳이 따로 갖고 있다** | ◎ |
| 보안 헤더 | CSP, `nosniff`, `no-store` 문자열 | — |
| 정적 파일 위치 | `<저장소>/web` (`parents[2]`) | — |
| 서버 이름 | `VOGUEOrchestratorDemo/0.1` | — |

### `src/vogue_orchestrator/demo.py`, `web/app.js`, `examples/`
| 위치 | 값 | 후보 |
|---|---|---|
| `demo.py` | 합성 응답의 버전(`1.0`, `1.3-proposal`), 생산자 ID, 안내 문장 | — (합성 데이터) |
| `web/app.js` | 단계 라벨·번호, 상태 라벨, 단계 설명 문장, 질의 글자 수 표시 "/ 1,000" | ○ |
| `examples/synthetic_local_integration.py` | sibling 경로 `<상위>/research/src`, `<상위>/project_06/src`, import `market_research_agent`, `vogue_npp.prototype.server._build_planning_draft_response` | — (로컬 검증 스크립트) |
| `examples/synthetic_demo.py` | trend 합성 응답이 `partial`을 반환 → 현재 core 규칙(`ok/no_results/error`)과 맞지 않아 trend 단계에서 멈춤 | 별도 수정 필요 |

## 4. 종속 관계

### 4.1 실행 환경
| 대상 | 요구 | 비고 |
|---|---|---|
| Python | 3.11 이상 (`pyproject.toml`) | 확인 환경: Windows, Python 3.12.10 (`py`) |
| 코어(core, connectors, server, demo) | 표준 라이브러리만 | 외부 패키지 없음 |
| 질의 모듈 | `pydantic>=2.0` (optional extra `query`) | 확인 버전 2.14.3. 없으면 질의 테스트는 skip |
| CONNECTED 모드 Project_07 | Project_07 checkout과 그 의존성이 같은 Python 환경에 있어야 함 | `sys.path`에 `<P07>/src`를 넣고 import |

### 4.2 다른 저장소·문서
| 대상 | 무엇에 의존 | 어디서 | 바뀌면 |
|---|---|---|---|
| trend-analysis-agent | `TrendQuery` 계약 (스냅샷 `docs/contracts/`, 원본 `origin/main@4196bc460876`) | 질의 모듈의 모델·어휘·프롬프트 | 스냅샷 갱신 → `generate_query_vocabulary.py` → 테스트 (`docs/query-structuring.md` §9) |
| trend-analysis-agent | `TrendAnalysisResult` 형태 (`request_id`, status 3종 등) | core의 trend 단계 검증 | core 수정 필요 |
| trend-analysis-agent | 호출 진입점 | 아직 없음 (`TrendSearcher.search` 함수만, D3) | connectors에 연결 방식 추가 |
| stock-agent | `POST /v1/inventory-products`, 응답 필드·`agent_id` | connectors, core | connectors·core 수정 |
| Project_07 | `market_research_agent` 패키지, 1.3 proposal 직렬화, 입력 `schema_version 1.0` | connectors, core | connectors·core 수정 |
| Project_06 | `POST /api/planning-draft`, 입력 필드와 `schema_version` 일치 규칙 | connectors, core | connectors·core 수정 |
| Notion 문서 | `Project Workflow`, `System Architecture`, `VOGUE 공통 핸드오프 스키마 제안` | 설계 판단의 근거 (README) | 이 세션에서는 열람 불가 (커넥터 미연결) |

### 4.3 환경변수 전체
| 이름 | 쓰는 곳 | 필수 |
|---|---|---|
| `VOGUE_TREND_ADAPTER` | connectors (CONNECTED 모드) | CONNECTED 모드에서 필수 |
| `VOGUE_STOCK_AGENT_URL` | connectors | CONNECTED 모드에서 필수 |
| `VOGUE_PROJECT07_ROOT` | connectors | 선택 (기본 `<상위>/project_07`) |
| `VOGUE_PROJECT06_URL` | connectors | CONNECTED 모드에서 필수 |
| `VOGUE_QUERY_CONFIG` | 질의 모듈 | 선택 (기본 `configs/query_structuring.json`) |
| `VOGUE_QUERY_PROMPTS_DIR` | 질의 모듈 | 선택 (기본 `prompts/`) |

### 4.4 저장소 안의 의존 방향
```
core ◄── connectors ◄── server ──► demo          (기존, 표준 라이브러리만)
                                               서로 import하지 않음 (tests/test_query_isolation.py)
query ──► configs/, prompts/, vocabulary.py ◄── docs/contracts/ (생성 스크립트)   (pydantic)
```

## 5. 정리하며 발견한 것 (조치하지 않음)
| # | 내용 | 영향 | 제안 |
|---|---|---|---|
| F-1 | `MAX_QUERY_CHARS` 1000이 server, 질의 설정, web 표시 세 곳에 따로 있다 | 하나만 바꾸면 UI와 서버·모듈의 한도가 어긋난다 | 질의 모듈을 파이프라인에 연결할 때 한 곳으로 모은다 |
| F-2 | 생산자 ID 문자열이 core, connectors, demo, web에 중복된다 | 이름이 바뀌면 여러 곳을 고쳐야 한다 | 공통 상수나 설정으로 모으는 것을 검토 |
| F-3 | Project_07 기본 경로(`<상위>/project_07`)가 현재 작업 공간에 없다 | `/api/connections`에서 Project_07이 미설정으로 보인다 | `VOGUE_PROJECT07_ROOT`를 쓰거나 기본값을 다시 정한다 |
| F-4 | `examples/synthetic_demo.py`가 trend 단계에서 계약 오류로 멈춘다 (README가 실행하라고 안내함) | README 안내대로 하면 실패 | 합성 trend 응답을 `no_results`로 맞춘다 (별도 작업) |
| F-5 | 서버 포트 8765와 Project_06 기본 포트가 겹친다 (README에 이미 기재) | 동시 실행 불가 | 포트를 설정으로 노출 |
| F-6 | 외부 호출 timeout이 없다 (미결정 사항) | 응답 없는 서비스에서 무한 대기 가능 | 운영 합의 후 설정으로 노출 |
