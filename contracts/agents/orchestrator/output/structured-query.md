# StructuredQuery 1.0 — 구조화된 질의 계약

> 상태: **1.0 확정 (2026-11-11, 결정 SQ-F).** 이 문서와 JSON Schema [structured-query-1.0.schema.json](structured-query-1.0.schema.json)이 받는 쪽에 넘기는 계약이다. 모델 원본은 `src/fashion_planner_orchestrator/query/models.py`이며 JSON Schema는 코드에서 생성한다. 현재 화면 경로는 `server.py` → `query.structured.structure_query` → `query.to_trend.to_trend_query` → 계약 기반 순차 workflow이다. Trend에는 `StructuredQuery` 자체가 아니라 첫 품목·첫 시즌을 변환한 `TrendQuery`를 전달한다. 현재 실행과 서비스 간 전달은 [P08 현재 기준](../../../../docs/current-state.md) 및 [workflow 계약](../../../workflow/workflow-contract.md)을 따른다. 소유: **이 저장소(vogue-orchestrator)**. 결정 이력은 [query-structuring.md](../../../../docs/query-structuring.md)에 있다.
> 이 문서는 **받는 쪽**(trend-analysis-agent, 이후 재고 등 다른 Agent)이 각 필드의 뜻과 쓰는 법을 판단할 수 있게 쓴 정의서다. 받는 쪽이 어떤 필드를 어떻게 쓸지는 **받는 쪽이 정한다.** 이 문서의 "활용 예"는 권장이지 강제가 아니다.

## 0. 받는 Agent를 위한 인수인계 (2026-11-11, 1.0 확정)
이 절만 읽어도 `StructuredQuery`를 받아 쓸 수 있도록 요약했다. 자세한 뜻은 §2~§7, 근거는 [query-structuring.md](../../../../docs/query-structuring.md).

**0.1 넘기는 것**
| 무엇 | 위치 | 비고 |
|---|---|---|
| 뜻 (이 문서) | `contracts/agents/vogue-orchestrator/output/structured-query.md` | 필드 §3, 값의 출처 `match` §4, 제외 §5, **조건을 합치는 규칙 §5.1**, 예시 §6 |
| 검증용 JSON Schema | `contracts/agents/vogue-orchestrator/output/structured-query-1.0.schema.json` | 모델에서 생성(`scripts/export_structured_query_schema.py`). 모든 필드 `required`, 모르는 필드 거부 |
| 모델 원본 (생산자) | `src/fashion_planner_orchestrator/query/models.py` `StructuredQuery` | Pydantic 2. 스키마가 표현하지 못하는 규칙(§2 마지막 항목)도 검사 |
| 검증된 예시 | §6 예 1~7 | `tests/test_query_structured_models.py`가 그대로 검증, 예 4~7은 코드가 똑같이 만드는지 `tests/test_query_structured_path.py`가 확인 |

**0.1.1 클래스(코드) 위치** — 모두 `src/fashion_planner_orchestrator/query/`
| 클래스 | 파일 | 계약에서의 자리 |
|---|---|---|
| `StructuredQuery` | `models.py` | 문서 전체 (§2) |
| `TrendTerms`, `SeasonRequest`, `TermRef` | `models.py` | `trend_terms`, `seasons[]`, `style_names[]`·`product_model_names[]` |
| `QueryItem` | `models.py` | `items[]` |
| `CategoryRef` | `models.py` | `items[].category`, `excluded_categories[]` |
| `GenderFilter`, `GenderRef` | `models.py` | `items[].gender`, 그 안의 항목 |
| `AttributeFilter`, `AttributeValues`, `ColorRef`·`MaterialRef`·`PatternRef`·`DetailRef`·`FitRef`·`BodyLengthRef`·`SleeveLengthRef` | `models.py` | `items[].attributes`, `include`/`exclude`, 종류별 항목 |
| `UnsupportedCondition` | `models.py` | `items[].unsupported[]` |
| `OutOfScopeItem` | `models.py` | `out_of_scope_items[]` |
| 표준 값 목록 | `vocabulary.py`(생성 파일), `contract_vocabulary.py`(기장 2종 포함 7종) | 각 `value`의 허용 값 |
| `TrendQuery` 등 (trend의 옛 계약) | `trend_query.py` | §7 비교·변환 대상. **이름이 같은 클래스(`CategoryRef`·`SeasonRequest`·`TermRef`·`TrendTerms`·`GenderRef`·`UnsupportedCondition`)가 `models.py`에도 있고 모양이 다르다** — `StructuredQuery` 쪽은 항상 `models.py` |
| 만드는 코드 | `structured.py`(`structure_query`, `assemble_structured_query`), `draft_v4.py`(LLM 초안·품목별 묶기), `resolve.py`(값·`match` 확정), `to_trend.py`(`to_trend_query`) | §4.1, §7 |

**0.2 받는 쪽이 꼭 알아야 할 것**
1. **품목 단위로 처리한다.** `items`의 품목 하나하나가 독립된 요청이다(§5.1 규칙 1). 질의 전체에 걸린 조건은 이미 **모든 품목에 복사**되어 있으므로 품목 하나만 보고 처리하면 된다. 결과는 `item_id`(i1, i2, …)로 다시 잇는다.
2. **품목 안에서는** 종류끼리 AND, 같은 종류 여러 값은 OR(규칙 2·3). 제외는 하나라도 맞으면 뺀다(규칙 6), 포함·제외가 겹치면 제외 우선(규칙 7).
3. **`category: null`** 은 "모든 카테고리에서 `excluded_categories`를 뺀 것"이다(규칙 4, 예 7). **성별**은 포함(없으면 전체)에서 제외를 뺀 것이다(규칙 5).
4. **`match`로 확실성을 판단한다**(§4). `value: null`(`match: none`) 조건과 `unsupported`는 **거르는 조건이 아니라 알림**이다(규칙 8·10).
5. **`out_of_scope_items`** 는 표준 카테고리 밖이라 처리할 수 없는 품목이다. 찾는 품목이 모두 범위 밖이어도 `items`는 비지 않는다: 질의 전체 조건(시즌 등)만 가진 `category: null` 품목 1개가 생긴다(IT-7′, 2026-11-12). 품목 자리의 말이 속성 값이면("반팔") 그 속성을 가진 `category: null` 품목이 된다(IT-10). 모델은 `[]`도 허용하지만 조립기는 쓰지 않는다.
6. **시즌은 목록**(1개 이상, 여러 개면 OR, 규칙 11), 트렌드명 `style_names`의 `value`는 지금 항상 `null`이다.
7. **버전**: `schema_version` "1.0". 모르는 필드를 거부하므로 1.x에서 필드가 늘면 받는 쪽도 새 스키마로 바꿔야 한다(OQ-2).
8. **경고(`warnings`)는 계약에 들어 있지 않다.** 생산자 결과(`StructuredQueryResult.warnings`, 코드 문자열 목록)에 따로 있다. 받는 쪽에 함께 넘길지(봉투 envelope)는 연결할 때 정한다(§0.4). 받는 쪽에 의미 있는 코드: `include_exclude_conflict`(포함·제외 겹침), `gender_merged_to_unisex`(남+여 → 공용), `season_year_inferred`(연도를 코드가 정함), `only_out_of_scope_items`(찾는 품목이 모두 범위 밖 → 모든 카테고리로, IT-7′), `item_word_used_as_condition`(품목 자리의 말이 속성 값, IT-10), `leading_gender_shared`(첫 품목 앞 성별을 모든 품목에), `condition_item_unknown`(조건의 품목을 못 찾아 질의 전체로 둠), `raw_not_in_query`·`no_conditions_extracted`(LLM 출력 이상), `multiple_values_in_span`(한 표현에서 값 여러 개). trend 입력으로 바꿀 때(§7)의 경고: `items_not_forwarded`·`seasons_not_forwarded`·`no_item_to_forward`·`conditions_not_forwarded`. 전체 목록은 [query-structuring.md](../../../../docs/query-structuring.md).

**0.3 1.0에 넣지 않은 것 (확정, 필요하면 1.x에서 추가)**
| 항목 | 지금 | 필요해지는 때 |
|---|---|---|
| 복사된 조건의 범위 표시(`scope`) | 공통 조건과 품목 자기 조건을 구분하지 않는다(모두 그 품목의 조건) | 받는 쪽이 "질의 전체 조건"만 따로 써야 할 때 |
| `unsupported`의 사유 코드(`reason_code`) | 사람이 읽는 `reason` 문장만 | 받는 쪽이 사유별로 분기할 때 |
| 기준일(`reference_date`) | 문서에 없음. `default_next` 시즌과 연도 추정은 질의한 날짜 기준 | 저장된 결과를 나중에 다시 해석할 때 |
| 품목별 시즌, 범위 밖 품목 조건의 구조화, `missing_fields`의 뜻 | 시즌은 질의 전체, `conditions_raw`는 원문, `missing_fields`는 항상 `[]` | — |

