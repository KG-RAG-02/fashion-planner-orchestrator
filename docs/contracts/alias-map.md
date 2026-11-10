# 별칭 사전 스냅샷 (읽기 전용 복사본)

> **`alias-map.snapshot.json`과 `alias-map.golden.json`은 trend-analysis-agent가 소유한 별칭 사전의 복사본과 그 원본 코드의 실행 결과다.** 직접 고치지 않는다. 사전을 바꾸는 것은 trend 저장소에서만, **D-29 규칙**(이미 있는 표준 값의 표기 변형만 `aliases`에 추가, [query-decisions.md §4](../query-decisions.md))과 사용자 승인으로 한다. 바뀐 원본은 아래 "갱신 절차"로 다시 가져온다. 근거 결정: H1(사전을 읽기 전용으로 따름)·H6(YAML → JSON 스냅샷 + 원본 코드 기대값으로 검증).

| 항목 | 값 |
|---|---|
| 원본 | trend-analysis-agent `configs/alias_map/*.yaml` 11개 속성 + `text_guard.yaml`, 변환 코드 `src/trend_agent/ingestion/normalization/alias_map.py`·`text_match.py`, 표준 값 `src/trend_agent/schemas/vocabulary.py` |
| 제외한 원본 | `category_otpensource.yaml`, `set_components.yaml`, `set_overrides.yaml` (02 데이터 적재 전용) |
| 원본 기준 | `_provenance.source_ref` = `<브랜치>@<HEAD 커밋>`, `files_last_commit` = 위 파일들을 마지막으로 바꾼 커밋, `source_dirty` = 생성 시 원본 파일에 커밋 안 된 변경이 있었는지(항상 `false`여야 함) |
| 내용 해시 | `_provenance.content_sha256` = `_provenance`를 뺀 데이터를 키 순서 그대로 압축 JSON(UTF-8)으로 만든 SHA-256. 생성기가 다시 계산해 손으로 고친 흔적을 막는다 |
| 생성 방법 | 코드로만 생성 (손으로 쓰지 않음). 아래 "갱신 절차" |

## 1. 파일 구성
| 파일 | 내용 |
|---|---|
| `alias-map.snapshot.json` | `attributes.<속성>`: YAML의 모든 절(`aliases`, `approx`, `null_values`, `route`, `hint`, 색의 `modifiers`) + 표준 값 `standard`(trend `vocabulary.py`의 Enum 순서). `text_guard.guards`: 자유 텍스트 오탐 가드. `code_constants.compound_values`: 나누지 않는 슬래시 복합 값(`alias_map.COMPOUND_VALUES`) |
| `alias-map.golden.json` | trend **원본 코드**의 출력: `normalize`(속성별, 그 속성의 모든 키 + 표기 변형(대소문자·공백·`-`·`_`) + 다른 속성의 키 + 모르는 단어), `normalize_tokens`(복수 값·블렌드/혼방), `normalize_color`(수식어·끝의 기본색·`Ncolor`·멜란지), `split_tokens`, `norm_key`, `text_match`(30개 질의 + 추가 문장의 `TextMatcher` 결과. 원래 문장 전체 스캐너(V1 계획 S3)용이었으나 V1은 γ로 대체되어 지금은 쓰지 않는다. 나중에 "띄어쓰기 단위 정확 일치" 보조 기능을 만들 때 기대값으로 쓸 수 있게 남겨 둠) |
| `src/vogue_orchestrator/query/alias_data.py` | 스냅샷에서 생성한 상수 모듈. 실행 코드는 이 모듈만 읽는다 (`docs/`·pyyaml 불필요) |
| `src/vogue_orchestrator/query/lexicon.py` | `AliasMap` 변환의 표준 라이브러리 이식 (`norm_key`, `normalize`, `split_tokens`, `normalize_tokens`, `normalize_color`) |

