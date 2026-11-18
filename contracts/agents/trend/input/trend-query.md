# TrendQuery 계약 스냅샷 (읽기 전용 복사본)

> **이 문서와 `trend-query.contract.json`은 fashion-planner-trend가 소유한 계약의 복사본이다.** 원본을 읽지 않고도 **이것만 보고 질의 구조화를 구현할 수 있게** 만들었다. 직접 고치지 않는다. 계약을 바꾸려면 원본 소유자와 합의한 뒤 아래 "갱신 절차"로 다시 만든다.
>
> 이 스냅샷은 trend의 **현재** 계약을 그대로 기록한다. 이 레포가 원하는 변경(속성 제외 칸 등)은 이 파일이 아니라 별도 제안으로 다룬다 ([query-structuring.md](../../../../docs/query-structuring.md)).

| 항목 | 값 |
|---|---|
| 원본 | fashion-planner-trend `src/fashion_planner_trend/schemas/trend_query.py`, `vocabulary.py` |
| JSON 내용 해시 | `content_sha256` = `462f998c7bbc…` (전체 값은 JSON의 `_provenance`) |
| 생성 방법 | 코드에서 자동 생성 (손으로 쓰지 않음). 아래 "갱신 절차" |

## 1. 파일 구성
`trend-query.contract.json`의 최상위 키:

| 키 | 내용 |
|---|---|
| `json_schema` | `TrendQuery`의 JSON Schema (구조·타입·필수 여부) |
| `vocabulary` | 표준 어휘 값 목록: `season`, `gender`, `category_major`(4), `category_minor`(21), `attribute_values`(`color` 21, `material` 26, `pattern` 19, `detail` 61, `fit` 7) |
| `category_minor_to_major` | 하분류 → 상분류 매핑 |

**JSON Schema만으로는 검증이 끝나지 않는다.** 속성 `value`가 속성 종류별 허용 값에 들어가는지는 원본 코드의 검증기가 하고 JSON Schema에는 `string`으로만 나온다. 그래서 `vocabulary.attribute_values`를 따로 담았다. 검증은 §3 규칙까지 함께 해야 한다.

## 2. 출력 구조 (요약)
필드 의미의 전체 정의는 JSON Schema가 기준이다. 아래는 읽기 쉽게 줄인 것이다.

| 필드 | 내용 |
|---|---|
| `request_id` | 요청 ID. 이 저장소가 발급한 값을 그대로 쓴다 |
| `original_query` | 사용자 원문 |
| `trend_terms.season` | `{year, season(SS/FW), raw, origin}`. `origin`은 `user_requested` 또는 `default_next` |
| `trend_terms.style_names[]`, `item_terms[]` | `{raw, value}`. 트렌드명·상품 어휘는 아직 없어 `value`는 `null` |
| `requested.categories` | `include[]`, `exclude[]` (각 `{raw, major, minor}`) |
| `requested.gender` | `{raw, value}` 또는 `null`. `value`는 남·여·공용 |
| `requested.attributes[]` | `{type(color/material/pattern/detail/fit), raw, value}` |
| `unsupported[]` | `{type, raw, reason}` |
| `missing_fields[]` | 검색에 쓰이는 필드 중 질의에 없는 것 |

## 3. JSON Schema에 없는 규칙 (원본 코드의 검증과 동작)
1. **속성 값**: `requested.attributes[].value`는 `null`이거나 `vocabulary.attribute_values[type]` 안의 값이어야 한다. 아니면 검증 오류다. 표준에 없으면 `null`로 둔다 (`null`이면 trend 쪽이 필터를 걸지 않는다). 예: 핑크색 → `핑크`(O), `핑크색`(X, 오류).
2. **`detail` 값 집합**은 디테일 40 + 넥라인 12 + 칼라 10에서 겹치는 `후드` 1개를 뺀 **61개**다.
3. **카테고리**: `minor`가 있으면 `major`는 `category_minor_to_major`로 **덮어써진다**. "상의"처럼 상분류만 말했으면 `minor = null`. `include`가 비면 상분류 4개 전체이고, `exclude`는 제외다. 상의와 아우터는 **별개**다 (상의 요청에 아우터가 따라오지 않는다).
4. **표준에 없는 카테고리**(신발·가방 등)와 **지원하지 않는 조건**(브랜드·가격·시장·채널)은 조용히 버리지 않고 `unsupported`에 기록한다. 속성 종류에 `length`·`size`·세이프는 없다 (요청 속성으로 쓰지 않는다).
5. **시즌**: 값은 `SS`(봄·여름) / `FW`(가을·겨울)다. 질의에 시즌이 없으면 **코드가** 오늘 날짜로 다음 패션 시즌을 계산해 채우고 `origin = default_next`, `raw = null`로 둔다. LLM이 날짜를 추측하지 않는다.

   | 오늘 | 다음 시즌 | 예 |
   |---|---|---|
   | 3~8월 | 같은 해 `FW` | 2026-05 → 2026 FW |
   | 9~12월 | 다음 해 `SS` | 2026-11 → 2027 SS |
   | 1~2월 | 같은 해 `SS` | 2027-01 → 2027 SS |

   질의에 시즌이 있으면 그 값을 쓰고 `origin = user_requested`, `raw`에 원문 표현을 둔다.
