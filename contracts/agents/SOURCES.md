# Agent 계약 스냅샷 출처

생성물이다. 직접 고치지 않는다. 다시 만들기: `py scripts/export_agent_contracts.py`, 확인: `py scripts/export_agent_contracts.py --check`.
구조와 갱신 절차는 [`../README.md`](../README.md)를 본다.

## 스냅샷

| Agent | 방향 | 파일 | 계약 | payload 버전 | 방식 | 출처 (branch @ commit) | 원본 파일 |
|---|---|---|---|---|---|---|---|
| trend-analysis-agent | input | [`trend-query.contract.json`](trend-analysis-agent/input/trend-query.contract.json) | TrendQuery | - | 고정(검사만) | `origin/main@4196bc460876` | `trend_agent.schemas.trend_query:TrendQuery` |
| trend-analysis-agent | output | [`trend-analysis-result-1.1.contract.json`](trend-analysis-agent/output/trend-analysis-result-1.1.contract.json) | TrendAnalysisResult | 1.1 | pydantic 내보내기 | `main @ 4a2086ad4244` | `trend_agent.schemas.trend_analysis_result:TrendAnalysisResult` |
| stock-agent | input | [`stock-agent-request-1.0.contract.json`](stock-agent/input/stock-agent-request-1.0.contract.json) | stock-agent input (TrendAnalysisResult as stock-agent reads it) | 1.0 | 복사 | `main @ f483bdf16573` | `contracts/schemas/stock-agent-request.schema.json` |
| stock-agent | output | [`stock-agent-result-1.0.contract.json`](stock-agent/output/stock-agent-result-1.0.contract.json) | stock-agent result | 1.0 | 복사 | `main @ 416c37c8695e` | `contracts/schemas/stock-agent-result.schema.json` |
| sales-market-agent | input | [`sales-market-review-proposal-0.2.contract.json`](sales-market-agent/input/sales-market-review-proposal-0.2.contract.json) | P07 /reviews proposal request (allowlisted product projection) | vogue-sales-market-review/0.2-proposal | 복사 | `main @ 86d833506cc7` | `contracts/schemas/proposals/sales-market-review-0.2-proposal.schema.json` |
| sales-market-agent | output | [`sales-market-review-proposal-response-0.1.contract.json`](sales-market-agent/output/sales-market-review-proposal-response-0.1.contract.json) | P07 /reviews native review response | sales-market-review/0.1-internal | 복사 | `main @ 86d833506cc7` | `contracts/schemas/proposals/sales-market-review-0.2-proposal-response.schema.json` |
| sales-market-agent | output | [`sales-market-review-proposal-error-0.2.contract.json`](sales-market-agent/output/sales-market-review-proposal-error-0.2.contract.json) | P07 /reviews error response | vogue-sales-market-review/0.2-proposal | 복사 | `main @ 86d833506cc7` | `contracts/schemas/proposals/sales-market-review-0.2-error.schema.json` |
| new-product-agent | input | [`planning-draft-input-0.3.contract.json`](new-product-agent/input/planning-draft-input-0.3.contract.json) | new-product-agent /runs input (three producer payloads) | - | 코드 읽고 작성 | `main @ cdd11cdb1b24` | `src/vogue_npp/adapters/handoff.py, src/vogue_npp/prototype/server.py` |
| new-product-agent | input | [`sales-market-review-bridge-0.1.contract.json`](new-product-agent/input/sales-market-review-bridge-0.1.contract.json) | P06 /sales-market-reviews bridge request (validates P06 Draft and returns a P07 request) | vogue-sales-market-review/0.1-proposal | 코드 읽고 작성 | `main @ f42bda6c9aa7` | `src/vogue_npp/prototype/server.py` |
| vogue-orchestrator | output | [`integrated-result-2.0.contract.json`](vogue-orchestrator/output/integrated-result-2.0.contract.json) | vogue-orchestrator workflow run response (integrated result) | integrated-result/2.0 | 복사 | `feat/merge-newP-salesM @ b259b97d7523+uncommitted` | `contracts/workflow/integrated-result.schema.json` |
| vogue-orchestrator | input | [`md-decision-request-2.0.contract.json`](vogue-orchestrator/input/md-decision-request-2.0.contract.json) | vogue-orchestrator MD decision request (POST /api/workflow/runs/{rid}/md-decision) | vogue-workflow/0.2 | 코드 읽고 작성 | `feat/merge-newP-salesM @ b259b97d7523` | `src/vogue_orchestrator/workflow_http.py, src/vogue_orchestrator/workflow_store.py, src/vogue_orchestrator/workflow_contract.py` |

## HTTP handoff contract_version

Agent 경로마다 Agent 소스의 값과 오케스트레이터가 그 경로에 쓰는 값을 비교한다. `/runs`(vogue-handoff)는 Trend·Stock `/runs`는 `HANDOFF_VERSION`, P06 `/runs`는 `P06_HANDOFF_VERSION`, P07 `/reviews`는 `P07_REVIEW_VERSION`, P08 public API는 `VERSION`과 비교한다.

| Agent | route | Agent가 받는 contract_version | 읽은 위치 | 오케스트레이터 값 | 읽은 위치 (이 저장소) | 비교 |
|---|---|---|---|---|---|---|
| trend-analysis-agent | `/v1/agents/trend-analysis-agent/runs` | `vogue-handoff/0.1` | `src/trend_agent/api/server.py:CONTRACT_VERSION` | `vogue-handoff/0.1` | `src/vogue_orchestrator/workflow_contract.py:HANDOFF_VERSION` | 같음 |
| stock-agent | `/v1/agents/stock-agent/runs` | `vogue-handoff/0.1` | `stock_agent/api.py:HANDOFF_CONTRACT_VERSION` | `vogue-handoff/0.1` | `src/vogue_orchestrator/workflow_contract.py:HANDOFF_VERSION` | 같음 |
| new-product-agent | `/v1/agents/new-product-agent/runs` | `vogue-handoff/0.3` | `src/vogue_npp/adapters/handoff.py:CONTRACT_VERSION` | `vogue-handoff/0.3` | `src/vogue_orchestrator/workflow_contract.py:P06_HANDOFF_VERSION` | 같음 |
| sales-market-agent | `/v1/agents/sales-market-agent/reviews` | `vogue-sales-market-review/0.2-proposal` | `src/market_research_agent/http_server.py:REVIEW_CONTRACT_VERSION` | `vogue-sales-market-review/0.2-proposal` | `src/vogue_orchestrator/workflow_contract.py:P07_REVIEW_VERSION` | 같음 |
| new-product-agent-review-bridge | `/v1/agents/new-product-agent/sales-market-reviews` | `vogue-sales-market-review/0.1-proposal` | `src/vogue_npp/prototype/server.py:P06_REVIEW_BRIDGE_CONTRACT_VERSION` | `vogue-sales-market-review/0.1-proposal` | `src/vogue_orchestrator/workflow_contract.py:P06_REVIEW_BRIDGE_VERSION` | 같음 |