**0.4 생산 상태 및 전달 경계**
- 생산 함수: `fashion_planner_orchestrator.query.structured.structure_query(query, request_id=..., draft_provider=...)` → `result.structured_query`(이 계약), `result.warnings`. LLM 호출은 `fashion_planner_orchestrator.llm_provider.build_draft_provider()`(키는 `.env`). LLM은 해석만 하고 값과 `match`는 코드가 정한다(§4.1).
- 프롬프트 v6, gpt-4o-mini, temperature 0. 예상 질의 30개(dev)에서 **완전 일치 21/30, 형식 오류 0, 조건을 다른 품목에 붙인 경우 0**. 틀린 것은 주로 **조건을 빠뜨린 것**(품목 바로 앞 꾸밈말, 품목 이름 속 기장)이고, 그다음이 일반어를 품목으로 읽음·포함/제외 뒤집힘이다. 따라서 받는 쪽은 "잘못 들어간 조건"보다 "빠진 조건"을 더 자주 만난다. 별도 holdout 20개 측정은 아직이다([query-structuring.md §15.7·§15.8](../../../../docs/query-structuring.md)).
- 개선은 LLM 모델·프롬프트 버전 올리기로 한다. **계약(이 문서·스키마)은 바뀌지 않는다.**
질의 구조화는 독립적인 Agent 호출이 아니라 P08 workflow의 입력 변환 단계다. 현재 전달은 다음과 같다.

| 단계 | 전달 입력 | 구조화 질의 사용 |
|---|---|---|
| Trend | `to_trend_query()`가 만든 `TrendQuery` | 첫 품목·첫 시즌의 조건을 사용 |
| Stock | Trend 결과 및 workflow가 구성한 Stock 요청 | `StructuredQuery` 원문 객체를 직접 받지 않음 |
| P06 Planning Draft | workflow 계약의 Planning Draft 요청 | Trend·Stock 근거와 요청 정보를 받음 |
| P07 Sales Market Review | 검증·보존된 P06 Draft를 바탕으로 만든 P07 요청 | P06/P07 간 직접 호출 없음. 요청 전달은 P08이 담당 |

이 문서에 보존된 모델·프롬프트 품질 측정치는 기록 당시의 개발 측정이며 현재 모델 성능 보증이 아니다. synthetic fixture와 제거된 실행 모드 설명은 이 현행 전달 표의 일부가 아니다.


**0.5 스키마를 바꾸는 절차 (체크리스트)**
1.0은 확정되었다(SQ-F). 필드를 바꾸려면 아래 순서를 따른다. 각 단계 옆은 그 단계를 지키는 테스트·명령이다.
| # | 할 일 | 지키는 테스트·명령 |
|---|---|---|
| 1 | **사용자 결정을 먼저 기록**: [query-structuring.md](../../../../docs/query-structuring.md) §2.2에 결정 행(이유·쉬운 예·예상 JSON), 버전 규칙(필드 추가 = 1.x, 깨지는 변경 = 2.0, `schema_version` 값 변경) | — |
| 2 | 모델 수정: `query/models.py` (필요하면 `contract_vocabulary.py`) | `tests/test_query_structured_models.py` |
| 3 | JSON Schema 다시 만들기: `PYTHONPATH=src py scripts/export_structured_query_schema.py` → `contracts/agents/vogue-orchestrator/output/structured-query-1.0.schema.json` (직접 고치지 않음) | `py scripts/export_structured_query_schema.py --check`, `test_committed_schema_is_the_exporter_output` |
| 4 | 이 정의서: §0(인수인계·클래스 표), §2 모양, §3 필드 표, 필요하면 §5.1 규칙, §6 예시, §7 차이·변환 표, §8 변경 이력 | — (문서) |
| 5 | 테스트의 예시 상수: §6 예시는 `tests/test_query_structured_models.py`의 `EXAMPLE_1`~`EXAMPLE_7`·`SECTION_2_SHAPE`에 **손으로 옮겨 둔 사본**이다(마크다운을 읽지 않음). 문서와 함께 고친다. 필드 순서 검사도 같은 파일에 있다 | `test_query_structured_models.py`, `test_contract_examples_validate_against_the_exported_schema` |
| 6 | `TrendQuery`와의 차이 목록 `INTENDED_DIFFERENCES`(같은 파일). **문서·`trend_terms` 수준의 차이만 잡는다**(품목 안 필드 변화는 잡지 않음) | `test_query_structured_models.py` |
| 7 | 만드는 코드: 조립 `query/structured.py`, 필요하면 LLM 초안 `draft_v4.py`·`resolve.py` | `tests/test_query_structured_path.py`(정의서 예 4~7을 코드가 똑같이 만드는지 포함) |
| 8 | trend 변환: `query/to_trend.py`와 §7 변환 표 | `tests/test_query_to_trend.py` |
| 9 | LLM이 새 필드를 써야 하면 **새 프롬프트 폴더**(v6은 이미 LLM에 보냈으므로 고치지 않고 v7을 만든다), `query/structured.py`의 `STRUCTURED_PROMPT_VERSION` 올리기, `py scripts/render_query_prompt.py` | `tests/test_query_prompt.py`, `render_query_prompt.py --check` |
| 10 | 평가 데이터 라벨(질의 문장은 그대로, W-11): `scripts/build_query_dataset_v3.py`·`build_query_holdout_v2.py` 다시 실행, 평가 스크립트 `scripts/evaluate_query_structuring.py`의 비교 사실 | `test_query_structured_path.py` `DatasetTests` |
| 11 | 화면에 보여야 하면 `server.py` `structure_query_for_ui`, `web/app.js` | `tests/test_ui_query_structuring.py` |
| 12 | 전체 테스트: `PYTHONPATH="src;tests" py -m unittest discover -s tests` (이 PC에서는 `test_connectors` 2개가 F-7로 실패) | — |

**0.6 옛 이름 → 새 이름 (trend의 `TrendQuery` → `StructuredQuery` 1.0)**
| 옛 (`TrendQuery`) | 새 (`StructuredQuery`) | 결정 |
|---|---|---|
| (이름 없음, 버전 없음) | `schema_version: "1.0"` | Y1 |
| `trend_terms.season` (하나) | `trend_terms.seasons[]` (1개 이상) | SQ-2 |
| `trend_terms.item_terms` | `trend_terms.product_model_names` | SQ-1 |
| `requested` (질의 전체에 하나) | `items[]` (품목마다 하나, `item_id`) | IT-1·IT-8 |
| `requested.categories.include` | `items[].category` (품목마다 1개, `null` = 전체) | IT-1·IT-6 |
| `requested.categories.exclude` | `items[].excluded_categories` | X3 |
| `requested.gender` (`{raw, value}` 하나) | `items[].gender.include[]`·`.exclude[]` | G-3 |
| `requested.attributes[]` (`{type, raw, value}`, 포함만, 5종) | `items[].attributes.include.<종류>[]`·`.exclude.<종류>[]` (7종, 기장 2종 추가) | X2·OQ-9′ |
| (없음) | 모든 값의 `match` | Q3 |
| 최상위 `unsupported[]` | `items[].unsupported[]` (+ `polarity`, 9개 type) | IT-5′·OQ-3′ |
| 범위 밖 카테고리는 `unsupported` (type `category`) | 찾는 품목이면 `out_of_scope_items[]`, 빼고 싶은 것이면 그대로 `unsupported` | IT-7 |
| `missing_fields` | `missing_fields` (같음, 항상 `[]`) | A-2 |
거꾸로(새 → 옛) 바꾸는 규칙은 §7 "변환 규칙"(`to_trend_query()`).

## 1. 무엇인가
사용자의 자연어 질의 1개를 파이프라인 **맨 앞**에서 구조화한 결과다. 모든 값은 trend-analysis-agent가 소유한 **K-Fashion 표준 어휘**(별칭 사전으로 연결)로 표현한다(D-29).

```
사용자 질의 ──► [이 저장소] StructuredQuery ──► trend 분석 (지금은 TrendQuery로 변환해서, Action Item)
                                            └─► 다른 Agent (예정, 각자 변환)
```
trend의 기존 계약 `TrendQuery`(스냅샷: [trend-query.md](../../trend-analysis-agent/input/trend-query.md))를 이어받아 이름을 바꾸고, 속성 포함/제외와 출처 표시(`match`)를 더한 것이다. 차이는 §7.

## 2. 전체 형태
조건은 **품목별로** 묶는다(2026-11-11 결정 IT-1~IT-7). 평평한 목록(카테고리 목록 + 속성 목록)에는 어느 속성이 어느 품목의 것인지가 남지 않아, "숏패딩이랑 반팔 니트 원피스"를 받는 쪽이 "니트 반팔 패딩"으로 찾게 되기 때문이다.

