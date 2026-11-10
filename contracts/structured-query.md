# StructuredQuery 1.0 — 구조화된 질의 계약

> 상태: **1.0 모델 구현됨 (U1, 2026-11-10)**: `src/vogue_orchestrator/query/models.py`, JSON Schema [structured-query.schema.json](structured-query.schema.json) (모델에서 생성, 직접 수정 금지). 질의 → 이 계약으로 조립하는 흐름은 아직 연결 전 (구현 계획 [structured-query-plan.md](../docs/structured-query-plan.md) U4). 소유: **이 저장소(vogue-orchestrator)** — 결정 Y1. 근거와 결정 기록은 [query-decisions.md](../docs/query-decisions.md) (X1·X2·X3·X4·Y1·R0·Q3).
> 이 문서는 **받는 쪽**(trend-analysis-agent, 이후 재고 등 다른 Agent)이 각 필드의 뜻과 쓰는 법을 판단할 수 있게 쓴 정의서다. 받는 쪽이 어떤 필드를 어떻게 쓸지는 **받는 쪽이 정한다.** 이 문서의 "활용 예"는 권장이지 강제가 아니다.

## 1. 무엇인가
사용자의 자연어 질의 1개를 파이프라인 **맨 앞**에서 구조화한 결과다. 모든 값은 trend-analysis-agent가 소유한 **K-Fashion 표준 어휘**(별칭 사전으로 연결)로 표현한다(D-29).

```
사용자 질의 ──► [이 저장소] StructuredQuery ──► trend 분석 (지금은 TrendQuery로 변환해서, Action Item)
                                            └─► 다른 Agent (예정, 각자 변환)
```
trend의 기존 계약 `TrendQuery`(스냅샷: [trend-query.md](trend-query.md))를 이어받아 이름을 바꾸고, 속성 포함/제외와 출처 표시(`match`)를 더한 것이다. 차이는 §7.

## 2. 전체 형태
```jsonc
{
  "schema_version": "1.0",
  "request_id": "req-001",                 // 오케스트레이터가 발급한 요청 ID
  "original_query": "사용자 원문",
  "trend_terms": {
    "season": {"year": 2027, "season": "SS", "raw": null, "origin": "default_next"},
    "style_names": [{"raw": "발레코어", "value": null}],
    "item_terms": []
  },
  "requested": {
    "categories": {"include": [CategoryRef...], "exclude": [CategoryRef...]},
    "gender": null,                         // 또는 {"raw": "여성", "value": "여", "match": "alias"} — 성별 규칙은 §4.3 G-1
    "attributes": {
      "include": {"color": [AttributeRef...], "material": [], "pattern": [], "detail": [], "fit": []},
      "exclude": {"color": [], "material": [], "pattern": [], "detail": [], "fit": []}
    }
  },
  "unsupported": [{"type": "brand", "raw": "자라", "reason": "...", "polarity": "exclude"}],
  "missing_fields": []
}
```
- `CategoryRef` = `{"raw", "major", "minor", "match"}`
- `AttributeRef` = `{"raw", "value", "match"}`
- **빈 값은 `null`이 아니라 `[]`** 다. 속성 키 5개(`color`·`material`·`pattern`·`detail`·`fit`)는 포함·제외 양쪽에 **항상 있다.**
- **모든 필드는 항상 출력된다** (JSON Schema에서 모두 `required`). **모르는 필드는 허용하지 않는다** (`additionalProperties: false`). 스키마에서는 속성 종류마다 허용 값 목록(`enum`)이 걸린 `ColorRef`·`MaterialRef`·`PatternRef`·`DetailRef`·`FitRef`로 나오지만 모양은 모두 `AttributeRef`와 같다.
- **JSON Schema가 표현하지 못하는 규칙**(생산자인 이 저장소의 모델만 검사한다): `match = none` ⇔ `value = null`, `minor`가 있으면 `major`는 그 하분류의 상분류, `minor = null`인 카테고리는 `match = exact`, 시즌의 `origin`과 `raw` 관계. 받는 쪽이 스키마로만 검증하면 이 규칙들은 검사되지 않으므로, 필요하면 받는 쪽이 따로 확인한다.

