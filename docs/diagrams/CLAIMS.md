# 다이어그램 근거표

`docs/diagrams/`의 그림 네 개에 나오는 상자와 화살표마다 코드 근거를 적었다. 이 레포의 경로는 레포 루트 기준이고,
다른 레포의 경로는 `fashion-planner-<서비스>/` 접두로 적었다. 그림을 고치면 이 표도 함께 고친다.
원본은 `docs/diagrams/src/`, 다시 그리기는 `sh scripts/render_diagrams.sh`(Graphviz `dot`, `rsvg-convert` 필요).

## 1. 시스템 전체 (`system.svg`)

| 요소 | 근거 | 확인 방법 |
|---|---|---|
| orchestrator `:8765`, 브라우저 UI(정적 `web/`) | `src/fashion_planner_orchestrator/server.py:15`(WEB_ROOT), `:151`(기본 포트), `deploy/compose.yaml:195-196`(127.0.0.1에만 공개) | 파일 읽기 |
| trend `:8768`, inventory `:8002`, novelty `:8767`, research `:8766` | `deploy/compose.yaml:177-180` | 파일 읽기 |
| UI → orchestrator: 실행, 진행 조회, 산출물, 기획서 | `src/fashion_planner_orchestrator/workflow_http.py:317-327`(라우트 표) | 파일 읽기 |
| ① ~ ④ 서비스 호출 경로 | `src/fashion_planner_orchestrator/workflow_http.py:182-185` | 파일 읽기, `tests/test_workflow_routes.py` |
| 실행 기록 저장소: SQLite 체크포인트, 내용 주소 blob | `src/fashion_planner_orchestrator/workflow_store.py:167-178` | 파일 읽기 |
| 6단계 기사 사진: 기본 꺼짐, 설정한 호스트(`ORCHESTRATOR_TREND_PHOTO_HOSTS`)에서만, HTTPS, 최대 3장. UI는 링크만, CSP에 바깥 이미지 호스트 없음 | `src/fashion_planner_orchestrator/trend_photos.py:8-11,30-31,48-58`, `src/fashion_planner_orchestrator/server.py:19-22`, `web/js/render.js`(`itemPhotoLink`), `deploy/compose.yaml`(`FASHION_PLANNER_TREND_PHOTO_HOSTS`) | `tests/test_trend_photos.py` |
| orchestrator → OpenAI: 질의 구조화, SWOT 초안(live에서만) | `src/fashion_planner_orchestrator/llm_provider.py:9,77`, `src/fashion_planner_orchestrator/swot.py:225-238`, `deploy/compose.paid.yaml:10-11` | 파일 읽기 |
| trend → Neo4j(Aura 또는 로컬 stub) | `fashion-planner-trend/src/fashion_planner_trend/config.py:79`(`neo4j_settings`), `:93-121`(`neo4j_driver_args`, `neo4j_driver`), `deploy/compose.stub.yaml:13,56`, `deploy/compose.data.yaml:18` | 파일 읽기 |
| inventory → DuckDB 캐시 + CLIP 인덱스 | `fashion-planner-inventory/fashion_planner_inventory/api.py:101`(DB 읽기 전용), `:137-165`(`clip_index_path`, `warm_up_clip`) | 파일 읽기 |
| inventory → R2: 요청 중 상품·착샷 사진, DB·CLIP 인덱스 캐시는 `--prepare-cache`가 미리 받음 | `fashion-planner-inventory/fashion_planner_inventory/storage.py:70-79`(R2 설정), `:207-271`(사진·착샷 읽기), `:319-333`(`allow_download`), `deploy/compose.data.yaml:6,34-35` | 파일 읽기 |
| inventory → 무신사 이미지 CDN: CLIP 기준 이미지(https, `msscdn.net`, 리다이렉트 없음, 10 MB 상한, 메모리에서 임베딩만). CLIP 인덱스가 있는 rehearsal·live에서만 | `fashion-planner-inventory/fashion_planner_inventory/clip_matcher.py:135-166` | 파일 읽기 |
| inventory → 모델 가중치 호스트: OpenCLIP 가중치를 처음 불러올 때 내려받음. 받는 곳은 설치한 open_clip 버전이 정함(open_clip_torch 4.2.2는 Hugging Face Hub를 먼저 씀, inventory 세션 확인) | `fashion-planner-inventory/fashion_planner_inventory/clip_matcher.py:51-52,79-92`(`ViT-B-32-quickgelu`, `openai`), open_clip `pretrained.py`의 `download_pretrained`, `deploy/compose.data.yaml`(`HF_HOME=/models/hf` 볼륨) | 파일 읽기 |
| novelty → 초안 원장(SQLite + 이미지 파일) | `fashion-planner-novelty/src/fashion_planner_novelty/workflows/draft_artifacts.py:151` | 파일 읽기 |
| novelty → OpenAI: 컨셉 텍스트, 이미지(live에서만) | `fashion-planner-novelty/src/fashion_planner_novelty/sources/openai_text_provider.py:152`, `fashion-planner-novelty/src/fashion_planner_novelty/workflows/image_generation.py:102`, `deploy/compose.paid.yaml:16-18` | 파일 읽기 |
| research → R2: H&M 동의(`RESEARCH_HM_NONCOMMERCIAL_RESEARCH=1`)와 스냅샷 키가 있으면 집계 스냅샷 하나(H&M 가격·고객 연령 + 원단 목록), 동의가 꺼져 있으면(기본) 원단 목록 CSV만 | `fashion-planner-research/src/fashion_planner_research/sources/sales/hm_kaggle/hm_combined_research_source.py:40-87`, `fashion-planner-research/src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:525`, `fashion-planner-research/src/fashion_planner_research/sources/market/fabric_price_r2_source.py:26`, `deploy/compose.data.yaml:64-66` | 파일 읽기 |
| research → Frankfurter 환율: H&M 동의가 켜지고 과거 가격이 있을 때만 | `fashion-planner-research/src/fashion_planner_research/domain/currency_conversion.py:17`, `fashion-planner-research/src/fashion_planner_research/http_server.py:444-448` | 파일 읽기 |
| research → OpenAI: 타깃 가설(live, 전송 동의 `RESEARCH_TARGET_LLM_TRANSFER_AUTHORIZED=1`일 때, 120초 제한) | `fashion-planner-research/src/fashion_planner_research/llm/responses_api.py:17`, `fashion-planner-research/src/fashion_planner_research/http_server.py:460-472`, `deploy/compose.paid.yaml:28-31` | 파일 읽기 |
| 초안 원장 → 실행 기록: 읽기 전용 볼륨, sha256 확인 | `deploy/compose.yaml:199-204`, `src/fashion_planner_orchestrator/workflow.py:242,255`, `src/fashion_planner_orchestrator/workflow_store.py:273,402-407` | `tests/test_workflow_artifacts.py` |

