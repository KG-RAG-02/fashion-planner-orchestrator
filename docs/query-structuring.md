# 질의 구조화

MD가 쓴 기획 요청 문장을 검증된 `StructuredQuery`로 바꾸고, 그 가운데 첫 품목과 첫 시즌을 trend 서비스가 받는
`TrendQuery`로 변환한다. 코드는 `src/fashion_planner_orchestrator/query/`에 있고 pydantic이 필요하다
(`pip install -e '.[query]'`).

```text
user_input.text
  → LLM 초안(QueryDraft: 조건마다 어느 품목의 것인지 `item`으로 표시한 평면 목록)
  → 표기 보정·검증(query/draft.py)
  → 값 확정(query/resolve.py: 표준 어휘 → 별칭 사전 → LLM 제안 순)
  → 품목별 묶기와 조립(query/draft.py, query/assembly.py)
  → StructuredQuery(schema_version 1.0.0)
  → TrendQuery(query/to_trend.py: 첫 품목·첫 시즌)
  → 1단계 trend 호출
```

## 1. 역할 나누기

- **LLM은 원문을 쪼개고 표준 값을 제안만 한다.** 시즌, 스타일 이름, 품목, 조건(색·소재·핏 등)과 포함/제외를 원문
  그대로(`raw`) 뽑고, 각 조건에 표준 값 제안(`suggestion`)과 속한 품목(`item`)을 붙인다. 날짜 계산, 표준 값 확정,
  `match` 판정은 하지 않는다.
- **코드가 값을 정한다.** 표준 어휘(trend의 TrendQuery 계약에서 생성한 `query/vocabulary.py`)와 trend의 별칭 사전
  (`query/alias_data.py`, [alias-map.md](../contracts/agents/trend/vocabulary/alias-map.md))을 먼저 보고, 사전에 없을 때만
  LLM 제안을 표준 목록 안에서 받아들인다. 연도가 없는 시즌은 기준일로 다음 시즌을 계산한다.
- 질의 구조화는 네트워크·데이터 소스·다른 서비스를 직접 부르지 않는다(LLM 호출만 `llm_provider.py`를 통한다).

## 2. `match`: 값이 어디서 왔나

| `match` | 뜻 | 예 |
|---|---|---|
| `exact` | 원문이 표준 값과 같다 | "블랙" → 블랙 |
| `alias` | 사전에 등록된 같은 뜻 표기다 | "슬랙스" → 팬츠, "원피스" → 드레스 |
| `approx` | 사전에 등록된 가까운 뜻이다(같은 뜻은 아님) | "버터옐로우" → 옐로우 |
| `llm` | 사전에 없어 LLM이 표준 목록에서 고른 값이다 | "오버핏" → 오버사이즈 |
| `none` | 표준 값으로 정하지 못했다(`value: null`) | "연두"(같은 뜻의 표준 색이 없음) |

받는 쪽은 `match`를 보고 그 값을 필터로 쓸지, 참고로만 쓸지 정한다. 성별은 사전에 없어
`configs/query_structuring.json`의 고정 표로 정하고 `alias`로 표시한다. 포함에 남·여가 함께 나오면 공용 하나로 합친다.

## 3. 품목별 묶기

조건은 품목마다 묶는다. "남성 말고 니트 베스트랑 와이드 조거팬츠"는 품목 두 개가 되고, "니트"는 베스트에만,
"와이드"는 조거팬츠에만 붙는다. 질의 전체에 걸리는 조건("남성 말고")은 모든 품목에 복사한다.

- 품목을 말하지 않았으면 `category: null`인 품목 하나에 조건을 모은다.
- 표준 카테고리가 아닌 품목("블랙 부츠"의 부츠)은 `out_of_scope_items`에 이유와 함께 남기고 검색 대상에서 뺀다.
- 브랜드, 표준 카테고리 밖의 것, 요청 속성으로 쓰지 않는 속성, 추상 표현은 `unsupported`에 이유와 함께 남긴다
  (이유 문장은 `configs/query_structuring.json`).
- 시즌과 스타일 이름은 질의 전체에 하나다(`trend_terms`).

필드 정의와 예시는 계약 문서 [structured-query.md](../contracts/agents/orchestrator/output/structured-query.md)와
JSON Schema [structured-query.schema.json](../contracts/agents/orchestrator/output/structured-query.schema.json)에 있다.
스키마는 pydantic 모델에서 생성한다(`python scripts/export_structured_query_schema.py`, `--check`로 일치 확인).