## 3. 필드 정의
| 필드 | 뜻 | 값 |
|---|---|---|
| `schema_version` | 이 계약의 버전 | `"1.0"`. 호환 변경(필드 추가)은 1.x, 깨지는 변경은 2.0. 단, 이 스키마는 모르는 필드를 허용하지 않으므로 받는 쪽이 스키마로 검증한다면 1.x가 올라갈 때 새 스키마로 바꿔야 한다 (확인 대기 OQ-2) |
| `request_id`, `original_query` | 요청 ID, 사용자 원문 그대로 | 문자열 |
| `trend_terms.season` | 기획 시즌 | `{year, season(SS/FW), raw, origin}`. `origin = user_requested`(사용자가 말함) 또는 `default_next`(말하지 않아 질의한 날짜 기준 다음 시즌으로 코드가 채움: 3~8월 → 같은 해 FW, 9~12월 → 다음 해 SS, 1~2월 → 같은 해 SS). `default_next`이면 `raw`는 `null`, `user_requested`이면 사용자가 쓴 시즌 표현 |
| `trend_terms.style_names[]`, `item_terms[]` | 트렌드명(발레코어 등), 특정 상품명 | `{raw, value}`. 정규화 어휘가 아직 없어 `value`는 `null` (스키마에서도 `null`만 허용. 어휘가 생기면 1.x에서 넓힘) |
| `requested.categories.include[]` | **기획하고 싶은** 카테고리. 비어 있으면 상분류 4개 전체 | `CategoryRef` |
| `requested.categories.exclude[]` | **기획에서 빼고 싶은** 카테고리 (§5) | `CategoryRef` |
| `CategoryRef.major` / `.minor` | 상분류(상의·하의·아우터·원피스) / 하분류 21개. `minor`가 있으면 `major`는 그 하분류의 상분류(코드가 정함). **`minor`가 있으면 그 하분류만, `minor`가 `null`이면 상분류 전체를 가리킨다.** `minor`가 `null`인 참조는 사용자가 상분류 이름을 직접 말한 경우뿐이라 `match`는 항상 `exact`다 | `major`: 표준 어휘 (**항상 있음**). `minor`: 표준 어휘 또는 `null` |
| `requested.gender` | 성별 | `{raw, value(남·여·공용 또는 null), match}` 또는 `null`. `raw`는 항상 원문. `value`가 `null`이면 `match`는 `none` |
| `requested.attributes.include.<종류>[]` | **원하는** 속성 값 | `AttributeRef`. 종류별 허용 값은 표준 어휘(색 21, 소재 26, 패턴 19, 디테일 61(디테일·넥라인·칼라 포함), 핏 7) |
| `requested.attributes.exclude.<종류>[]` | **빼고 싶은** 속성 값 (§5) | `AttributeRef` |
| `AttributeRef.value` | 표준 값. 표준 값으로 정하지 못했으면 `null` (원문은 `raw`에 남음) | 표준 어휘 또는 `null` |
| `match` | 값이 **어떻게 정해졌는지** (§4) | `exact`·`alias`·`approx`·`llm`·`none` (`CategoryRef`에는 `none`이 없다 — 정하지 못한 카테고리는 `unsupported`) |
| `unsupported[]` | 요청했지만 표준 어휘·이 계약으로 표현할 수 없는 조건 (브랜드·가격·시장·채널·연령·표준 밖 카테고리·길이·세이프·추상 표현 등). 조용히 버리지 않고 기록 | `{type, raw, reason, polarity}`. `polarity = include`(원함) 또는 `exclude`(빼고 싶음, 예: "자라 말고"). `polarity`는 항상 있다 (기본값 없음) |
| `missing_fields[]` | 예약 필드. 지금은 항상 `[]` (정의 미정, 가정 A-2) | — |

## 4. `match` — 값의 출처 (받는 쪽 판단용)
같은 표준 값이라도 **얼마나 확실한지**가 다르다. 받는 쪽은 이 값을 보고 필터로 쓸지, 순위에만 반영할지, 표시만 할지 정한다.

