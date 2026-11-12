# Agent 계약 스냅샷 출처

생성물이다. 직접 고치지 않는다. 다시 만들기: `py scripts/export_agent_contracts.py`, 확인: `py scripts/export_agent_contracts.py --check`.
구조와 갱신 절차는 [`../README.md`](../README.md)를 본다.

## 스냅샷

| Agent | 방향 | 파일 | 계약 | payload 버전 | 방식 | 출처 (branch @ commit) | 원본 파일 |
|---|---|---|---|---|---|---|---|
| trend-analysis-agent | input | [`trend-query.contract.json`](trend-analysis-agent/input/trend-query.contract.json) | TrendQuery | - | 고정(검사만) | `origin/main@4196bc460876` | `trend_agent.schemas.trend_query:TrendQuery` |
| trend-analysis-agent | output | [`trend-analysis-result-1.1.contract.json`](trend-analysis-agent/output/trend-analysis-result-1.1.contract.json) | TrendAnalysisResult | 1.1 | pydantic 내보내기 | `feature/scope-2026-baseline @ 741860cd1400` | `trend_agent.schemas.trend_analysis_result:TrendAnalysisResult` |
| stock-agent | input | [`stock-agent-request-1.0.contract.json`](stock-agent/input/stock-agent-request-1.0.contract.json) | stock-agent input (TrendAnalysisResult as stock-agent reads it) | 1.0 | 복사 | `main @ 416c37c8695e` | `contracts/schemas/stock-agent-request.schema.json` |
| stock-agent | output | [`stock-agent-result-1.0.contract.json`](stock-agent/output/stock-agent-result-1.0.contract.json) | stock-agent result | 1.0 | 복사 | `main @ 416c37c8695e` | `contracts/schemas/stock-agent-result.schema.json` |
| sales-market-agent | input | [`sales-market-run-request-0.1.contract.json`](sales-market-agent/input/sales-market-run-request-0.1.contract.json) | sales-market-agent /runs request (envelope + input) | - | 복사 | `feat/market-research-agent @ d25cd8e30e4c` | `contracts/schemas/vogue-handoff-0.1-request.schema.json` |
| sales-market-agent | output | [`market-research-result-1.0.contract.json`](sales-market-agent/output/market-research-result-1.0.contract.json) | MarketResearchResult | 1.0 | 복사 | `feat/market-research-agent @ d25cd8e30e4c` | `contracts/schemas/market-research-result.schema.json` |
| sales-market-agent | output | [`sales-market-run-response-0.1.contract.json`](sales-market-agent/output/sales-market-run-response-0.1.contract.json) | sales-market-agent /runs response envelope | - | 복사 | `feat/market-research-agent @ d25cd8e30e4c` | `contracts/schemas/vogue-handoff-0.1-response.schema.json` |
| sales-market-agent | output | [`sales-market-run-error-0.1.contract.json`](sales-market-agent/output/sales-market-run-error-0.1.contract.json) | sales-market-agent /runs error body | - | 복사 | `feat/market-research-agent @ d25cd8e30e4c` | `contracts/schemas/vogue-handoff-0.1-error.schema.json` |
| new-product-agent | input | [`planning-draft-input-0.2.contract.json`](new-product-agent/input/planning-draft-input-0.2.contract.json) | new-product-agent /runs input (four producer payloads) | - | 코드 읽고 작성 | `main @ fef9548cf5c5` | `src/vogue_npp/adapters/handoff.py, src/vogue_npp/prototype/server.py` |
| new-product-agent | output | [`planning-draft-result-1.0.contract.json`](new-product-agent/output/planning-draft-result-1.0.contract.json) | new-product-agent planning draft result | 1.0 | 코드 읽고 작성 | `main @ fef9548cf5c5` | `src/vogue_npp/prototype/server.py` |

## HTTP handoff contract_version

오케스트레이터(`src/vogue_orchestrator/core.py`)가 Agent마다 기대하는 값과 Agent 소스의 값을 비교한다. route는 Agent 저장소가 제공하는 `/runs`이며, 오케스트레이터가 실제로 부르는 경로와는 다를 수 있다 (2026-11-12 `main`: v0.1 실행은 Trend·Stock `/runs` 후 P07 `/reviews`, New Product는 부르지 않음).

| Agent | route | Agent가 받는 contract_version | 읽은 위치 | 오케스트레이터 기대값 (core.py) | 비교 |
|---|---|---|---|---|---|
| trend-analysis-agent | `/v1/agents/trend-analysis-agent/runs` | `vogue-handoff/0.1` | `src/trend_agent/api/server.py:CONTRACT_VERSION` | `vogue-handoff/0.1` (`CONTRACT_VERSION`) | 같음 |
| stock-agent | `/v1/agents/stock-agent/runs` | `vogue-handoff/0.1` | `stock_agent/api.py:HANDOFF_CONTRACT_VERSION` | `vogue-handoff/0.1` (`CONTRACT_VERSION`) | 같음 |
| sales-market-agent | `/v1/agents/sales-market-agent/runs` | `vogue-handoff/0.1` | `src/market_research_agent/http_server.py:CONTRACT_VERSION` | `vogue-handoff/0.1` (`CONTRACT_VERSION`) | 같음 |
| new-product-agent | `/v1/agents/new-product-agent/runs` | `vogue-handoff/0.2` | `src/vogue_npp/adapters/handoff.py:CONTRACT_VERSION` | `vogue-handoff/0.2` (`PROJECT06_CONTRACT_VERSION`) | 같음 |