```jsonc
{
  "schema_version": "1.0",
  "request_id": "req-001",                 // 오케스트레이터가 발급한 요청 ID
  "original_query": "사용자 원문",
  "trend_terms": {                          // 질의 전체에 걸림 (IT-5″)
    "seasons": [{"year": 2027, "season": "SS", "raw": null, "origin": "default_next"}],  // 1개 이상 (SQ-2)
    "style_names": [{"raw": "발레코어", "value": null}],
    "product_model_names": []               // 옛 item_terms (SQ-1)
  },
  "items": [                                // 찾는 품목, 질의에 나온 순서 (IT-4: 지금 trend에는 items[0]만)
    {
      "item_id": "i1",                      // 품목 순서대로 i1, i2, ... (IT-8)
      "category": {"raw": "블라우스", "major": "상의", "minor": "블라우스", "match": "exact"},  // null = 품목을 말하지 않음 (IT-6)
      "excluded_categories": [],            // 빼고 싶은 카테고리 (CategoryRef)
      "gender": {"include": [{"raw": "여성", "value": "여", "match": "alias"}], "exclude": []},   // G-3
      "attributes": {
        "include": {"color": [], "material": [], "pattern": [], "detail": [], "fit": [],
                    "body_length": [], "sleeve_length": []},          // 기장 2종 (OQ-9′)
        "exclude": {"color": [], "material": [], "pattern": [], "detail": [], "fit": [],
                    "body_length": [], "sleeve_length": []}
      },
      "unsupported": [{"type": "brand", "raw": "자라", "reason": "Product 데이터는 Musinsa뿐", "polarity": "exclude"}]
    }
  ],
  "out_of_scope_items": [                   // 표준 카테고리 밖의 품목과 그 품목에 붙은 조건 원문 (IT-7)
    {"raw": "부츠", "reason": "표준 카테고리(상분류 4·하분류 21)에 없음", "conditions_raw": ["블랙"]}
  ],
  "missing_fields": []
}
```
- `CategoryRef` = `{"raw", "major", "minor", "match"}`
- `AttributeRef` = `{"raw", "value", "match"}`
- `GenderRef` = `{"raw", "value", "match"}` (`AttributeRef`와 같은 모양, `value`는 남·여·공용 또는 `null`)
- **품목 하나는 그 자체로 완전한 조건 묶음이다**(IT-1·IT-3). 품목마다 같은 처리 과정(예: 품목마다 Agent 한 번)을 그대로 돌릴 수 있도록, 질의 전체에 걸리는 조건("블랙으로 셔츠랑 팬츠"의 블랙, "남성 말고")은 **공통 칸 없이 모든 품목에 복사**되어 있다.
- 품목 앞 꾸밈말은 **바로 뒤 품목에만** 붙는다(IT-2). "레오파드 패턴 없는 셔츠랑 스커트" → 레오파드 제외는 셔츠에만. "둘 다"·"모두"·품목 나열 뒤에서 전체를 꾸미는 말("셔츠랑 스커트 블랙으로")은 모든 품목에.
- **예외: 첫 품목 앞의 성별은 모든 품목에 붙는다**(IT-9). "여성 셔츠랑 스커트" → 여성 셔츠 + 여성 스커트. 다른 품목에 따로 성별이 있으면("여성 셔츠랑 남성 바지") 각자 자기 것만. 두 번째 이후 품목 앞의 성별("셔츠랑 여성 스커트")은 IT-2대로 그 품목에만.
- **찾는 품목을 말하지 않은 질의**는 `category: null`인 품목 1개다(IT-6). 뜻은 모든 카테고리에서 `excluded_categories`를 뺀 것이다: "아우터는 빼고 블랙" = 상의·하의·원피스 중 블랙(예 7). 제외를 남은 카테고리로 바꿔 쓰지 않는다(G-3과 같은 원칙).
- **표준 카테고리 밖의 품목**("블랙 부츠"의 부츠)은 품목이 되지 않고 `out_of_scope_items`에 그 품목에 붙은 조건 원문과 함께 남는다(IT-7). 품목으로 만들면 "모든 카테고리의 블랙"으로 넓어지기 때문이다. 찾는 품목이 모두 범위 밖이면 질의 전체 조건만 가진 `category: null` 품목 1개가 생긴다(경고 `only_out_of_scope_items`, IT-7′ — 시즌은 넘어가고 범위 밖 품목에 붙은 조건은 붙지 않는다).
- **품목 자리의 말이 카테고리가 아니라 속성 값**이면("2027SS 여성 반팔"의 반팔 = 소매 기장) 범위 밖 품목이 아니라 그 속성 하나를 가진 `category: null` 품목이 된다(경고 `item_word_used_as_condition`, IT-10). 사전 값 `exact`·`alias`만 인정하고(`approx`는 범위 밖 그대로: "벨트"), 여러 종류로 정해지면 exact 우선, 그다음 소매 기장 → 몸판 기장 → 색 → 소재 → 패턴 → 핏 → 디테일.
- 한 표현이 표준 카테고리 두 개를 가리키면(띄어 쓴 "셔츠 원피스", OQ-10′) 품목 두 개가 되고 조건은 둘 다 같다.
- **시즌·트렌드명은 품목과 따로 질의 전체에 걸린다**(IT-5″). 시즌은 **목록**이다(SQ-2): 사용자가 말한 시즌을 나온 순서대로 모두 담고("올봄이랑 가을" → 2개), 말하지 않았으면 `default_next` 1개라 **항상 1개 이상**이다. 같은 연도·시즌은 한 번만. 시즌이 어느 품목의 것인지는 담지 않는다("봄 블라우스랑 겨울 코트" → 시즌 [SS, FW], 품목 [블라우스, 코트], 한계).
- **빈 값은 `null`이 아니라 `[]`** 다. 속성 키 7개(`color`·`material`·`pattern`·`detail`·`fit`·`body_length`·`sleeve_length`)는 포함·제외 양쪽에 **항상 있다.** §6 예시는 지면상 빈 목록을 생략했다.
- **모든 필드는 항상 출력된다** (JSON Schema에서 모두 `required`). **모르는 필드는 허용하지 않는다** (`additionalProperties: false`). 스키마에서는 속성 종류마다 허용 값 목록(`enum`)이 걸린 `ColorRef`·`MaterialRef`·`PatternRef`·`DetailRef`·`FitRef`·`BodyLengthRef`·`SleeveLengthRef`로 나오지만 모양은 모두 `AttributeRef`와 같다.
- **JSON Schema가 표현하지 못하는 규칙**(생산자인 이 저장소가 지킨다: 모델 `models.py`가 검사하거나, `items` 순서처럼 조립 코드 `query/structured.py`가 만든다): `match = none` ⇔ `value = null`, `minor`가 있으면 `major`는 그 하분류의 상분류, `minor = null`인 카테고리는 `match = exact`, 시즌의 `origin`과 `raw` 관계, `items`의 순서(질의에 나온 순서), `item_id`가 순서와 일치(i1, i2, …). 받는 쪽이 스키마로만 검증하면 이 규칙들은 검사되지 않으므로, 필요하면 받는 쪽이 따로 확인한다.