| `match` | 뜻 | 어떻게 정해지나 | 예 | 활용 예 (권장) |
|---|---|---|---|---|
| `exact` | 원문이 표준 값과 같음 | 사전(표준 값) | "블랙" → 블랙 | 그대로 조건으로 사용 |
| `alias` | 사전에 등록된 **같은 뜻** 표기 | 사전(별칭) | "슬랙스" → 팬츠 | 그대로 조건으로 사용 |
| `approx` | 사전에 등록된 **가까운 뜻** (같은 뜻은 아님) | 사전(근사) | "버터옐로우" → 옐로우, "슬림" → 타이트 | trend는 근사 값을 검색 필터로 쓰지 않는 원칙이 있음 → 표시·참고 |
| `llm` | 사전에 없어 **LLM이 표준 목록에서 고른** 값 (표준 목록 밖 값은 나오지 않음) | LLM 제안 → 코드가 표준 목록 확인 | "오버핏" → 오버사이즈 | 확실성이 낮음 → 표시·참고, 또는 받는 쪽 판단. 이 표현은 사전 추가 후보로 수집됨 |
| `none` | 표준 값으로 정하지 못함 (`value = null`). ① 사전이 "값 없음"으로 명시했거나(LLM 제안은 쓰지 않음, 예: detail "카라"), ② 사전에 없고 LLM도 표준 목록 안에서 같은 뜻의 값을 제안하지 못한 경우 | — | "연두" (색 21개에 같은 뜻 값이 없음) | 조건으로 쓸 수 없음. 응답의 한계로 알리기. 카테고리에는 쓰지 않는다 (아래 보충 규칙: `unsupported`로 감) |

**종류별 보충 규칙**
| 경우 | 규칙 |
|---|---|
| 상분류 이름 ("상의", "하의", "아우터", "원피스") | 표준 목록과 직접 비교해 같으면 `exact` (사전은 이 단어를 힌트로만 돌려주므로 사전 결과를 쓰지 않는다) |
| 카테고리를 표준 값으로 정하지 못함 | `CategoryRef`(`major`·`minor` 모두 `null`)로 두지 않고 **`unsupported`(type `category`)** 에 기록한다. 사전이 범위 밖으로 명시한 품목(예: 가방·모자)과 사전에 없는 품목은 `reason` 문장으로 구분한다 |
| 한 표현에 여러 값 ("블랙 화이트") | LLM이 값마다 따로 낸다. 그래도 한 표현에서 값이 여러 개 나오면 값마다 별도 `AttributeRef`를 만들고 `raw`는 같은 원문을 쓴다 |
| 성별 | 고정 표로 정하고 `alias` (G-1, §4.3) |
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
| 크기 | 상분류 4, 하분류 21, 색 21, 소재 26, 패턴 19, 디테일 61, 핏 7, 시즌 2, 성별 3 | 약 500개 항목 |
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

- 이 표는 trend 사전에 **없는 속성**에 대한 것이라 "질의 전용 별칭 사전을 만들지 않는다"(사전 중복 금지, [query-decisions.md](../docs/query-decisions.md) §5)와 충돌하지 않는다. trend 사전에 성별이 생기면 그쪽을 따르고 이 표는 지운다.
- 표 항목 (2026-11-10 U2 확정): 여 = 여성·여자·여성용·우먼·위민즈·women·womens, 남 = 남성·남자·남성용·맨즈·men·mens, 공용 = 남녀공용·유니섹스·unisex. "젠더리스"는 같은 뜻이 아니라 넣지 않았다. 비교는 사전과 같은 방식(소문자, 공백·`-`·`_` 무시)이라 "남녀 공용"도 맞는다. 위치: `configs/query_structuring.json`의 `gender.aliases`.
- **성별 제외("남성 말고")를 담을 칸은 아직 없다** (`gender`는 값 하나, 포함만). 처리 방법은 확인 대기 OQ-4 ([query-decisions.md](../docs/query-decisions.md) §6).

**G-2 카테고리 하위 종류 — 하분류 목록에 없을 때만 LLM이 상위 하분류를 제안**

