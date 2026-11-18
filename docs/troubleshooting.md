# 트러블슈팅과 교훈

Fashion Planner의 다섯 서비스를 연결하면서 무엇이 왜 깨졌고 어떻게 고쳤는지 적은 기록입니다. 항목마다
증상, 원인(근거가 되는 코드), 해결, 검증, 교훈, 커밋 순서로 씁니다. 서비스 하나에 국한된 문제는 각 서비스의
`docs/troubleshooting.md`에 있고, 두 레포에 걸친 항목은 양쪽에서 같은 ID를 씁니다.

커밋 링크는 이 레포의 히스토리를 가리킵니다.

| ID | 문제 |
|---|---|
| [T-HTTP-01](#t-http-01) | novelty 본문 크기 한도를 넘은 요청이 "서비스 사용 불가"로 보고됨 |
| [T-HTTP-02](#t-http-02) | 서비스가 직접 보낸 503·504가 일반 502로 바뀜 |
| [T-HTTP-03](#t-http-03) | 컨테이너끼리 연결하지 못하고, 읽기 타임아웃이 장애처럼 보임 |
| [T-HTTP-04](#t-http-04) | 알 수 없는 POST 경로가 워크플로 실행을 시작함 |
| [T-UI-01](#t-ui-01) | 일부 호스트에서 모듈 UI가 로드되지 않음 |
| [T-DEPLOY-01](#t-deploy-01) | 트렌드가 많은 요청에서 inventory가 60초 마감에 걸림 |
| [T-DEPLOY-02](#t-deploy-02) | 깨끗한 checkout에서 통합 스택이 기동하지 않음 |
| [T-DEPLOY-03](#t-deploy-03) | stub 프로필이 두 번째 실행에서 실패함 |
| [T-DEPLOY-04](#t-deploy-04) | 컨테이너 안에서 research의 메모리가 부족함 |
| [T-CONFIG-01](#t-config-01) | 설정하지 않은 Compose 값이 빈 문자열로 들어옴 |
| [T-SHEET-01](#t-sheet-01) | 기획서가 서로 다른 두 트렌드를 섞어 보여 줌 |
| [교훈](#교훈) | 진행 방식에서 얻은 교훈 |

---

<a id="t-http-01"></a>
## T-HTTP-01 본문 크기 한도가 "서비스 사용 불가"로 보고됨

fashion-planner-novelty와 공동 항목입니다(같은 ID로 서비스 쪽 내용이 있습니다).

- **증상.** novelty가 정상인데도 3단계가 `503 UPSTREAM_UNAVAILABLE`로 실패했습니다. 요청 본문은 1,041,802바이트였습니다.
- **원인.** novelty는 1,000,000바이트가 넘는 본문을 받지 않습니다. `Content-Length`만 보고 본문을 읽지 않은 채
  `413`을 보내고 연결을 닫습니다(HTTP/1.0 핸들러). orchestrator는 아직 보내는 중이었으므로 소켓 쓰기가
  `BrokenPipeError`로 실패했습니다. 이 예외는 `OSError`이고, 전송 중 생긴 `OSError`는 모두
  `UPSTREAM_UNAVAILABLE`로 바뀝니다(`src/fashion_planner_orchestrator/workflow_http.py`의 `post_wire`). 그래서
  `413`은 끝내 읽히지 않았습니다. 본문의 대부분은 novelty가 읽지 않는 trend·inventory 데이터(유사 상품, 근거
  URL, 반복되는 상품 사진)였습니다.
- **해결.** orchestrator는 trend·inventory 결과 원문을 실행 기록에 그대로 두고, novelty에는 투영을
  보냅니다(`upstream_projection.py`, `novelty-input-projection`). 유사 상품과 근거 URL·제목을 빼고, 재고 상품
  사진은 상품마다 한 번만 보냅니다. 예시 요청은 604,993바이트에서 176,069바이트가 되었습니다(착장 이미지를
  위해 사진을 남긴 뒤에는 292,493바이트). 결합 검사는 저장된 원문에서 투영을 다시 계산해 novelty가 받은 요청과
  같은지 확인합니다.
- **검증.** `tests/test_upstream_projection.py`가 픽스처에 대한 투영 결과를 해시로 고정하고,
  `tests/test_workflow.py`가 저장된 호출이 투영과 같은지 확인합니다.
- **교훈.** 전송 오류가 진짜 응답을 가릴 수 있습니다. 요청을 쓰는 도중에 호출이 실패하면 서비스를 의심하기
  전에 요청 크기부터 확인하세요. 각 서비스에는 그 서비스가 읽는 것만 보내세요.
- **커밋.** {{commit:7463058}}, {{commit:cefc61f}}

<a id="t-http-02"></a>
## T-HTTP-02 서비스의 503·504가 502로 바뀜

- **증상.** 서비스가 `503`(바쁨)이나 `504`(자체 마감)로 답하면 실행 결과가 `502`가 되어, UI가 "다시 시도"
  대신 "계약 오류"를 보여 줬습니다.
- **원인.** 2xx가 아닌 응답을 모두 계약 실패로 처리했습니다.
- **해결.** 서비스가 직접 보낸 `503`과 `504`는 의미를 유지합니다. 단계는 `503 UPSTREAM_UNAVAILABLE` 또는
  `504 UPSTREAM_TIMEOUT`(재시도 가능)으로 실패하며, 이는 연결 거부나 소켓 타임아웃과 같은 코드입니다. 그 밖의
  non-2xx는 `502`, `401`·`403`은 `UPSTREAM_AUTH`입니다. 거부된 응답 본문은 그대로 저장합니다.
- **검증.** `tests/test_outbound.py`, `tests/test_workflow.py`.
- **교훈.** 오류 매핑도 계약입니다. 표를 한 번 정하고 모든 행을 테스트하세요.
- **커밋.** {{commit:3afd346}}

<a id="t-http-03"></a>
## T-HTTP-03 컨테이너끼리 연결하지 못하고, 타임아웃이 장애처럼 보임

- **증상.** Docker 안에서 orchestrator가 `http://trend:8768` 호출을 거부했습니다. 느린 서비스에서는 읽기
  타임아웃이 `503 UPSTREAM_UNAVAILABLE`로 보고됐습니다.
- **원인.** 아웃바운드 정책이 평문 HTTP를 loopback에만 허용했습니다. 또 `TimeoutError`는 `OSError`의 하위
  클래스여서 일반 `OSError` 분기가 먼저 잡았습니다.
- **해결.** 평문 HTTP는 명시한 단일 라벨 호스트 이름(`trend`, `inventory`, …)에만, 그리고 bearer 토큰이 있을
  때만 허용합니다. 타임아웃은 `OSError`보다 먼저 확인해 `504 UPSTREAM_TIMEOUT`으로 보고합니다.
- **검증.** `tests/test_outbound.py`, `tests/test_workflow.py`.
- **교훈.** 예외 처리는 가장 구체적인 클래스부터 순서대로 쓰세요. Python의 `TimeoutError`는 `OSError`입니다.
- **커밋.** {{commit:bdc8cba}}

<a id="t-http-04"></a>
## T-HTTP-04 알 수 없는 POST 경로가 실행을 시작함

- **증상.** `/api/workflow/` 아래의 문서에 없는 경로로 POST하면 워크플로 실행이 시작됐습니다.
- **원인.** 맞는 경로가 없으면 실행 핸들러로 넘어갔습니다.
- **해결.** 경로를 명시적인 표로 관리합니다. `/api/workflow/` 아래의 다른 경로는 `404` problem 본문으로,
  알려진 경로에 다른 메서드로 오면 `Allow` 헤더가 붙은 `405` problem으로 답합니다.
- **검증.** `tests/test_workflow_routes.py`.
- **교훈.** 비용이 드는 경로는 정확히 일치할 때만 받으세요.
- **커밋.** {{commit:188414a}}

<a id="t-ui-01"></a>
## T-UI-01 일부 호스트에서 모듈 UI가 로드되지 않음

- **증상.** 어떤 컴퓨터에서는 UI가 빈 화면이었고 다른 컴퓨터에서는 정상이었습니다.
- **원인.** 모든 응답에 `X-Content-Type-Options: nosniff`가 붙으므로 MIME 형식이 없거나 틀리면 브라우저가
  파일을 막습니다. Python의 MIME 표는 호스트마다 달라서 `.woff2`가 없을 수 있고, macOS와 Debian은 `.js`의 형식이 다릅니다.
- **해결.** 서버가 시작할 때 자기가 보내는 MIME 형식(`text/javascript`, `text/css`, `font/woff2` 등)을 직접
  등록하고, `'self'`로 제한한 Content-Security-Policy를 보냅니다.
- **검증.** `tests/test_static_assets.py`.
- **교훈.** 직접 배포하는 파일의 MIME 형식을 호스트 설정에 맡기지 마세요.
- **커밋.** {{commit:5a7c768}}

<a id="t-deploy-01"></a>
## T-DEPLOY-01 inventory 마감과 orchestrator 읽기 타임아웃

- **증상.** 트렌드 11개짜리 실행이 2단계에서 inventory의 `504 TIMEOUT`으로 실패했습니다.
- **원인.** inventory는 트렌드를 하나씩 처리하고 트렌드당 약 9~11초가 걸리는데, 요청 마감은 60초였습니다.
- **해결.** inventory 마감을 180초로 늘렸고(`INVENTORY_HANDOFF_TIMEOUT_SECONDS`), orchestrator에는 이 호출에만
  쓰는 210초 읽기 타임아웃을 두어 inventory의 타임아웃 응답이 orchestrator가 포기하기 전에 도착하게 했습니다.
- **검증.** `tests/test_blank_settings.py`가 단계별 읽기 타임아웃 설정을 다룹니다. 값은 `deploy/compose.yaml`에 있습니다.
- **교훈.** 호출하는 쪽의 타임아웃은 호출받는 쪽의 마감보다 길어야 합니다. 그래야 상대의 설명을 받을 수 있습니다.
- **커밋.** {{commit:8b435a0}}, {{commit:4ffa2c5}}

<a id="t-deploy-02"></a>
## T-DEPLOY-02 통합 스택이 기동하지 않음

- **증상.** `docker compose up`이 끝나지 않거나 서비스가 시작하자마자 종료했습니다.
- **원인.** 세 가지 문제가 겹쳤습니다. novelty 이미지에는 health check가 없고 health 경로에 bearer 토큰이
  필요해서 `service_healthy`가 오지 않았습니다. research의 DuckDB는 `<cwd>/.tmp`에 넘침 파일을 쓰는데 읽기 전용
  루트 파일 시스템이 막았습니다. 유료 프로필은 novelty가 읽지 않는 이름으로 키를 넘겼습니다.
- **해결.** 토큰을 보내는 Compose health check, DuckDB 넘침 디렉터리용 쓰기 가능한 볼륨 subpath, `LLM_API_KEY` 이름으로 키 전달.
- **검증.** stub 종단 간 검사(`deploy/e2e/run_e2e.py`).
- **교훈.** 읽기 전용 루트 파일 시스템은 숨은 쓰기를 모두 드러냅니다. 쓰는 곳은 하나하나 명시적으로 마운트하세요.
- **커밋.** {{commit:c10d35d}}

<a id="t-deploy-03"></a>
## T-DEPLOY-03 stub 프로필이 두 번째 실행에서 실패함

- **증상.** `stub`이 처음에는 됐지만 두 번째에는 inventory 픽스처를 준비하다 실패했고, 종단 간 검사는
  system-status 응답을 잘못 읽었습니다.
- **원인.** 픽스처 작업이 이전 데이터베이스 파일을 교체하지 못했고, 검사는 옛 응답 모양을 기대했습니다.
- **해결.** 권한 작업이 필요한 권한으로 옛 파일을 지우고, 검사는 현행 응답 모양을 읽습니다. (그때는 기준선이
  동결되기 전이라 stub이 계약 게이트를 개발 모드로 실행했습니다. 지금은 동결된 기준선으로 실행합니다.)
- **검증.** 같은 변경에서 stub 종단 간 검사(`deploy/e2e/run_e2e.py`)를 고쳤습니다.
- **교훈.** 상태를 만드는 작업은 두 번째 실행까지 확인하세요.
- **커밋.** {{commit:f50f13f}}

<a id="t-deploy-04"></a>
## T-DEPLOY-04 research의 메모리 부족

fashion-planner-research와 공동 항목입니다.

- **증상.** 4단계가 `partial`로 끝나며 research가 38초 뒤 DuckDB `OutOfMemoryException`을 보고하거나, 컨테이너가 강제 종료됐습니다.
- **원인.** 고객 연령 질의가 1.5 GB 컨테이너 안에서 DuckDB 메모리 한도를 3 GB로 잡아, DuckDB가 넘침 파일을 쓰기
  전에 커널이 프로세스를 죽였습니다. 1 GB로 낮추자 이번에는 DuckDB 자체가 메모리 부족을 냈습니다.
- **해결.** 컨테이너 한도는 3 GB, DuckDB 한도는 2 GB로 하고 쓰기 가능한 넘침 디렉터리를 줬습니다.
- **검증.** 이 레포에는 자동 검사가 없습니다(실제 데이터가 필요). 질의 자체는 fashion-planner-research가 다룹니다.
- **교훈.** 프로세스 안의 메모리 한도는 컨테이너 한도보다 낮게 잡고, 넘칠 곳을 마련하세요.
- **커밋.** {{commit:d440a3f}}, {{commit:de996a4}}

<a id="t-config-01"></a>
## T-CONFIG-01 설정하지 않은 값이 빈 문자열로 들어옴

- **증상.** 빈 모델 이름이 이미지 제공자에 전달됐고, 공백뿐인 키가 설정된 것처럼 보였습니다.
- **원인.** Compose는 설정하지 않은 선택 변수를 빈 문자열로 넘깁니다.
- **해결.** 모든 선택 설정에서 빈 값(빈 문자열이나 공백)은 "기본값" 또는 "설정 없음"을 뜻합니다.
- **검증.** `tests/test_blank_settings.py`.
- **교훈.** 설정을 읽는 경계에서 빈 값을 없는 값으로 다루세요.
- **커밋.** {{commit:66797fc}}

<a id="t-sheet-01"></a>
## T-SHEET-01 기획서가 두 트렌드를 섞어 보여 줌

- **증상.** 분석 열은 한 트렌드를, 디자인 열은 다른 트렌드를 보여 줬습니다.
- **원인.** 기획서가 trend 결과의 첫 트렌드를 초점으로 삼았는데, trend 결과의 순서는 Style id 순일 뿐 관련도를 뜻하지
  않고, novelty는 트렌드를 자유롭게 고릅니다.
- **해결.** 초안이 인용한 트렌드 스타일(`trend_basis.source_style_id`)을 초점으로 삼습니다.
- **검증.** `tests/test_focus_trend.py`.
- **교훈.** 계약이 순서를 정하지 않았다면 목록의 위치에 의미를 두지 마세요.
- **커밋.** {{commit:118bc39}}

---

<a id="교훈"></a>
## 교훈

- **계약은 깨끗한 checkout에서 동결하세요.** 커밋하지 않은 변경이 있는 작업 트리에서 계약 사본을 내보낸 적이
  있어, 기록된 출처를 재현할 수 없었습니다. 지금은 동결 스크립트가 더러운 checkout을 거부하고 각 checkout의
  지문을 기록합니다.
- **작업 폴더를 동기화 폴더 밖에 두세요.** 클라우드 동기화가 내려 둔(dataless) 파일 때문에 git, Python, Docker가
  멈췄습니다. 가상환경과 작업 사본을 동기화 폴더 밖으로 옮겼습니다.
- **유료 실행을 막는 장치를 두세요.** 프로필을 잘못 알고 실행해 유료 모델 호출이 나간 적이 있습니다. 지금은
  `live` 명령이 두 번째 실행을 거부하고, 문서에 실행 중인 프로필부터 확인하라고 적었습니다.
- **CI에서 테스트를 돌리세요.** 테스트를 고치지 않은 변경 두 개가 머지되어 실패가 나중에야 드러났습니다. 지금은
  모든 풀 리퀘스트에서 오프라인 테스트를 실행합니다.