## 3. 필드 정의
| 필드 | 뜻 | 값 |
|---|---|---|
| `schema_version` | 이 계약의 버전 | `"1.0"`. 호환 변경(필드 추가)은 1.x, 깨지는 변경은 2.0. 단, 이 스키마는 모르는 필드를 허용하지 않으므로 받는 쪽이 스키마로 검증한다면 1.x가 올라갈 때 새 스키마로 바꿔야 한다 (확인 대기 OQ-2) |
| `request_id`, `original_query` | 요청 ID, 사용자 원문 그대로 | 문자열 |
| `trend_terms.seasons[]` | 기획 시즌, 질의에 나온 순서 (SQ-2). 항상 1개 이상 | 항목마다 `{year, season(SS/FW), raw, origin}`. `origin = user_requested`(사용자가 말함) 또는 `default_next`(말하지 않아 질의한 날짜 기준 다음 시즌으로 코드가 채움: 3~8월 → 같은 해 FW, 9~12월 → 다음 해 SS, 1~2월 → 같은 해 SS). `default_next`이면 `raw`는 `null`, `user_requested`이면 사용자가 쓴 시즌 표현 |
| `trend_terms.style_names[]`, `product_model_names[]` | 트렌드명(발레코어 등), 특정 상품의 모델명. `product_model_names`(SQ-1, 옛 이름 `item_terms`)는 **표준 카테고리·속성으로 나타낼 수 없는 모델명에만** 쓴다. 찾는 품목은 `items`에 있다(OQ-8′: "숏패딩"은 패딩 품목 + 기장 크롭). 대부분 빈 목록이고 trend는 지금 읽지 않는다 | `{raw, value}`. 정규화 어휘가 아직 없어 `value`는 `null` (스키마에서도 `null`만 허용. 어휘가 생기면 1.x에서 넓힘) |
| `items[]` | **찾는 품목**마다 하나, 질의에 나온 순서 (IT-1·IT-4). 품목을 말하지 않았으면 `category: null`인 품목 1개(IT-6). 찾는 품목이 모두 범위 밖이면 `[]` | `QueryItem` = `{item_id, category, excluded_categories, gender, attributes, unsupported}` |
| `items[].item_id` | 품목 식별자 (IT-8). `items` 순서대로 `"i1"`, `"i2"`, … 품목을 따로 보낸 뒤(예: 품목마다 Agent) 결과를 다시 이을 때 쓴다 | `^i[1-9][0-9]*$`, 순서와 일치(모델이 검사) |
| `items[].category` | 그 품목의 카테고리 | `CategoryRef` 또는 `null`(= 모든 카테고리 중 `excluded_categories`를 뺀 것) |
| `items[].excluded_categories[]` | **빼고 싶은** 카테고리 (§5). 질의 전체에 걸린 제외는 모든 품목에 복사됨 | `CategoryRef` |
| `CategoryRef.major` / `.minor` | 상분류(상의·하의·아우터·원피스) / 하분류 21개. `minor`가 있으면 `major`는 그 하분류의 상분류(코드가 정함). **`minor`가 있으면 그 하분류만, `minor`가 `null`이면 상분류 전체를 가리킨다.** `minor`가 `null`인 참조는 사용자가 상분류 이름을 직접 말한 경우뿐이라 `match`는 항상 `exact`다 | `major`: 표준 어휘 (**항상 있음**). `minor`: 표준 어휘 또는 `null` |
| `items[].gender.include[]` / `.exclude[]` | **원하는** 성별 / **빼고 싶은** 성별 (G-3). 말하지 않았으면 둘 다 `[]` | `GenderRef`. `raw`는 항상 원문. `value`가 `null`이면 `match`는 `none` |
| (성별의 뜻) | **고를 성별** = 포함에 값이 있으면 그 값들(제외 값은 뺌) · 포함이 비어 있으면 남·여·공용 중 제외 값을 뺀 나머지 · 둘 다 비면 성별로 거르지 않음. `value = null` 항목은 계산에서 빠진다 | 예: 제외 `[남]`만 있으면 **여·공용** (§4.3) |
| `items[].attributes.include.<종류>[]` | **원하는** 속성 값 | `AttributeRef`. 종류별 허용 값은 표준 어휘(색 21, 소재 26, 패턴 19, 디테일 61(디테일·넥라인·칼라 포함), 핏 7, 몸판 기장 `body_length` 9(크롭·노멀·하프·롱·미니·니렝스·미디·발목·맥시), 소매 기장 `sleeve_length` 5(민소매·반팔·캡·7부소매·긴팔)) |
| `items[].attributes.exclude.<종류>[]` | **빼고 싶은** 속성 값 (§5) | `AttributeRef` |
| `AttributeRef.value` | 표준 값. 표준 값으로 정하지 못했으면 `null` (원문은 `raw`에 남음) | 표준 어휘 또는 `null` |
| `match` | 값이 **어떻게 정해졌는지** (§4) | `exact`·`alias`·`approx`·`llm`·`none` (`CategoryRef`에는 `none`이 없다 — 정하지 못한 카테고리는 찾는 품목이면 `out_of_scope_items`, 빼고 싶은 것이면 그 품목의 `unsupported`) |
| `items[].unsupported[]` | 그 품목에 대해 요청했지만 표준 어휘·이 계약으로 표현할 수 없는 조건(질의 전체에 걸린 것은 모든 품목에 복사, IT-5′) (브랜드·가격·시장·채널·연령·표준 밖 카테고리·세이프·추상 표현 등. 기장은 속성 칸에 담는다). 조용히 버리지 않고 기록 | `{type, raw, reason, polarity}`. `type`은 **9개 중 하나**(OQ-3′): `brand`(브랜드)·`category`(표준 밖 카테고리)·`attribute`(세이프 등 표준 밖 속성)·`abstract`(추상 표현)·`price`(가격)·`market`(시장)·`channel`(판매 채널)·`age`(연령)·`other`(그 밖). 받는 쪽은 `type`별로 처리를 정할 수 있다. `polarity = include`(원함) 또는 `exclude`(빼고 싶음, 예: "자라 말고"). `polarity`는 항상 있다 (기본값 없음) |
| `out_of_scope_items[]` | **찾는 품목이지만 표준 카테고리 밖**인 것("블랙 부츠"의 부츠). 품목으로 만들지 않는다 (IT-7) | `{raw, reason, conditions_raw}`. `conditions_raw` = 그 품목에 붙은 조건과 질의 전체 조건의 원문 (값으로 정하지 않음) |
| `missing_fields[]` | 예약 필드. 지금은 항상 `[]` (정의 미정, 가정 A-2) | — |

## 4. `match` — 값의 출처 (받는 쪽 판단용)
같은 표준 값이라도 **얼마나 확실한지**가 다르다. 받는 쪽은 이 값을 보고 필터로 쓸지, 순위에만 반영할지, 표시만 할지 정한다.

| `match` | 뜻 | 어떻게 정해지나 | 예 | 활용 예 (권장) |
|---|---|---|---|---|
| `exact` | 원문이 표준 값과 같음 | 사전(표준 값) | "블랙" → 블랙 | 그대로 조건으로 사용 |
| `alias` | 사전에 등록된 **같은 뜻** 표기 | 사전(별칭) | "슬랙스" → 팬츠 | 그대로 조건으로 사용 |
| `approx` | 사전에 등록된 **가까운 뜻** (같은 뜻은 아님) | 사전(근사) | "버터옐로우" → 옐로우, "슬림" → 타이트 | trend는 근사 값을 검색 필터로 쓰지 않는 원칙이 있음 → 표시·참고 |
| `llm` | 사전에 없어 **LLM이 표준 목록에서 고른** 값 (표준 목록 밖 값은 나오지 않음) | LLM 제안 → 코드가 표준 목록 확인 | "오버핏" → 오버사이즈 | 확실성이 낮음 → 표시·참고, 또는 받는 쪽 판단. 이 표현은 사전 추가 후보로 수집됨 |
| `none` | 표준 값으로 정하지 못함 (`value = null`). ① 사전이 "값 없음"으로 명시했거나(LLM 제안은 쓰지 않음, 예: detail "카라"), ② 사전에 없고 LLM도 표준 목록 안에서 같은 뜻의 값을 제안하지 못한 경우 | — | "연두" (색 21개에 같은 뜻 값이 없음) | 조건으로 쓸 수 없음. 응답의 한계로 알리기. 카테고리에는 쓰지 않는다 (아래 보충 규칙) |

**종류별 보충 규칙**
| 경우 | 규칙 |
|---|---|
| 상분류 이름 ("상의", "하의", "아우터") | 표준 목록과 직접 비교해 같으면 `exact` (사전은 이 단어를 힌트로만 돌려주므로 사전 결과를 쓰지 않는다). **"원피스"는 예외**: 하분류 드레스(`alias`, OQ-5′) |
| 카테고리를 표준 값으로 정하지 못함 | `CategoryRef`(`major`·`minor` 모두 `null`)로 두지 않는다. **찾는 품목이면 `out_of_scope_items`**(IT-7), **빼고 싶은 것이면 품목의 `unsupported`(type `category`)** 에 기록한다. 사전이 범위 밖으로 명시한 품목(예: 가방·모자)과 사전에 없는 품목은 `reason` 문장으로 구분한다 |
| 한 표현에 여러 값 ("블랙 화이트") | LLM이 값마다 따로 낸다. 그래도 한 표현에서 값이 여러 개 나오면 값마다 별도 `AttributeRef`를 만들고 `raw`는 같은 원문을 쓴다 |
| 기장 | "미니" → `body_length` 미니(`exact`), "반팔" → `sleeve_length` 반팔. 하위 종류 안의 기장("미니스커트", "숏패딩")은 카테고리와 기장 두 조건으로 나눈다 (OQ-9′·OQ-8′, §4.3) |
| 성별 | 고정 표로 정하고 `alias` (G-1, §4.3). 포함/제외 목록이고, 포함에 남과 여가 함께 나오면 공용 하나로 합친다 (G-3, §4.3) |
| 카테고리 하위 종류 ("블레이저", "미니스커트") | 하분류 목록에 없는 경우에만 LLM이 상위 **하분류**를 제안 → `llm`. 사용자가 말한 하분류·상분류는 그대로 두고 더 넓게 바꾸지 않는다 (G-2, §4.3) |

### 4.1 값이 정해지는 과정 (결정 Q3 = γ)
`match`는 **항상 이 저장소의 코드가 정한다.** LLM은 `match`를 쓰지 않는다.

```
① LLM (1회 호출): 질의에서 조건 표현을 찾고 [종류, 포함/제외, 표준 목록 안의 제안 값]을 붙인다
                  LLM은 별칭 사전을 받지 않는다. 표준 목록(§4.2)만 받는다
② 코드: 표현마다 원문을 별칭 사전에서 찾는다
     사전에 있다  → 사전 값 사용, LLM 제안은 쓰지 않음        → exact / alias / approx
     사전이 "값 없음" → LLM 제안도 쓰지 않음                   → none (속성) / unsupported (카테고리)
     사전에 없다  → LLM 제안이 그 종류의 표준 목록 안에 있나?
                     있다 → 그 값 사용                      → llm
                     없다 → 값 없음 (value = null)          → none
③ 추상 표현·브랜드·가격 등은 값을 정하지 않고 unsupported에 기록
```
- **사전이 항상 LLM보다 우선한다.** 그래서 같은 표현은 언제나 같은 값이 되고, LLM은 사전이 모르는 표현에만 영향을 준다.
- 원문 전체로 사전에 없으면, 그 **표현 안의 단어**로 다시 찾는다 (예: "니트 소재" → "니트"). 질의 문장 전체를 사전으로 훑지는 않는다 (예: "겨울"의 "울"을 소재로 잡는 오탐을 막기 위해).