## 2. 계약 지도 (`contracts.svg`)

| 요소 | 근거 | 확인 방법 |
|---|---|---|
| `fashion-planner-workflow`, POST `/api/workflow/run`·`/v1/orchestrations`, 응답 integrated-result | `src/fashion_planner_orchestrator/workflow_contract.py`(`VERSION`), `contracts/workflow/integrated-result.schema.json`, `src/fashion_planner_orchestrator/workflow_http.py:318,358` | `python scripts/freeze_workflow_contract.py --check` |
| md-decision 요청 | `src/fashion_planner_orchestrator/workflow_http.py:326`, `src/fashion_planner_orchestrator/workflow_store.py:30`, `contracts/agents/orchestrator/input/md-decision-request.contract.json` | `tests/test_workflow_routes.py` |
| ① `trend-handoff`: TrendQuery → TrendAnalysisResult | `contracts/agents/trend/input/trend-query.contract.json`, `contracts/agents/trend/output/trend-analysis-result.contract.json`, `fashion-planner-trend/src/fashion_planner_trend/api/server.py:24` | `python scripts/export_agent_contracts.py --check` |
| ② `inventory-handoff`: trend 결과를 그대로 → inventory 결과 | `contracts/agents/inventory/`, `fashion-planner-inventory/fashion_planner_inventory/api.py:47` | 같음 |
| ③ `novelty-handoff`: 기획 요청 + 투영(`novelty-input-projection`) → 초안 + 이미지 | `src/fashion_planner_orchestrator/upstream_projection.py:19,61-63`, `contracts/workflow/planning-draft-request.schema.json`, `contracts/workflow/planning-draft-response.schema.json`, `contracts/agents/novelty/input/handoff-request.contract.json` | 같음, `tests/test_upstream_projection.py` |
| ④ `research-handoff`: 허용 상품 필드 + 필드별 출처 → 판매·시장 검토 | `src/fashion_planner_orchestrator/workflow_contract.py`(`research_review_input`, `research_product_projection`), `contracts/workflow/research-review-{request,response,error}.schema.json`, `fashion-planner-research/src/fashion_planner_research/http_server.py:23` | 같음 |
| 공통 규칙: 요청·응답 envelope, python-json-sort sha256, frozen 기준선일 때만 실행 | `contracts/workflow/common.schema.json`, `src/fashion_planner_orchestrator/workflow_store.py`(`canonical`, `digest`), `contracts/workflow/baseline.json`, `tests/test_workflow_gate.py` | `python scripts/freeze_workflow_contract.py --check` |