규칙:
1. 사용자가 말한 표현이 **표준 하분류이거나 사전으로 하분류가 정해지면 그 하분류를 그대로 쓴다.** 더 넓은 상분류로 바꾸지 않는다. 예: "스커트"는 스커트(`exact`)이고, "하의"로 바꾸지 않는다.
2. 표현이 하분류 21개 중 어디에도 직접 해당하지 않는 **하위 종류**일 때만(예: "블레이저", "미니스커트"), LLM이 그것을 포함하는 **하분류**를 제안할 수 있다 → `match: llm`.
3. LLM 제안은 **하분류로만** 받는다. 하위 종류를 상분류(상의·하의·아우터·원피스)로 올리는 제안은 받지 않는다 → 맞는 하분류가 없으면 `unsupported`(type `category`). 상분류는 사용자가 상분류 이름을 직접 말했을 때만 쓴다(`exact`).
4. 가방·신발·모자처럼 **범위 밖 품목**은 어느 경우든 `unsupported`.

| 원문 | 사전 결과 (실제 확인) | 최종 | `match` |
|---|---|---|---|
| 스커트 | 표준 값 | 하분류 스커트 (하의로 바꾸지 않음) | `exact` |
| 데님 스커트 | 전체는 없음 → 표현 안 단어 "스커트" | 하분류 스커트 | `exact` |
| 미니스커트 | 없음 | LLM 제안 스커트 → 하분류 스커트 | `llm` |
| 블레이저 | 없음 | LLM 제안 재킷 → 하분류 재킷 | `llm` |
| 하의 | 힌트만 | 상분류 하의 (사용자가 직접 말함) | `exact` |
| 가방 | 값 없음(범위 밖) | — | `unsupported` |

- 근거: trend 표준 문서도 "블레이저·트렌치코트는 재킷·코트로만 남는다"고 적어 데이터 쪽이 같은 방식으로 다룬다. `llm` 표시로 받는 쪽이 신뢰도를 판단한다.
- LLM이 하위 종류에 상분류를 제안하면(예: "롱스커트" → 하의) 받지 않고 `unsupported`(type `category`)로 보낸다. 그래서 프롬프트 v2는 "가장 구체적인 하분류를 제안하라"고 지시한다.
- "원피스"는 사전에서 하분류 드레스의 별칭이지만, 상분류 이름을 사전보다 먼저 보므로 **상분류 원피스**(`exact`, 드레스·점프수트 모두)가 된다. "니트 원피스"도 표현 안 단어로 상분류 원피스. 이 처리가 맞는지는 확인 대기 OQ-5.
- `llm`으로 정해진 하위 종류 표현은 사전 추가 후보다. 단, "미니스커트 → 스커트"처럼 같은 뜻이 아니라 **하위 종류**인 경우 D-29(같은 뜻 표기만 추가)에 따라 `aliases`에 넣을 수 있는지는 따로 판단한다.

### 4.4 무엇이 저장되나
`match`는 결과 JSON 안의 필드로 받는 쪽에 **전달**된다. 이 저장소는 결과를 DB·로그에 **보관하지 않는다.** `match = llm` 표현을 모아 사전 추가 후보로 쓰는 기록 저장은 아직 정하지 않은 Action Item이다.

## 5. 제외 조건 (결정 X3)
**정의**: 사용자가 "기획 대상에서 빼고 싶다"고 명시한 카테고리·속성 값.
1. **추적**: 사용자가 뺀 것은 표준 값으로 정해졌든 아니든 **모두 기록**한다 (원문 `raw` + `match`). 표준 어휘로 표현할 수 없는 제외(브랜드 등)는 `unsupported`에 `polarity: "exclude"`로 남긴다.
2. **전달**: 표준 값으로 정해진 것은 그 표준 값으로 전달해, 받는 쪽이 **같은 표준 값을 가진 아이템·상품을 결과에서 뺄 수 있게** 한다.
3. **받는 쪽 적용 (권장)**:
   - 데이터가 그 값과 **확실히 일치**하면 뺀다.
   - 데이터에 그 정보가 **없거나**(예: 색이 기록되지 않은 상품) 근사로만 일치하면, 해당한다는 근거가 없으므로 남기고 **그 개수를 알린다**.
   - 같은 값을 포함과 제외에 동시에 요청하면 둘 다 기록되고 경고가 붙는다. 사용자가 "원하지 않는다"고 명시했으므로 **제외를 우선**하는 것을 권장한다.