**예 3의 값이 정해진 과정** ("꾸안꾸 느낌 버터옐로우 오버핏 후드티 기획해줘, 블랙은 빼고")
| 표현 | ① LLM이 낸 것 | ② 사전 조회 결과 | 최종 `value` | `match` |
|---|---|---|---|---|
| 후드티 | 카테고리·포함, 제안 "후드티" | 표준 값과 같음 | 후드티 | `exact` |
| 블랙 | 색·**제외**, 제안 "블랙" | 표준 값과 같음 | 블랙 | `exact` |
| 버터옐로우 | 색·포함, 제안 "옐로우" | 사전 근사 항목 | 옐로우 | `approx` |
| 오버핏 | 핏·포함, 제안 "오버사이즈" | 사전에 없음 → 제안이 핏 목록 안 | 오버사이즈 | `llm` |
| 꾸안꾸 느낌 | 추상 표현 | 조회하지 않음 | — | `unsupported` (type `abstract`) |

### 4.2 표준 목록과 별칭 사전
| | 표준 목록 (표준 어휘) | 별칭 사전 |
|---|---|---|
| 뜻 | `value`에 들어갈 수 있는 값 전부 | 여러 표현을 표준 값으로 연결하는 표 |
| 소유 | trend-analysis-agent (K-Fashion 기준) | trend-analysis-agent |
| 크기 | 상분류 4, 하분류 21, 색 21, 소재 26, 패턴 19, 디테일 61, 핏 7, 몸판 기장 9, 소매 기장 5, 시즌 2, 성별 3 | 약 500개 항목 |
| 예 | 팬츠, 블랙, 오버사이즈 | 슬랙스 → 팬츠 (같은 뜻), 슬림 → 타이트 (근사) |

받는 쪽은 `value`가 `null`이 아니면 **항상 표준 목록 안의 값**이라고 믿어도 된다. 표준 목록은 trend 저장소의 `docs/schemas/vocabulary.md`에 정의되어 있다.

### 4.3 성별과 카테고리 하위 종류 (결정 G-1·G-2, 2026-11-10 사용자 확정)

**G-1 성별 — 고정 표로 정하고 `alias`**

성별(남·여·공용)은 trend 별칭 사전에 없다. 그대로 γ를 적용하면 "여성"처럼 뻔한 표현도 LLM 제안(`llm`)이 되어 확실성이 낮은 값처럼 보인다. 표준 값이 3개뿐이고 표현이 거의 고정이므로, 이 저장소의 `configs/`에 작은 고정 표를 두고 코드로 정한다.

| 원문 (예) | `value` | `match` |
|---|---|---|
| 여, 남, 공용 | 같은 값 | `exact` |
| 여성, 여자, 우먼 | 여 | `alias` |
| 남성, 남자, 맨즈 | 남 | `alias` |
| 남녀공용, 유니섹스 | 공용 | `alias` |
| 표에 없는 표현 | LLM 제안이 남·여·공용 중 하나면 그 값 | `llm` |

- 이 표는 trend 사전에 **없는 속성**에 대한 것이라 "질의 전용 별칭 사전을 만들지 않는다"(사전 중복 금지, [query-structuring.md](../../../../docs/query-structuring.md))와 충돌하지 않는다. trend 사전에 성별이 생기면 그쪽을 따르고 이 표는 지운다.
- 표 항목 (2026-11-10 U2 확정): 여 = 여성·여자·여성용·우먼·위민즈·women·womens, 남 = 남성·남자·남성용·맨즈·men·mens, 공용 = 남녀공용·유니섹스·unisex. "젠더리스"는 같은 뜻이 아니라 넣지 않았다. 비교는 사전과 같은 방식(소문자, 공백·`-`·`_` 무시)이라 "남녀 공용"도 맞는다. 위치: `configs/query_structuring.json`의 `gender.aliases`.
- **성별 제외와 여러 성별 (결정 G-3, 2026-11-10 사용자 확정, U3.5 구현)**: 성별도 카테고리·속성처럼 `include`/`exclude` 목록이다.

| 질의 | `gender` | 고를 성별 |
|---|---|---|
| 성별을 말하지 않음 | `{"include": [], "exclude": []}` | 거르지 않음 |
| "여성 코트" | `{"include": [{"raw": "여성", "value": "여", "match": "alias"}], "exclude": []}` | 여 |
| "남성 말고 셔츠" | `{"include": [], "exclude": [{"raw": "남성", "value": "남", "match": "alias"}]}` | **여·공용** |
| "여성 말고 니트" | `{"include": [], "exclude": [{"raw": "여성", "value": "여", "match": "alias"}]}` | 남·공용 |
| "남녀 셔츠" | `{"include": [{"raw": "남녀", "value": "공용", "match": "alias"}], "exclude": []}` | **공용** |
| "남성·여성 모두 볼 수 있는 셔츠" | `{"include": [{"raw": "남성, 여성", "value": "공용", "match": "alias"}], "exclude": []}` + 경고 `gender_merged_to_unisex` | 공용 |
| "유니섹스 후드" | `{"include": [{"raw": "유니섹스", "value": "공용", "match": "alias"}], "exclude": []}` | 공용 |
| 표에 없고 LLM도 정하지 못한 표현 | `{"include": [{"raw": "...", "value": null, "match": "none"}], "exclude": []}` | 거르지 않음 |

  - 코드는 "남성 말고"를 여·공용으로 **바꿔 쓰지 않는다.** 사용자가 말한 대로 제외 `[남]`을 기록하고, 뜻(나머지 성별)은 이 정의서가 정한다. 성별 표준 값이 3개뿐이라 둘은 같은 뜻이다.
  - 포함에 남과 여가 따로 나오면 코드가 공용 하나로 합친다(사용자 결정: 남녀 = 공용). 합친 항목의 `raw`는 두 원문을 ", "로 이은 것, `match`는 둘 중 덜 확실한 쪽(exact → alias → llm 순). 제외 쪽에는 적용하지 않는다("남성·여성 말고"는 제외 2개 = 공용만).
  - "남녀"는 성별 표의 공용 표기로 추가한다. 성별은 `unsupported`로 보내지 않는다.

**G-2 카테고리 하위 종류 — 하분류 목록에 없을 때만 LLM이 상위 하분류를 제안**

규칙:
1. 사용자가 말한 표현이 **표준 하분류이거나 사전으로 하분류가 정해지면 그 하분류를 그대로 쓴다.** 더 넓은 상분류로 바꾸지 않는다. 예: "스커트"는 스커트(`exact`)이고, "하의"로 바꾸지 않는다.
2. 표현이 하분류 21개 중 어디에도 직접 해당하지 않는 **하위 종류**일 때만(예: "블레이저", "미니스커트"), LLM이 그것을 포함하는 **하분류**를 제안할 수 있다 → `match: llm`.
3. LLM 제안은 **하분류로만** 받는다. 하위 종류를 상분류(상의·하의·아우터·원피스)로 올리는 제안은 받지 않는다 → 맞는 하분류가 없으면 찾는 품목은 `out_of_scope_items`, 빼고 싶은 것은 `unsupported`(type `category`). 상분류는 사용자가 상분류 이름을 직접 말했을 때만 쓴다(`exact`).
4. 가방·신발·모자처럼 **범위 밖 품목**은 찾는 것이면 `out_of_scope_items`, 빼고 싶은 것이면 `unsupported`.

| 원문 | 사전 결과 (실제 확인) | 최종 | `match` |
|---|---|---|---|
| 스커트 | 표준 값 | 하분류 스커트 (하의로 바꾸지 않음) | `exact` |
| 데님 스커트 | 전체는 없음 → 표현 안 단어 "스커트" | 하분류 스커트 | `exact` |
| 미니스커트 | 없음 | LLM 제안 스커트 → 하분류 스커트. "미니"는 기장 `body_length` 미니(`exact`)로 따로 (OQ-9′) | `llm` |
| 블레이저 | 없음 | LLM 제안 재킷 → 하분류 재킷 | `llm` |
| 하의 | 힌트만 | 상분류 하의 (사용자가 직접 말함) | `exact` |
| 원피스 | 드레스의 별칭 | 하분류 드레스 (점프수트는 포함 안 됨, OQ-5′) | `alias` |
| 셔츠 원피스 (띄어 씀) | 전체는 없음 → 단어마다 | 하분류 셔츠 **와** 하분류 드레스, 두 항목 (OQ-10′) | `exact`, `alias` |
| 셔츠원피스 (붙여 씀) | 없음 | LLM 제안 드레스 하나 (OQ-10′) | `llm` |
| 니트 원피스 | 전체는 없음 → 단어마다("니트"는 카테고리 아님) | 하분류 드레스. "니트"는 LLM이 소재 조건으로 따로 낸다 | `alias` |
| 숏패딩 | 카테고리에는 없음, 기장에는 근사 크롭 | LLM 제안 패딩 → 하분류 패딩 + `body_length` 크롭(`approx`). `product_model_names`(옛 `item_terms`)에는 넣지 않음 (OQ-8′) | `llm`, `approx` |
| 가방 | 값 없음(범위 밖) | 찾는 것이면 `out_of_scope_items`, 빼고 싶은 것이면 `unsupported` | — |