**절 안의 항목 순서는 YAML 순서를 그대로 둔다** (절 이름과 속성 이름만 정렬). 원본 코드는 같은 표기가 두 값에 걸리면 먼저 나온 것을 쓰므로(`setdefault`) 순서가 결과에 영향을 줄 수 있다. 현재 사전에는 이런 충돌이 없다(같은 값을 가리키는 중복 표기만 있음).

golden 형식: 각 경우는 `[입력, 기대값]`이다. 변환 결과 하나는 `normalized_fields` 순서(`raw, value, approx, route_to, hint, matched_by`)의 행이다.

## 2. 변환 규칙 (원본과 같음, 정책은 호출하는 쪽)
`normalize` 순서: 정규화 키(소문자, 공백·`-`·`_` 제거) → 표준 값 → 별칭 → 근사 → 이동(`route`) → 힌트(`hint`) → 값 없음(`null_values`) → 매핑 불가. `lexicon.py`는 결과를 **보고만** 한다. 근사 값에 `match: approx`를 붙여 전달하는 것(H2′, 결정 Q3 = γ)과 힌트를 적용하지 않는 것(H5)은 이 모듈을 쓰는 쪽(값 확정 단계 `resolve.py`, [structured-query-plan.md §2.1](../structured-query-plan.md))이 지킨다.

## 3. 갱신 절차
1. trend 저장소에서 사전이 바뀌고 커밋됐는지 확인한다 (생성은 원본을 읽기만 한다. trend 작업 트리의 브랜치를 바꾸지 않는다).
2. 이 저장소 루트에서 trend의 가상환경으로 실행한다 (pyyaml과 trend 코드가 필요한 단계는 이것뿐):
   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 \
     ../trend-analysis-agent/.venv/Scripts/python.exe scripts/export_alias_snapshot.py --trend-repo ../trend-analysis-agent
   ```
   스냅샷, golden, `alias_data.py`를 함께 다시 쓴다. `--check`를 붙이면 바뀔 파일이 있을 때 종료 코드 1.
3. 스냅샷만 바뀐 경우(`alias_data.py`만 다시 만들 때): `py scripts/generate_alias_data.py` (표준 라이브러리만, `--check` 있음).
4. `py -m unittest discover -s tests`로 확인한다. `tests/test_query_lexicon.py`가 생성 모듈 일치·출처·**golden 전부 일치**를 검사한다. 원본 코드의 변환 규칙이 바뀌었으면 golden이 달라져 테스트가 실패하므로 `lexicon.py`를 원본에 맞게 고친다. YAML에 새 절이 생기면 생성기가 멈춘다(이식 검토 필요).

## 4. 알려진 원본 동작 (그대로 이식함)
| 동작 | 예 |
|---|---|
| 색: 끝의 기본색은 영문 별칭 키(`red`·`sky`·`gold`·`cream`)로도 근사 일치 | `colored` → 레드(근사), `husky` → 스카이블루(근사), `marigold` → 골드(근사) |
| 색: 수식어는 문자열 어디서든 지운다 | `웜그레이` → 그레이(별칭) |
| 무늬: `없음`·`없는`·`none`·`null`은 `무지`의 별칭 | LLM이 "로고 없는"을 패턴 조건으로 뽑으면 사전 조회 결과가 무지가 된다 (프롬프트 v2 규칙과 값 확정 코드에서 주의) |
| 카테고리: `원피스`는 하분류 `드레스`의 별칭 (상분류 이름이기도 함) | `normalize("category", "원피스")` → 드레스 |
| 블렌드/혼방 꼬리표는 문자열 어디서든 지우고 다시 찾는다 | `혼방울` → 울/캐시미어 |
| 정규화 키는 유니코드 정규화(NFC/NFKC)를 하지 않는다 | 전각 문자·분해형 한글은 일치하지 않는다 |
| 슬래시 복합 표준 값은 자유 텍스트 토큰화에서 쪼개진다 | `울/캐시미어 코트`의 텍스트 매칭은 별칭 `캐시미어`로만 잡힌다 |