4. **카테고리 제외의 범위**: `minor`가 있는 제외는 **그 하분류만** 뺀다. 상분류는 없어지지 않는다 (예 2).

## 6. 예시

### 예 1. "블랙 말고 니트웨어 보여줘"
```jsonc
"requested": {
  "categories": {"include": [{"raw": "니트웨어", "major": "상의", "minor": "니트웨어", "match": "exact"}], "exclude": []},
  "gender": null,
  "attributes": {
    "include": {"color": [], "material": [], "pattern": [], "detail": [], "fit": []},
    "exclude": {"color": [{"raw": "블랙", "value": "블랙", "match": "exact"}],
                "material": [], "pattern": [], "detail": [], "fit": []}
  }
}
```
받는 쪽 해석 예: 니트웨어 중 주 색이 블랙인 것은 빼고, 색이 기록되지 않은 니트는 남기고 "색 미확인 n개 포함"을 알린다.

### 예 2. "슬랙스 빼고 하의로 기획해줘" — 상분류는 남는다
```jsonc
"categories": {
  "include": [{"raw": "하의", "major": "하의", "minor": null,   "match": "exact"}],
  "exclude": [{"raw": "슬랙스", "major": "하의", "minor": "팬츠", "match": "alias"}]
}
```
받는 쪽 해석: 포함은 **하의 전체**(`minor = null`), 제외는 **하분류 팬츠만**(`minor = 팬츠`). 결과는 하의 중 팬츠를 뺀 **청바지·스커트·레깅스·조거팬츠**다. 제외 항목의 `major: 하의`는 팬츠가 속한 상분류를 알려 주는 정보일 뿐, 하의 전체를 빼라는 뜻이 아니다. (trend의 현재 검색도 `minor`가 있으면 하분류로만 비교한다.) 하의인데 하분류가 기록되지 않은 아이템은 팬츠라는 근거가 없으므로 남는다.

### 예 3. 출처가 섞인 경우 — "꾸안꾸 느낌 버터옐로우 오버핏 후드티 기획해줘, 블랙은 빼고"
```jsonc
{
  "schema_version": "1.0",
  "request_id": "req-003",
  "original_query": "꾸안꾸 느낌 버터옐로우 오버핏 후드티 기획해줘, 블랙은 빼고",
  "trend_terms": {"season": {"year": 2027, "season": "SS", "raw": null, "origin": "default_next"},
                  "style_names": [], "item_terms": []},
  "requested": {
    "categories": {"include": [{"raw": "후드티", "major": "상의", "minor": "후드티", "match": "exact"}], "exclude": []},
    "gender": null,
    "attributes": {
      "include": {"color": [{"raw": "버터옐로우", "value": "옐로우", "match": "approx"}],
                  "material": [], "pattern": [], "detail": [],
                  "fit": [{"raw": "오버핏", "value": "오버사이즈", "match": "llm"}]},
      "exclude": {"color": [{"raw": "블랙", "value": "블랙", "match": "exact"}],
                  "material": [], "pattern": [], "detail": [], "fit": []}
    }
  },
  "unsupported": [{"type": "abstract", "raw": "꾸안꾸 느낌", "reason": "표준 어휘로 바꿀 수 없는 추상 표현", "polarity": "include"}],
  "missing_fields": []
}
```
받는 쪽 해석 예 (trend): 후드티, 블랙 제외는 확실한 조건(`exact`). 옐로우(`approx`)와 오버사이즈(`llm`)는 확실하지 않으므로 필터 대신 순위나 표시에 쓰고, "꾸안꾸"는 반영할 수 없다고 알린다.

