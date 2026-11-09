# data/ — 예상 사용자 질의

## 파일
| 파일 | 내용 |
|---|---|
| [`user_queries.v1.json`](user_queries.v1.json) | 예상 사용자 질의 30개(합성)와 각 질의의 기대 draft. 프롬프트 `query-structuring-prompt/v1` 기준 |

모든 질의는 Claude가 임의로 만든 합성 문장이다. 실제 사용자 로그나 기사 문장은 없다. 프롬프트의 few-shot 예시와 겹치지 않는다 (겹치면 정확도가 부풀려진다. `tests/test_query_data.py`가 검사).

## 형식
```jsonc
{
  "dataset": "user_queries", "version": "v1", "created": "2026-11-09",
  "prompt_version": "query-structuring-prompt/v1",   // 기대 draft가 따르는 프롬프트 규칙의 버전
  "reference_today": "2026-11-09",                    // draft → TrendQuery 조립 때 쓰는 기준 날짜
  "items": [
    {
      "id": "q001",
      "query": "사용자 원문",
      "tags": ["season_with_year", "..."],            // 아래 태그 표
      "label_status": "silver",                       // silver: Claude 작성 / gold: 사용자 확인 완료
      "expected_draft": { /* QueryDraft 형식: LLM이 내야 할 출력 */ },
      "notes": "판단 근거나 확인이 필요한 점 (있을 때만)"
    }
  ]
}
```
기대값을 `TrendQuery`가 아니라 **draft**로 둔 이유는 이 데이터가 LLM 단계를 평가하기 위한 것이기 때문이다. `TrendQuery`는 draft를 `assemble_trend_query(..., today=reference_today)`에 넣으면 결정적으로 나온다.

## v1 라벨과 별칭 사전 정책의 관계 (중요)
v1의 기대 draft는 별칭 사전 정합 정책(H1~H6, [docs/query-decisions.md](../docs/query-decisions.md))이 정해지기 **전에** 만들었다. 정책을 구현할 때 다음 라벨이 바뀌므로 **`user_queries.v2.json`을 새로 만든다** (v1은 보존):
- q027 `슬랙스`: 지원 불가 → **팬츠** (사전에 정확한 별칭이 있음)
- q011 `오버핏`, q029 `루즈핏`: 오버사이즈·루즈 → **`null`** (사전 공백, 한계 L-1)
- 형식: 결정 V1(코드 후보 → LLM 검수)에 맞춰 기대 `TrendQuery` 값으로 바꾼다 ([구현 계획](../docs/query-candidate-review-plan.md) P-5)

## 라벨 상태와 확인 절차
- 30개 모두 **silver**다. 사용자가 확인한 항목만 `label_status`를 `gold`로 바꾼다.
- 특히 확인이 필요한 항목 (`ambiguous` 태그):

| id | 질의 | 확인할 점 |
|---|---|---|
| q002 | 올여름 린넨 셔츠 트렌드 알려줘 | 상대 표현 '올여름'. 지금은 year null → 코드가 2027 SS로 정함 (사용자 뜻은 2026 여름일 수 있음, 가정 A-1) |
| q011 | 유니섹스 오버핏 후드티 그레이로 | **정책으로 결정됨 (H1)**: '오버핏'은 사전에 없으므로 v2에서 `null`. 사전에 표기 변형으로 넣을지는 사전 추가 후보로 사용자 확인 |
| q021 | 숏패딩 트렌드 정리해줘 | `item_terms`만 둘지, 카테고리 '패딩'도 넣을지 (계약에 정의 없음) |
| q027 | 청바지 말고 슬랙스 위주로 | **정책으로 결정됨 (H1)**: 사전에 `슬랙스 → 팬츠` 별칭이 있으므로 v2에서 팬츠 |

`notes`가 있는 나머지 항목(q001, q004, q006, q012, q015, q020, q022, q026, q028, q029)은 판단 근거나 계약 수준의 한계를 적은 것이다.

## 태그 (30개 기준 개수)
| 태그 | 개수 | 뜻 |
|---|---|---|
| `include` / `exclude` | 25 / 4 | 포함 / 제외 카테고리 |
| `minor` / `major_only` / `multi_category` | 24 / 4 / 3 | 하분류 / 상분류만 / 카테고리 2개 이상 |
| `color` / `material` / `pattern` / `detail` / `fit` | 8 / 7 / 5 / 2 / 3 | 속성 종류 |
| `gender` | 6 | 성별 표현 |
| `season_with_year` / `season_without_year` / `relative_season` | 4 / 3 / 1 | 시즌 표현 (시즌이 없는 질의는 태그 없음) |
| `mixed_notation` | 3 | `2026 FW`, `2027 S/S` 같은 영문 혼합 표기 (QP-9, 검증 안 된 형식) |
| `style_name` / `item_term` | 2 / 1 | 트렌드명 / 특정 상품 |
| `unsupported_brand` / `_price` / `_channel` / `_market` / `_age` | 1 / 2 / 1 / 1 / 1 | 지원 불가 조건 |
| `unsupported_category` / `all_categories_unsupported` | 2 / 1 | 표준 밖 카테고리 / 요청 카테고리 전부가 표준 밖 |
| `unsupported_attribute` | 2 | 길이·세이프 (요청 속성으로 쓰지 않음) |
| `abstract` | 4 | 추상 표현 ("깔끔한", "데이트룩" 등) |
| `no_conditions` | 1 | 추출할 조건이 없음 |
| `prompt_injection` | 1 | 질의 안의 지시문 |
| `ambiguous` | 4 | 정답이 확정되지 않아 사용자 확인 필요 |

## 쓰는 법 (LLM 어댑터가 생긴 뒤, QP-8)
1. 각 `query`를 `structure_query(...)`에 넣고 `result.draft`를 얻는다.
2. `expected_draft`와 비교한다. 권장 비교 기준:
   - **값 필드는 정확히 비교**: `season.season`, `season.year`, 카테고리 `major`/`minor`, 성별 `value`, 속성 `type`/`value`, `unsupported.type`.
   - **`raw`는 느슨하게 비교**: 경계 차이("라벤더" vs "라벤더 컬러")는 오류로 보지 않는다. 질의의 부분 문자열인지만 본다 (`raw_not_in_query` 경고).
   - 순서는 무시한다.
3. 결과에 `prompt_version`과 경고 빈도를 함께 기록한다.

## 새 버전 만들기
프롬프트 규칙이 바뀌어 기대 draft가 달라지면 `user_queries.v2.json`을 새로 만들고 `prompt_version`을 맞춘다. v1은 지우지 않는다.