- 근거: trend 표준 문서도 "블레이저·트렌치코트는 재킷·코트로만 남는다"고 적어 데이터 쪽이 같은 방식으로 다룬다. `llm` 표시로 받는 쪽이 신뢰도를 판단한다.
- LLM이 하위 종류에 상분류를 제안하면(예: "롱스커트" → 하의) 받지 않는다(찾는 품목이면 `out_of_scope_items`, 빼고 싶은 것이면 `unsupported`). 그래서 프롬프트 v2~v6은 "가장 구체적인 하분류를 제안하라"고 지시한다.
- "원피스"는 **하분류 드레스**(`alias`)다(OQ-5′, 2026-11-10 사용자 결정). 코드는 사전을 상분류 이름보다 먼저 본다(상의·하의·아우터는 사전에 힌트만 있어 상분류 이름으로 `exact`).
- `llm`으로 정해진 하위 종류 표현은 사전 추가 후보다. 단, "미니스커트 → 스커트"처럼 같은 뜻이 아니라 **하위 종류**인 경우 D-29(같은 뜻 표기만 추가)에 따라 `aliases`에 넣을 수 있는지는 따로 판단한다.

### 4.4 무엇이 저장되나
`match`는 결과 JSON 안의 필드로 받는 쪽에 **전달**된다. 이 저장소는 결과를 DB·로그에 **보관하지 않는다.** `match = llm` 표현을 모아 사전 추가 후보로 쓰는 기록 저장은 아직 정하지 않은 Action Item이다.

## 5. 제외 조건 (결정 X3)
**정의**: 사용자가 "기획 대상에서 빼고 싶다"고 명시한 카테고리·속성 값.
1. **추적**: 사용자가 뺀 것은 표준 값으로 정해졌든 아니든 **모두 기록**한다 (원문 `raw` + `match`). 표준 어휘로 표현할 수 없는 제외(브랜드 등)는 품목의 `unsupported`에 `polarity: "exclude"`로 남긴다.
2. **전달**: 표준 값으로 정해진 것은 그 표준 값으로 전달해, 받는 쪽이 **같은 표준 값을 가진 아이템·상품을 결과에서 뺄 수 있게** 한다.
3. **받는 쪽 적용 (권장)**:
   - 데이터가 그 값과 **확실히 일치**하면 뺀다.
   - 데이터에 그 정보가 **없거나**(예: 색이 기록되지 않은 상품) 근사로만 일치하면, 해당한다는 근거가 없으므로 남기고 **그 개수를 알린다**.
   - 같은 값을 포함과 제외에 동시에 요청하면 둘 다 기록되고 경고가 붙는다. 사용자가 "원하지 않는다"고 명시했으므로 **제외를 우선**하는 것을 권장한다.
4. **카테고리 제외의 범위**: `minor`가 있는 제외는 **그 하분류만** 뺀다. 상분류는 없어지지 않는다 (예 2).

## 5.1 조건을 합치는 규칙 (결정 CR-1, 2026-11-11)
이 계약에서 조건이 **무엇을 뜻하는지**를 정한다. 받는 쪽이 그 뜻을 어떻게 적용할지(필터·순위·표시)는 받는 쪽이 정한다(R0). 이 규칙이 없으면 같은 결과를 받는 쪽마다 다르게 읽는다 — 품목별로 묶기로 한 이유(IT-1)도 받는 쪽이 속성을 모든 품목에 똑같이 걸었기 때문이다.

| # | 범위 | 규칙 | 예 |
|---|---|---|---|
| 1 | 품목끼리 | **품목은 서로 독립된 요청이다.** 품목마다 따로 찾고, 한 품목의 조건을 다른 품목에 걸지 않는다. 결과는 품목별로 낸다(`item_id`로 잇는다, IT-8) | "스키니 청바지랑 루즈핏 셔츠" → (청바지 ∧ 스키니), (셔츠 ∧ 루즈). 루즈 청바지는 어느 품목에도 맞지 않는다 |
| 2 | 품목 안: 종류끼리 | 서로 다른 조건(카테고리, 성별, 속성 종류)은 **모두 만족**해야 한다 (AND) | "반팔 니트 원피스" → 드레스 ∧ 니트 ∧ 반팔 |
| 3 | 품목 안: 같은 종류 여러 값 | **하나라도 맞으면** 된다 (OR) | 색 [블랙, 화이트] → 블랙이거나 화이트 |
| 4 | 카테고리 | `category`가 있으면 그 카테고리(`minor`가 있으면 그 하분류, 없으면 상분류 전체), `null`이면 **모든 카테고리**. 여기서 `excluded_categories`를 뺀다(제외 하분류는 그 하분류만, X3) | `null` + 제외 [아우터] = 상의·하의·원피스 |
| 5 | 성별 | 포함에 값이 있으면 그 값들, 없으면 남·여·공용 전체. 여기서 제외 값을 뺀다. 둘 다 비면 성별로 거르지 않는다 (G-3) | 제외 [남] = 여·공용 |
| 6 | 속성 제외 | 제외 값 중 **하나라도 해당하면** 뺀다 | 색 제외 [블랙, 네이비] → 블랙도 네이비도 아닌 것 |
| 7 | 포함과 제외가 같은 값 | **제외가 우선**한다(권장, X3). 이때 경고 `include_exclude_conflict`가 붙는다 | — |
| 8 | 값을 정하지 못한 조건 (`value: null`, `match: none`) | **조건으로 쓰지 않는다**(거르는 데 참여하지 않음). 원문은 남으므로 "반영하지 못한 조건"으로 알린다 | "연두색" |
| 9 | 확실성 (`match`) | 뜻은 같다(조건이다). `approx`·`llm`을 필터로 쓸지 순위·표시에만 쓸지는 받는 쪽이 정한다(§4) | — |
| 10 | `unsupported` | **거르는 조건이 아니다.** 반영하지 못했다고 알리는 기록이다 | "자라 말고" |
| 11 | 시즌 (`seasons`) | 질의 전체에 걸리고, 여러 개면 **그중 하나에 맞으면** 된다 (OR). 지금 trend에는 첫 시즌만 넘긴다(SQ-2) | [2027 SS, 2027 FW] |
| 12 | 트렌드명 (`style_names`) | 질의 전체에 걸리고, 여러 개면 하나라도 맞으면 된다 (OR) | — |
| 13 | 데이터에 정보가 없을 때 | 데이터에 그 속성이 기록되지 않은 아이템·상품은 조건을 **만족한다는 근거가 없다.** 포함 조건이면 확실히 맞는 것과 구분해 알리고, 제외 조건이면 빼지 않고 남긴 뒤 개수를 알린다(권장, X3) | 색 미기록 니트 |

참고: trend의 현재 검색(`retrieval/scoring.py` `check_requested`)은 규칙 2·3과 같은 방식(같은 종류 OR, 종류끼리 AND)이지만, 품목이 하나뿐인 `TrendQuery`를 받으므로 규칙 1은 지금 "첫 품목만 넘김"(IT-4)으로 지켜진다.

## 6. 예시
빈 목록과 빈 속성 키는 생략했다. 전체 문서는 예 3. 예시는 `tests/test_query_structured_models.py`가 그대로 검증하고, 예 4~7은 `tests/test_query_structured_path.py`가 코드로 똑같이 만들어지는지 확인한다.

### 예 1. "블랙 말고 니트웨어 보여줘"
```jsonc
"items": [
  {"item_id": "i1",
   "category": {"raw": "니트웨어", "major": "상의", "minor": "니트웨어", "match": "exact"},
   "attributes": {"include": {}, "exclude": {"color": [{"raw": "블랙", "value": "블랙", "match": "exact"}]}}}
]
```
받는 쪽 해석 예: 니트웨어 중 주 색이 블랙인 것은 빼고, 색이 기록되지 않은 니트는 남기고 "색 미확인 n개 포함"을 알린다.

### 예 2. "슬랙스 빼고 하의로 기획해줘" — 상분류는 남는다
```jsonc
"items": [
  {"item_id": "i1",
   "category":            {"raw": "하의",   "major": "하의", "minor": null,   "match": "exact"},
   "excluded_categories": [{"raw": "슬랙스", "major": "하의", "minor": "팬츠", "match": "alias"}]}
]
```
받는 쪽 해석: 품목은 **하의 전체**(`minor = null`), 제외는 **하분류 팬츠만**(`minor = 팬츠`). 결과는 하의 중 팬츠를 뺀 **청바지·스커트·레깅스·조거팬츠**다. 제외 항목의 `major: 하의`는 팬츠가 속한 상분류를 알려 주는 정보일 뿐, 하의 전체를 빼라는 뜻이 아니다. (trend의 현재 검색도 `minor`가 있으면 하분류로만 비교한다.) 하의인데 하분류가 기록되지 않은 아이템은 팬츠라는 근거가 없으므로 남는다.

