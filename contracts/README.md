# contracts — Agent별 입력·출력 계약

오케스트레이터는 모든 Agent의 입력과 출력을 이어 주므로, 각 Agent의 계약을 이 폴더 한 곳에 모아 둡니다.
소유권은 그대로입니다. 각 Agent의 계약은 그 Agent 저장소가 기준이고, 여기 있는 것은 **코드에서 뽑은 복사본**과 출처 기록입니다.
오케스트레이터 자신이 만드는 `StructuredQuery`만 이 저장소가 기준입니다.

> **서비스별 I/O 규정 요약**은 [`SERVICES.md`](SERVICES.md)를 봅니다. 다섯 서비스의 엔드포인트·입력·출력·상태·오류·환경변수를 한 곳에 정리했습니다. 순차 워크플로 계약 패키지(`vogue-workflow/0.1`)는 [`workflow/`](workflow/)에 있습니다. 통합 규칙은 [`../docs/workflow-integration-conventions.md`](../docs/workflow-integration-conventions.md)를 봅니다.
>
> 통합 작업(2026-11-13)이 바꾼 입력·출력과 전달값 목록은 [`../docs/integration-io-change-report.md`](../docs/integration-io-change-report.md)에 있습니다.

이 폴더는 단계 1(현재 계약을 있는 그대로 등록)의 결과입니다. 각 Agent가 받을 입력을 받는 쪽 기준으로 정의하는 일(단계 3)과 오케스트레이터 매핑 모듈(단계 4)은 아직 하지 않았습니다. 전체 목표와 진행 상황은 [`../docs/agent-contract-integration.md`](../docs/agent-contract-integration.md)를 봅니다.

## 구조

```text
contracts/
├── README.md                      이 문서
└── agents/
    ├── SOURCES.md                 스냅샷별 출처·방식, Agent별 handoff contract_version (생성물)
    ├── vogue-orchestrator/
    │   └── output/                StructuredQuery 1.0 (이 저장소 소유)
    ├── trend-analysis-agent/
    │   ├── input/                 TrendQuery
    │   ├── output/                TrendAnalysisResult 1.1
    │   └── vocabulary/            별칭 사전 스냅샷 (질의 구조화 모듈이 사용)
    ├── stock-agent/
    │   ├── input/                 stock-agent가 읽는 TrendAnalysisResult
    │   └── output/                stock-agent result 1.0
    ├── sales-market-agent/
    │   ├── input/                 /runs 요청 (envelope + input)
    │   └── output/                MarketResearchResult 1.0, /runs 응답·오류
    └── new-product-agent/
        ├── input/                 /runs input (producer payload 4개, handoff 0.2)
        └── output/                planning draft result 1.0
```

파일 이름은 `<계약 이름>-<버전>.contract.json`입니다. 버전은 producer payload 버전이고, payload 버전이 없는 계약은 handoff 버전을 붙이거나 생략합니다.

## 파일 형식

Agent 스냅샷(`*.contract.json`)은 모두 같은 모양입니다.

```json
{
  "_provenance": {
    "agent_id": "stock-agent",
    "direction": "output",
    "payload_schema_version": "1.0",
    "method": "copy",
    "source_repo": "KG-RAG-02/stock-agent",
    "source_branch": "main",
    "source_commit": "416c37c8695e",
    "source_files": [{"path": "contracts/schemas/stock-agent-result.schema.json", "sha256": "…"}],
    "handoff": {"route": "/v1/agents/stock-agent/runs", "contract_version": "vogue-handoff/0.1", "detected_from": "stock_agent/api.py:HANDOFF_CONTRACT_VERSION"},
    "generated_by": "scripts/export_agent_contracts.py"
  },
  "json_schema": { "…": "원본 JSON Schema (draft 2020-12)" }
}
```

- `sha256`은 CRLF를 LF로 바꾼 뒤 계산합니다. Windows와 Unix checkout에서 같은 값이 나옵니다.
- `source_commit` 뒤의 `+uncommitted`는 원본 파일에 커밋하지 않은 변경이 있었다는 뜻입니다.
- `handoff`는 그 Agent의 `/runs`가 받는 `contract_version`을 소스 상수에서 읽은 값입니다.

`structured-query-1.0.schema.json`은 이 저장소 소유라 감싸지 않은 JSON Schema 그대로입니다. `trend-query.contract.json`은 예전 형식(`_provenance`, `json_schema`, `vocabulary`, `category_minor_to_major`)을 유지합니다.

## 만드는 방식

| 방식 | 뜻 | 해당 파일 | 스크립트가 다시 쓰나 |
|---|---|---|---|
| 복사 (`copy`) | Agent 저장소가 이미 게시한 JSON Schema를 감싸서 복사 | stock-agent 2개, sales-market-agent 4개 | 예 |
| pydantic 내보내기 (`pydantic`) | Agent 자신의 `.venv` Python으로 모델의 `model_json_schema()` 호출 | trend `TrendAnalysisResult` | 예 |
| 고정 (`pinned`) | 생성 코드가 의존하는 기존 스냅샷. 내용 일치만 검사 | trend `TrendQuery` (`query/vocabulary.py`의 원본) | 아니요 |
| 코드 읽고 작성 (`authored`) | Agent 저장소에 JSON Schema가 없어 코드를 읽고 작성 | new-product-agent 2개 | 아니요. 원본 코드가 바뀌면 `REVIEW`로 알림 |
| 이 저장소 소유 | `query/models.py`에서 생성 | `structured-query-1.0.schema.json` | `export_structured_query_schema.py`가 담당 |