### 예 4. "가방이랑 신발은 빼고, 자라 말고"
```jsonc
"categories": {"include": [], "exclude": []},
"unsupported": [
  {"type": "category", "raw": "가방", "reason": "표준 카테고리(상분류 4·하분류 21)에 없음", "polarity": "exclude"},
  {"type": "category", "raw": "신발", "reason": "표준 카테고리(상분류 4·하분류 21)에 없음", "polarity": "exclude"},
  {"type": "brand", "raw": "자라", "reason": "Product 데이터는 Musinsa뿐", "polarity": "exclude"}
]
```
참고: 실제 사전에서 가방은 "값 없음"(범위 밖으로 명시), 신발은 "사전에 없음"이다. 코드(`resolve.py`)는 둘을 구분하는 사유 키(`null_value`·`not_in_dictionary`)를 낸다. 단, 프롬프트 v2는 가방·신발 같은 품목을 LLM이 `unsupported`로 바로 보내게 하므로, 이 구분이 생기려면 조립 단계(U4)가 그 항목도 사전으로 한 번 조회해야 한다(계획 §4 U4 할 일 4). 사유 키를 서로 다른 `reason` 문장으로 바꾸는 것도 U4다. 위 예의 문장은 그 전의 임시 문장이다.

받는 쪽 해석 예: trend 데이터에는 가방·신발이 원래 없어 영향이 없다. 제외 요청은 추적 기록으로 남는다. 브랜드를 다루는 Agent가 생기면 `polarity: exclude`를 보고 뺄 수 있다.

## 7. trend의 현재 계약(`TrendQuery`)과의 차이
| 항목 | `TrendQuery` (trend 현재) | `StructuredQuery` 1.0 |
|---|---|---|
| 이름·소유 | `TrendQuery`, trend 소유 | `StructuredQuery`, 이 저장소 소유 (Y1) |
| 버전 | 없음 | `schema_version` |
| 속성 | `attributes: [{type, raw, value}]` (포함만) | `attributes: {include: {종류: [...]}, exclude: {종류: [...]}}` (X2) |
| 출처 표시 | 없음 | 모든 값에 `match` |
| 지원 불가 조건 | `{type, raw, reason}` | + `polarity` |
| 카테고리 `major` | `null` 가능 | 항상 있음 (정하지 못한 카테고리는 `unsupported`) |
| 성별 `raw` | `null` 가능 | 항상 원문 |
| `style_names`·`item_terms`의 `value` | 문자열 또는 `null` | `null`만 (어휘 없음) |
| 모르는 필드 | 무시 (pydantic 기본) | 거부 (`additionalProperties: false`) |

이 차이 목록은 `tests/test_query_structured_models.py`의 `INTENDED_DIFFERENCES`로 검사한다. 목록에 없는 차이가 생기면 테스트가 실패한다.

trend에 넘길 때의 변환(`to_trend_query()`)과 trend 쪽 계약 반영은 **Action Item**이다([query-decisions.md §0](../docs/query-decisions.md)의 7·8). 변환에서 trend가 아직 모르는 필드(속성 제외, `match`)를 그냥 버리면 안 된다. trend가 반영하기 전에는 결과에 "반영되지 않은 조건"으로 알려야 한다 (X4 안전 조건).

## 8. 변경 이력
| 날짜 | 내용 |
|---|---|
| 2026-11-10 | 초안: 필드 정의, `match` 정의, 제외 조건 정의(X3), 예시 4개, `TrendQuery`와의 차이 |
| 2026-11-10 (2차) | Q3 = γ 확정 반영: 값이 정해지는 과정(§4.1)과 예 3의 단계별 표, 표준 목록과 별칭 사전(§4.2), 저장 범위(§4.4) |
| 2026-11-10 (3차) | G-1·G-2 확정(§4.3). U1 구현 반영: 모델·JSON Schema 생성, `CategoryRef`는 `none` 없음·`major` 항상 있음, 상분류 전체는 `exact`만, 성별 `raw` 필수, `TermRef.value`는 `null`만, 시즌 raw/origin 규칙, 모든 필드 출력·모르는 필드 거부, §7에 좁히기 행 추가. U2 확인 반영: 성별 표 항목, 원피스 처리, 가방·신발 사유 구분 |