6. **없는 값을 보충하지 않는다.** 카테고리·속성이 없으면 비워 두면 필터가 걸리지 않는다. `missing_fields`에는 질의에 없는 필드를 적는다 (시즌은 기본값으로 채우므로 제외).
7. **trend 쪽에서 일어나는 일** (구현이 알고 있어야 하는 것): 성별 필터는 아직 적용되지 않는다. `value`가 `null`인 요청 속성은 필터에 쓰이지 않고 응답의 한계로 알려진다. 요청한 카테고리가 모두 지원 밖이면 `no_results`와 한계가 돌아온다.

## 4. 예시 (합성, 원본 모델로 검증됨)
```jsonc
{
  "request_id": "req_example_001",
  "original_query": "2027년 봄 발레코어에서 유행하는 핑크색 상의를 기획하고 싶어",
  "trend_terms": {
    "season": {"year": 2027, "season": "SS", "raw": "2027년 봄", "origin": "user_requested"},
    "style_names": [{"raw": "발레코어", "value": null}],
    "item_terms": []
  },
  "requested": {
    "categories": {"include": [{"raw": "상의", "major": "상의", "minor": null}], "exclude": []},
    "gender": null,
    "attributes": [{"type": "color", "raw": "핑크색", "value": "핑크"}]
  },
  "unsupported": [],
  "missing_fields": []
}
```
- "하의 빼고": `include = []`, `exclude = [{"raw": "하의", "major": "하의", "minor": null}]` → 상의·아우터·원피스.
- 블라우스 + 브랜드 H&M: `include = [{"raw": "블라우스", "major": "상의", "minor": "블라우스"}]`, `unsupported = [{"type": "brand", "raw": "H&M", "reason": "Product 데이터는 Musinsa뿐"}]`.

## 5. 이 스냅샷이 다루지 않는 것
- **별칭 사전**(무신사·LLM 표현 → 표준 값 변환표)은 포함하지 않았다. 원본 저장소의 파일이다. 질의 구조화가 표준 값을 고르는 방법은 현재 질의 구조화는 [query-structuring.md](../../../../docs/query-structuring.md)에 설명한 방식으로 동작한다: 별칭 사전도 별도 스냅샷으로 가져와([alias-map.md](../vocabulary/alias-map.md)) 코드가 값을 정하고, 사전에 없을 때만 LLM 제안을 출처 표시와 함께 쓴다 ([query-structuring.md](../../../../docs/query-structuring.md)).
- 검색·점수 계산, `TrendAnalysisResult`의 정의 (출력 스냅샷은 [`trend-analysis-result.contract.json`](../output/trend-analysis-result.contract.json)).

## 6. 갱신 절차 (원본이 바뀔 때)
1. 원본 소유자가 `TrendQuery` 변경을 알린다 (알림이 없으면 이 스냅샷은 낡을 수 있다).
2. 원본 저장소 루트에서 계약을 다시 내보낸다. 내보내는 코드는 `TrendQuery.model_json_schema()`, `Season`·`Gender`·`CategoryMajor`·`CategoryMinor` Enum 값, 속성별 허용 값(`_ATTRIBUTE_VALUES`), `MINOR_TO_MAJOR`를 JSON 하나로 묶고 `content_sha256`를 기록한다(커밋 SHA는 적지 않는다).
3. 이 폴더의 JSON을 교체하고 이 문서의 해시·§3의 개수를 고친다. 값 개수는 JSON에서 읽어 확인한다.
4. `python scripts/generate_query_vocabulary.py`로 `query/vocabulary.py`를 다시 만들고 테스트를 돌린다.