### 예 3. 출처가 섞인 경우 — "꾸안꾸 느낌 버터옐로우 오버핏 후드티 기획해줘, 블랙은 빼고"
```jsonc
{
  "schema_version": "1.0",
  "request_id": "req-003",
  "original_query": "꾸안꾸 느낌 버터옐로우 오버핏 후드티 기획해줘, 블랙은 빼고",
  "trend_terms": {"seasons": [{"year": 2027, "season": "SS", "raw": null, "origin": "default_next"}],
                  "style_names": [], "product_model_names": []},
  "items": [
    {
      "item_id": "i1",
      "category": {"raw": "후드티", "major": "상의", "minor": "후드티", "match": "exact"},
      "excluded_categories": [],
      "gender": {"include": [], "exclude": []},
      "attributes": {
        "include": {"color": [{"raw": "버터옐로우", "value": "옐로우", "match": "approx"}],
                    "material": [], "pattern": [], "detail": [],
                    "fit": [{"raw": "오버핏", "value": "오버사이즈", "match": "llm"}],
                    "body_length": [], "sleeve_length": []},
        "exclude": {"color": [{"raw": "블랙", "value": "블랙", "match": "exact"}],
                    "material": [], "pattern": [], "detail": [], "fit": [], "body_length": [], "sleeve_length": []}
      },
      "unsupported": [{"type": "abstract", "raw": "꾸안꾸 느낌", "reason": "표준 어휘로 바꿀 수 없는 추상 표현", "polarity": "include"}]
    }
  ],
  "out_of_scope_items": [],
  "missing_fields": []
}
```
받는 쪽 해석 예 (trend): 후드티, 블랙 제외는 확실한 조건(`exact`). 옐로우(`approx`)와 오버사이즈(`llm`)는 확실하지 않으므로 필터 대신 순위나 표시에 쓰고, "꾸안꾸"는 반영할 수 없다고 알린다.

### 예 4. "가방이랑 신발은 빼고, 자라 말고" — 찾는 품목을 말하지 않음
```jsonc
"items": [
  {"item_id": "i1",
   "category": null,
   "unsupported": [
     {"type": "category", "raw": "가방", "reason": "표준 카테고리(상분류 4·하분류 21)에 없음", "polarity": "exclude"},
     {"type": "category", "raw": "신발", "reason": "표준 카테고리(상분류 4·하분류 21)에 없음", "polarity": "exclude"},
     {"type": "brand", "raw": "자라", "reason": "Product 데이터는 Musinsa뿐", "polarity": "exclude"}
   ]}
]
```
참고: 실제 사전에서 가방은 "값 없음"(범위 밖으로 명시), 신발은 "사전에 없음"이다. 코드(`resolve.py`)는 둘을 구분하는 사유 키(`null_value`·`not_in_dictionary`)를 내지만, 지금 `reason` 문장은 둘 다 같은 category 문장이다. 사유 키를 서로 다른 문장으로 바꾸는 것은 U4다(계획 §4 U4 할 일 3).

받는 쪽 해석 예: trend 데이터에는 가방·신발이 원래 없어 영향이 없다. 제외 요청은 추적 기록으로 남는다. 브랜드를 다루는 Agent가 생기면 `polarity: exclude`를 보고 뺄 수 있다.

### 예 5. "남성 제외하고 숏패딩이랑 반팔 니트 원피스 보여줘" — 품목 두 개, 공통 조건은 복사
```jsonc
"items": [
  {"item_id": "i1",
   "category": {"raw": "숏패딩", "major": "아우터", "minor": "패딩", "match": "llm"},
   "gender": {"include": [], "exclude": [{"raw": "남성", "value": "남", "match": "alias"}]},
   "attributes": {"include": {"body_length": [{"raw": "숏패딩", "value": "크롭", "match": "approx"}]}, "exclude": {}}},
  {"item_id": "i2",
   "category": {"raw": "원피스", "major": "원피스", "minor": "드레스", "match": "alias"},
   "gender": {"include": [], "exclude": [{"raw": "남성", "value": "남", "match": "alias"}]},
   "attributes": {"include": {"material": [{"raw": "니트", "value": "니트", "match": "exact"}],
                              "sleeve_length": [{"raw": "반팔", "value": "반팔", "match": "exact"}]}, "exclude": {}}}
]
```
받는 쪽 해석(§5.1 규칙 1·2): i1 = 크롭 기장 패딩, i2 = 반팔 니트 드레스. 니트·반팔은 패딩에 걸리지 않는다. 성별 제외는 질의 전체에 걸려 두 품목에 모두 있다. 지금 trend에는 첫 품목(패딩)만 넘어간다(§7, IT-4).

### 예 6. "블랙 부츠랑 셔츠 보여줘" — 범위 밖 품목
```jsonc
"items": [
  {"item_id": "i1", "category": {"raw": "셔츠", "major": "상의", "minor": "셔츠", "match": "exact"}}
],
"out_of_scope_items": [
  {"raw": "부츠", "reason": "표준 카테고리(상분류 4·하분류 21)에 없음", "conditions_raw": ["블랙"]}
]
```
블랙은 부츠의 꾸밈말이라 셔츠에 붙지 않는다(IT-2). 부츠는 품목이 아니므로 trend로 가지 않고, 사용자에게 "부츠는 다룰 수 없음"을 알릴 근거로 남는다.

### 예 7. "아우터는 빼고 블랙으로" — 품목을 말하지 않음 (IT-6)
```jsonc
"items": [
  {"item_id": "i1",
   "category": null,
   "excluded_categories": [{"raw": "아우터", "major": "아우터", "minor": null, "match": "exact"}],
   "attributes": {"include": {"color": [{"raw": "블랙", "value": "블랙", "match": "exact"}]}, "exclude": {}}}
]
```
받는 쪽 해석: **상의·하의·원피스 중 블랙**. 코드는 제외를 상의·하의·원피스 세 품목으로 펼치지 않는다(지금은 첫 품목만 trend로 가므로 펼치면 상의만 넘어간다).

## 7. trend의 현재 계약(`TrendQuery`)과의 차이
| 항목 | `TrendQuery` (trend 현재) | `StructuredQuery` 1.0 |
|---|---|---|
| 이름·소유 | `TrendQuery`, trend 소유 | `StructuredQuery`, 이 저장소 소유 (Y1) |
| 버전 | 없음 | `schema_version` |
| 묶는 단위 | 질의 전체에 `requested` 하나(카테고리 목록 + 속성 목록) | **품목별 `items[]`** (IT-1·IT-3), 범위 밖 품목은 `out_of_scope_items` (IT-7). trend로 넘길 때는 아래 "품목 수 제한" |
| 속성 | `attributes: [{type, raw, value}]` (포함만, 기장 없음) | `attributes: {include: {종류: [...]}, exclude: {종류: [...]}}` (X2), 기장 2종 포함 7종 (trend로 넘길 때 기장은 "반영되지 않은 조건") |
| 출처 표시 | 없음 | 모든 값에 `match` |
| 지원 불가 조건 | 최상위 `unsupported: [{type, raw, reason}]` | 품목 안 `items[].unsupported`, + `polarity`, `type`은 9개 닫힌 목록 |
| 카테고리 `major` | `null` 가능 | 항상 있음 (정하지 못한 카테고리는 `out_of_scope_items` 또는 `unsupported`) |
| 성별 | `gender: {raw, value}` 하나 또는 `null` (`raw`는 `null` 가능), 포함만 | `gender: {include: [...], exclude: [...]}`, `raw` 항상 원문 (G-3). trend로 넘길 때 포함 1개·제외 없음이면 그 값, 그 밖은 `null` + "반영되지 않은 조건" |
| 시즌 | `trend_terms.season` 하나 | `trend_terms.seasons` 목록, 1개 이상 (SQ-2). trend로 넘길 때는 아래 "품목 수 제한"과 같이 **첫 시즌만** |
| 상품명 | `item_terms` | `product_model_names` (SQ-1, 이름만 바뀜) |
| `style_names`·`product_model_names`의 `value` | 문자열 또는 `null` | `null`만 (어휘 없음) |
| 모르는 필드 | 무시 (pydantic 기본) | 거부 (`additionalProperties: false`) |