## 4. trend로 넘기기

trend는 지금 품목 하나와 시즌 하나를 받는다. 그래서 첫 품목(`items[0]`)과 첫 시즌만 TrendQuery로 바꾸고, 나머지는
StructuredQuery에 남긴 채 경고로 알린다.

| 경고 | 뜻 |
|---|---|
| `items_not_forwarded` | 두 번째 이후 품목은 보내지 않았다 |
| `seasons_not_forwarded` | 두 번째 이후 시즌은 보내지 않았다 |
| `conditions_not_forwarded` | 범위 밖 품목은 TrendQuery의 `unsupported`(type `category`)로만 알렸다 |
| `no_item_to_forward` | 보낼 품목이 없어 trend를 부르지 않는다 |

## 5. 실행 모드와 오류

| 모드 | 설정 | 동작 |
|---|---|---|
| LLM | `LLM_MODEL`, `LLM_API_KEY`(`llm_provider.py`, `configs/llm.json`) | 모델 이름이 `claude`로 시작하면 Anthropic, 아니면 OpenAI SDK(호출할 때만 import, `pip install -e '.[llm-openai]'` 등) |
| 오프라인 | `ORCHESTRATOR_QUERY_STRUCTURING_MODE=synthetic_fixture` | `examples/fixtures/query/`의 합성 초안을 질의 문장이 정확히 같을 때만 돌려준다. stub 프로필이 쓴다 |

LLM 호출은 temperature 0(추출 작업이라 실행마다 결과가 달라지지 않게)이고, 타임아웃·출력 한도·재시도는
`configs/llm.json`에 있다. 실패하면 `INPUT_INVALID`, `PROVIDER_FAILED`, `DRAFT_NOT_JSON`, `DRAFT_INVALID`,
`STRUCTURED_QUERY_INVALID` 가운데 하나로 멈추고, 질의 원문·모델 출력·예외 문장은 응답에 넣지 않는다.

## 6. 프롬프트

프롬프트는 `prompts/query_structuring/`에 있다([prompts/README.md](../prompts/README.md)). 버전 폴더는 없고, 결과는
프롬프트를 `prompt_id`(`query-structuring`)와 템플릿 세 파일의 sha256(`prompt_sha256`)으로 식별한다. 프롬프트를 고치면
`python scripts/render_query_prompt.py`로 실제 보내는 문장을 다시 만들고 `tests/test_query_prompt.py`로 예시 규칙을 확인한다.

## 7. 평가

`data/query-structuring-eval/`의 합성 질의(개발용 30개, 홀드아웃 20개)로 잰다. 라벨은 AI가 썼고 사람이 검증하지
않았다([data.md](data.md)).

```bash
PYTHONPATH=src python scripts/evaluate_query_structuring.py --provider fixture   # 코드 부분만, 외부 호출 없음
PYTHONPATH=src python scripts/evaluate_query_structuring.py --provider llm       # 설정한 LLM 호출, 비용 발생
```

`--provider fixture`는 라벨을 모델 출력처럼 넣어 값 확정·묶기·조립이 라벨을 재현하는지 본다. 모델 품질의 측정이
아니다. `tests/test_query_structured_path.py`가 같은 검사를 테스트로 고정한다.

## 8. 코드 지도

| 위치 | 역할 |
|---|---|
| `query/draft.py` | LLM 초안 모델(QueryDraft), 표기 보정, 품목별 묶기 |
| `query/resolve.py`, `query/lexicon.py` | 값 확정, 별칭 사전 변환(trend `AliasMap`의 표준 라이브러리 이식) |
| `query/assembly.py` | 묶은 초안 → StructuredQuery 조립 |
| `query/models.py` | StructuredQuery pydantic 모델 |
| `query/to_trend.py`, `query/trend_query.py` | TrendQuery 변환과 모델 |
| `query/structured.py` | 위 단계를 잇는 진입점 `structure_query` |
| `query/prompt.py`, `query/config.py` | 프롬프트 렌더, 설정 읽기 |
| `query/vocabulary.py`, `query/alias_data.py` | trend 계약·사전 사본에서 생성한 상수(직접 고치지 않는다) |
| `query/contract_vocabulary.py` | trend 계약에 없는 기장 두 종류(`body_length`, `sleeve_length`)의 값 |
