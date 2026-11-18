# Agent 계약 스냅샷 출처

생성물이다. 직접 고치지 않는다. 다시 만들기: `py scripts/export_agent_contracts.py`, 확인: `py scripts/export_agent_contracts.py --check`.
구조와 갱신 절차는 [`../README.md`](../README.md)를 본다.

## 스냅샷

| 서비스 | 방향 | 파일 | 계약 | payload 버전 | 방식 | 출처 레포 | 원본 파일 |
|---|---|---|---|---|---|---|---|
| trend | input | [`trend-query.contract.json`](trend/input/trend-query.contract.json) | TrendQuery | - | 고정(검사만) | `-` | `trend_agent.schemas.trend_query:TrendQuery` |
| trend | output | [`trend-analysis-result.contract.json`](trend/output/trend-analysis-result.contract.json) | TrendAnalysisResult | 1.0.0 | pydantic 내보내기 | `KG-RAG-02/trend-analysis-agent` | `trend_agent.schemas.trend_analysis_result:TrendAnalysisResult` |
| inventory | input | [`inventory-request.contract.json`](inventory/input/inventory-request.contract.json) | inventory input (TrendAnalysisResult as inventory reads it) | 1.0.0 | 복사 | `KG-RAG-02/stock-agent` | `contracts/schemas/stock-agent-request.schema.json` |
| inventory | output | [`inventory-result.contract.json`](inventory/output/inventory-result.contract.json) | inventory result | 1.0.0 | 복사 | `KG-RAG-02/stock-agent` | `contracts/schemas/stock-agent-result.schema.json` |
| research | input | [`review-request.contract.json`](research/input/review-request.contract.json) | research /reviews request (allowlisted product projection with per-field provenance) | research-handoff | 복사 | `KG-RAG-02/sales-market-agent` | `contracts/schemas/proposals/sales-market-review-0.3-proposal.schema.json` |
| research | output | [`review-response.contract.json`](research/output/review-response.contract.json) | research /reviews native review response | 1.0.0 | 복사 | `KG-RAG-02/sales-market-agent` | `contracts/schemas/proposals/sales-market-review-0.3-proposal-response.schema.json` |
| research | output | [`review-error.contract.json`](research/output/review-error.contract.json) | research /reviews error response | research-handoff | 복사 | `KG-RAG-02/sales-market-agent` | `contracts/schemas/proposals/sales-market-review-0.3-error.schema.json` |
| novelty | input | [`handoff-request.contract.json`](novelty/input/handoff-request.contract.json) | novelty /runs input (three producer payloads) | - | 코드 읽고 작성 | `KG-RAG-02/new-product-agent` | `src/vogue_npp/adapters/draft_inputs.py, src/vogue_npp/workflows/planning_draft.py, src/vogue_npp/domain/context.py, src/vogue_npp/workflows/text_concept_generation.py, src/vogue_npp/prototype/server.py` |
| orchestrator | output | [`integrated-result.contract.json`](orchestrator/output/integrated-result.contract.json) | orchestrator workflow run response (integrated result) | 1.0.0 | 복사 | `KG-RAG-02/fashion-planner-orchestrator` | `contracts/workflow/integrated-result.schema.json` |
| orchestrator | input | [`md-decision-request.contract.json`](orchestrator/input/md-decision-request.contract.json) | orchestrator MD decision request (POST /api/workflow/runs/{rid}/md-decision) | fashion-planner-workflow | 코드 읽고 작성 | `KG-RAG-02/fashion-planner-orchestrator` | `src/fashion_planner_orchestrator/workflow_http.py, src/fashion_planner_orchestrator/workflow_store.py, src/fashion_planner_orchestrator/workflow_contract.py` |

## HTTP handoff contract_version

서비스 경로마다 서비스 소스의 값과 오케스트레이터가 그 경로에 쓰는 값을 비교한다. trend·inventory·novelty `/runs`는 각각 `TREND_HANDOFF_VERSION`·`INVENTORY_HANDOFF_VERSION`·`NOVELTY_HANDOFF_VERSION`, research `/reviews`는 `RESEARCH_HANDOFF_VERSION`, 오케스트레이터 공개 API는 `VERSION`과 비교한다.

| 서비스 | 경로 | 서비스가 받는 contract_version | 읽은 위치 | 오케스트레이터 값 | 읽은 위치 (이 저장소) | 비교 |
|---|---|---|---|---|---|---|