**품목 수 제한 (IT-4, 2026-11-11 결정)**: `TrendQuery`는 품목 하나만 담고 지금 파이프라인도 품목 1개만 받는다. 그래서 trend에는 **`items`의 첫 품목(질의에 가장 먼저 나온 품목)만** 넘긴다. 나머지 품목은 `StructuredQuery`에 그대로 남고, 넘기지 않았다는 사실을 경고 `items_not_forwarded`로 알린다(변환은 2026-11-11 `query/to_trend.py` `to_trend_query()`로 구현, FW-1. 화면 표시는 U4). **재고·시장성·새 상품 기획 Agent도 이 계약을 받게 되면 같은 규칙(첫 품목·첫 시즌)을 따른다**(FW-1, Multi-Loop 전까지). 찾는 품목이 모두 범위 밖이어도 `category: null` 품목이 있으므로 trend를 부른다(IT-7′). 품목마다 반복하는 Multi-Loop 파이프라인이 생기면 모든 품목을 넘기도록 바꾼다. 받는 `TrendQuery` 한 개는 `items[0]`의 조건과 질의 전체의 `trend_terms`를 합쳐 만든다. `TrendQuery`의 시즌도 하나뿐이라 **`seasons[0]`만** 넘기고, 나머지 시즌은 경고 `seasons_not_forwarded`로 알린다(SQ-2). `items`가 `[]`이면 `TrendQuery`를 만들지 않는다(경고 `no_item_to_forward`).

**변환 규칙 (`to_trend_query()`, 2026-11-11 FW-1)**
| `StructuredQuery` (`items[0]`) | `TrendQuery` |
|---|---|
| `category` | `requested.categories.include` = [그 카테고리] (`null`이면 `[]` = 상분류 4개 전체) |
| `excluded_categories` | `requested.categories.exclude` |
| `gender`: 포함 1개(값 있음)·제외 없음 | `requested.gender` |
| `gender`: 그 밖(제외, 여러 개, 값 없음) | `unsupported`에 type `gender`, reason "trend 입력(TrendQuery)에 담을 칸이 없어 반영되지 않음" (+ 제외면 "(제외 요청)"), 경고 `conditions_not_forwarded` |
| `attributes.include`의 색·소재·패턴·디테일·핏 | `requested.attributes` (`value: null`도 원문과 함께) |
| `attributes.exclude` 전부, 기장 2종(포함·제외) | `unsupported`(위와 같은 reason), 경고 `conditions_not_forwarded`. 이때 `TrendQuery.unsupported.type`은 속성 종류 이름(`color`·`body_length` 등) 또는 `gender`다 — `StructuredQuery`의 9개 `unsupported.type`과 다른 값이다(`TrendQuery`의 type은 자유 문자열) |
| `unsupported` | `unsupported` (`polarity`가 없으므로 제외면 reason 끝에 "(제외 요청)") |
| `seasons[0]`, `style_names`, `product_model_names` | `trend_terms.season`, `style_names`, `item_terms` |
| `match`, `item_id` | 넘기지 않음 (`StructuredQuery`에 남음) |
| `out_of_scope_items` | 품목마다 `unsupported` `{type: "category", raw, reason}`로 넘김(IT-7′, 2026-11-12). 그 품목에 붙은 조건은 넘기지 않음 |

이 차이 목록은 `tests/test_query_structured_models.py`의 `INTENDED_DIFFERENCES`로 검사한다. 목록에 없는 차이가 생기면 테스트가 실패한다.

`to_trend_query()` 결과는 현재 순차 workflow의 Trend 입력으로 전달된다. TrendQuery에 표현할 수 없는 조건은 `unsupported`와 warning으로 남기고, `match`와 나머지 품목은 P08의 StructuredQuery 및 실행 trace에 보존한다. 최신 호출·오류·계약 동작은 [query-structuring.md](../../../../docs/query-structuring.md)와 [workflow contract](../../../workflow/workflow-contract.md)를 따른다.

## 8. 변경 이력
| 날짜 | 내용 |
|---|---|
| 2026-11-10 | 초안: 필드 정의, `match` 정의, 제외 조건 정의(X3), 예시 4개, `TrendQuery`와의 차이 |
| 2026-11-10 (2차) | Q3 = γ 확정 반영: 값이 정해지는 과정(§4.1)과 예 3의 단계별 표, 표준 목록과 별칭 사전(§4.2), 저장 범위(§4.4) |
| 2026-11-10 (3차) | G-1·G-2 확정(§4.3). U1 구현 반영: 모델·JSON Schema 생성, `CategoryRef`는 `none` 없음·`major` 항상 있음, 상분류 전체는 `exact`만, 성별 `raw` 필수, `TermRef.value`는 `null`만, 시즌 raw/origin 규칙, 모든 필드 출력·모르는 필드 거부, §7에 좁히기 행 추가. U2 확인 반영: 성별 표 항목, 원피스 처리, 가방·신발 사유 구분 |
| 2026-11-10 (4차) | 사용자 결정 반영(모델·스키마는 U3.5에서): **G-3** 성별 포함/제외 목록과 "고를 성별" 뜻("남성 말고" = 여·공용, "남녀" = 공용, 남+여 합치기), **OQ-3′** `unsupported.type` 9개 고정. §2·§3·§4·§4.3·§6 예·§7 갱신. 1.0은 아직 보낸 적이 없어 버전 유지 |
| 2026-11-10 (5차) | 사용자 결정 반영(구현은 U3.5): **OQ-9′** 기장 칸 `body_length`·`sleeve_length`, **OQ-5′** "원피스" = 드레스, **OQ-8′** "숏패딩" = 패딩 + 기장 크롭·`item_terms`는 표준으로 못 나타내는 상품명만, **OQ-10′** 띄어 쓴 카테고리 단어는 각각. §2·§3·§4·§4.2·§4.3·§7 갱신 |
| 2026-11-10 (6차) | U3.5 구현 반영: ⏳ 표시 제거(성별 포함/제외, `unsupported.type` 9개, 기장 칸, 원피스 = 드레스가 모델·JSON Schema에 들어감). 상태 줄에 v2 경로(`query/structured.py`)와 기준 측정 기록 |
| 2026-11-11 (7차) | 사용자 결정 IT-1~IT-4(품목별 묶음, 공통 조건 복사, 범위는 바로 뒤 품목, 모든 조건 품목별, trend에는 첫 품목만) 예고: 상태 줄, §2.1(예정 모양과 예), §7(품목 수 제한). 모델·스키마는 아직 바꾸지 않음 |
| 2026-11-11 (8차) | **품목별 묶음 구현 반영**(IT-1~IT-7): §2를 `items[]`·`out_of_scope_items` 모양으로 다시 씀(예정 절 §2.1 삭제), §3 필드 행, §4·§5의 정하지 못한 카테고리 처리, §6 예시를 새 모양으로(예 5 품목 두 개, 예 6 범위 밖 품목, 예 7 품목 없는 질의 추가), §7 묶는 단위·지원 불가 조건 행. 시즌·트렌드명은 질의 전체에 하나(IT-5″) |
| 2026-11-11 (9차) | **SQ-1** `item_terms` → `product_model_names`, **SQ-2** `season` → `seasons` 목록(1개 이상, trend에는 첫 시즌만). §2·§3·§6 예 3·§7 |
| 2026-11-11 (10차) | **CR-1** 조건을 합치는 규칙 §5.1 신설(품목끼리 독립, 품목 안 종류끼리 AND·같은 종류 OR, 카테고리·성별 계산, 제외 우선, 값 없는 조건·unsupported는 거르지 않음, 시즌·트렌드명 OR, 데이터 정보 없음). **IT-8** `item_id`(§2·§3·§6 예시) |
| 2026-11-11 (11차) | **IT-9** 첫 품목 앞 성별은 모든 품목에(§2 IT-2 예외) |
| 2026-11-11 (12차) | **1.0 확정**(SQ-F, 사용자 결정): 상태 줄, §0 인수인계(넘기는 것·꼭 알아야 할 것·1.0에 넣지 않은 것·생산 상태와 연결 상태) 신설. 필드·뜻은 바뀌지 않음 |
| 2026-11-11 (13차) | **FW-1**: §0.4 연결 상태(화면 서버가 이 계약을 만듦, 4개 Agent가 받는 입력 표), §7 첫 품목·첫 시즌 규칙을 모든 받는 Agent로, `to_trend_query()` 변환 규칙 표. 계약 필드는 바뀌지 않음 |
| 2026-11-11 (14차) | 콜드리드 검증 반영: 상태 줄(화면 서버 연결), §0.1.1 클래스 위치 표, §0.5 스키마를 바꾸는 절차(체크리스트), §0.6 옛 이름 → 새 이름 표, §0.2 경고 목록 보충, §2 "모델만 검사" 정정, §3 `QueryItem`에 `item_id`, §7 변환 표의 type 설명. 계약 필드는 바뀌지 않음 |
| 2026-11-11 (15차) | `main` 병합(팀원 HTTP v0.1 E2E): HTTP v0.1 실행에서 `trend_query`(첫 품목·첫 시즌)가 Trend로 실제 전송됨을 상태 줄·§0.4에 반영. 계약 필드는 바뀌지 않음 |
| 2026-11-12 (16차) | 사용자 결정 **IT-10**(품목 자리의 속성 값 → 그 속성을 가진 `category: null` 품목)·**IT-7′**(범위 밖 품목만 있어도 `category: null` 품목으로 시즌을 넘기고, `out_of_scope_items`는 trend `unsupported` type `category`). §0 5·8번, 연결 상태, §2 범위 밖 품목, §7 품목 수 제한·넘기지 않는 필드 표 갱신. 계약 모양은 그대로(1.0) |