## 3. 배포 프로필 (`deploy-profiles.svg`)

| 요소 | 근거 | 확인 방법 |
|---|---|---|
| 기본: 서비스 5개, 읽기 전용, uid 10001, orchestrator만 127.0.0.1 공개, 공용 토큰 | `deploy/compose.yaml:14-16,31,195-196` | `docker compose config` |
| stub: 로컬 Neo4j + 합성 그래프, 합성 DuckDB, novelty 합성 이미지, research 합성 소스, 합성 질의 구조화, UI 18765 | `deploy/compose.stub.yaml:13,48,72,104,108`, `deploy/compose.yaml:117`, `deploy/bin/fashion-planner:27` | 같음 |
| rehearsal: Aura, R2, H&M 동의 설정 | `deploy/compose.data.yaml:6,18,64-66` | 같음 |
| live: OpenAI 호출, research 동의 설정, live 1회 가드 | `deploy/compose.paid.yaml:10-31`, `deploy/bin/fashion-planner:20,111` | 같음 |

## 4. 워크플로 시퀀스 (`sequence-workflow.svg`)

| 요소 | 근거 | 확인 방법 |
|---|---|---|
| 요청과 계약 게이트, 질의 구조화 | `src/fashion_planner_orchestrator/workflow_http.py:318`, `src/fashion_planner_orchestrator/server.py:57` | `tests/test_workflow_gate.py`, `tests/test_ui_query_structuring.py` |
| 1~4단계 호출 순서와 경로 | `src/fashion_planner_orchestrator/workflow_contract.py:21`(`STAGES`), `src/fashion_planner_orchestrator/workflow_http.py:182-185` | `tests/test_workflow.py` |
| novelty 이미지: `artifact://novelty/images/<sha256>.<확장자>`(지금은 `.png`), 읽기 전용 볼륨, sha256 확인 | `src/fashion_planner_orchestrator/workflow.py:242,255`, `deploy/compose.yaml:199-204` | `tests/test_workflow_artifacts.py` |
| 멈춤 규칙: error면 멈춤, trend·novelty no_results면 no_results, research no_results면 partial로 멈추고 통합·기획서 없음 | `src/fashion_planner_orchestrator/workflow.py:493-503,407-411` | `tests/test_workflow.py` |
| 5단계 통합, 6단계 기사 사진·SWOT·기획서 PNG(3200 px, 이미지 모델 호출 없음) | `src/fashion_planner_orchestrator/workflow.py:405-416`, `src/fashion_planner_orchestrator/trend_photos.py:30-31`, `src/fashion_planner_orchestrator/swot.py:225`, `src/fashion_planner_orchestrator/planning_sheet.py:908-909`, `src/fashion_planner_orchestrator/workflow_http.py:100,121` | `tests/test_planning_sheet.py` |
| 이후: md-decision(adopt, revise, hold; 추가만) | `src/fashion_planner_orchestrator/workflow_store.py:30`, `src/fashion_planner_orchestrator/workflow_http.py:326` | `tests/test_workflow_routes.py` |