## 갱신과 확인

```bash
py scripts/export_agent_contracts.py            # 복사·pydantic 스냅샷과 SOURCES.md 다시 쓰기
py scripts/export_agent_contracts.py --check    # 원본과 다르면 exit 1 (CI·작업 전 확인용)
py scripts/export_structured_query_schema.py    # StructuredQuery 스키마 (models.py를 바꿨을 때)
```

- 형제 checkout 위치는 기본 `../<저장소 이름>`입니다. 다르면 `VOGUE_TREND_ROOT`, `VOGUE_STOCK_ROOT`, `VOGUE_SALES_MARKET_ROOT`, `VOGUE_NEW_PRODUCT_ROOT`로 지정합니다.
- Trend 스키마는 `<trend>/.venv`의 Python으로 내보냅니다. 다른 Python을 쓰려면 `VOGUE_TREND_PYTHON`을 지정합니다.
- `--check` 결과 표시: `OK` 일치, `STALE` 원본이 바뀜(스크립트를 다시 실행), `REVIEW` 작성한 계약의 원본 코드가 바뀜(코드를 다시 읽고 손으로 갱신), `MISSING` 파일 없음.
- 마지막에 Agent별 handoff `contract_version`을 오케스트레이터 `core.py`가 그 Agent에 기대하는 값(`CONTRACT_VERSION`, new-product-agent는 `PROJECT06_CONTRACT_VERSION`)과 비교해 `MISMATCH`를 표시합니다. 이 비교는 exit code에 반영하지 않습니다.
- `trend-query.contract.json`이 `STALE`이면 Trend의 TrendQuery가 바뀐 것입니다. 스냅샷을 새로 만든 뒤 `py scripts/generate_query_vocabulary.py`로 `query/vocabulary.py`를 다시 생성합니다.
- 별칭 사전(`vocabulary/alias-map.*`)은 `scripts/export_alias_snapshot.py`와 `scripts/generate_alias_data.py`가 담당합니다.

## 코드·문서 지도

| 파일 | 역할 |
|---|---|
| [`../scripts/export_agent_contracts.py`](../scripts/export_agent_contracts.py) | Agent 스냅샷 생성·확인, `SOURCES.md` 생성, handoff 버전 비교. 대상 목록은 파일 안의 `ENTRIES`·`ENVELOPES` |
| [`../scripts/export_structured_query_schema.py`](../scripts/export_structured_query_schema.py) | `StructuredQuery` 1.0 스키마 생성 |
| [`../scripts/generate_query_vocabulary.py`](../scripts/generate_query_vocabulary.py) | TrendQuery 스냅샷 → `src/vogue_orchestrator/query/vocabulary.py` |
| [`../scripts/export_alias_snapshot.py`](../scripts/export_alias_snapshot.py), [`../scripts/generate_alias_data.py`](../scripts/generate_alias_data.py) | 별칭 사전 스냅샷 → `src/vogue_orchestrator/query/alias_data.py` |
| [`agents/SOURCES.md`](agents/SOURCES.md) | 스냅샷별 출처·방식과 handoff 버전 표 (생성물) |
| [`agents/vogue-orchestrator/output/structured-query.md`](agents/vogue-orchestrator/output/structured-query.md) | StructuredQuery 1.0 정의서 (받는 Agent용 요약 §0) |
| [`agents/trend-analysis-agent/input/trend-query.md`](agents/trend-analysis-agent/input/trend-query.md) | TrendQuery 스냅샷 설명 |
| [`agents/trend-analysis-agent/vocabulary/alias-map.md`](agents/trend-analysis-agent/vocabulary/alias-map.md) | 별칭 사전 스냅샷 설명 |

## 이 등록으로 드러난 것 (2026-11-11)

- **new-product-agent handoff 버전 불일치 → `main`에서 해소(2026-11-12):** new-product-agent는 `vogue-handoff/0.2`만 받습니다(2026-11-11 `59dade3`). 등록 당시 오케스트레이터는 `0.1`만 보냈으나, `main` 559a39d에서 `core.py`에 `PROJECT06_CONTRACT_VERSION = "vogue-handoff/0.2"`가 생겼고 v0.1 실행은 New Product를 부르지 않습니다(PlanningDraft 생성기가 오케스트레이터로 이관됨).
- **new-product-agent가 trend 아이템의 `length`를 읽음:** trend는 `length_body`·`length_sleeve`로 내보내므로 값이 항상 비어 있습니다. `planning-draft-input-0.2.contract.json`의 해당 필드 설명에 적었습니다.
