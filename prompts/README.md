# prompts/ — 버전별 프롬프트

LLM에 보내는 프롬프트의 **원본**이다. 코드(`src/vogue_orchestrator/query/prompt.py`)는 이 폴더의 파일을 읽어 프롬프트를 만들고, 코드 안에는 프롬프트 문장이 없다. 어느 버전을 쓸지는 [`configs/query_structuring.json`](../configs/query_structuring.json)의 `prompt.version`이 정한다.

## 버전 목록
| 프롬프트 | 버전 | 형식 (`meta.json`) | 상태 | 날짜 | 내용 | 결과에 찍히는 ID |
|---|---|---|---|---|---|---|
| query_structuring | [v1](query_structuring/v1/) | `draft_v1` | 보관 (`archived`). 단, **U4 전까지는 설정이 아직 이 버전을 실행**한다 | 2026-11-09 | 최초 버전. LLM이 값까지 고르는 추출형. 규칙 12개, 예시 6개. 실제 LLM에 보낸 적 없음 | `query-structuring-prompt/v1` |
| query_structuring | [v2](query_structuring/v2/) | `draft_v2` | 보관 (`archived`, 2026-11-10 v3가 생기면서. `meta.json`의 `status`만 바꿈 — OQ-6의 예외) | 2026-11-10 | 결정 Q3 = γ. LLM은 조건 표현·종류·포함/제외·표준 목록 안 제안 값만 내고, 값과 `match`는 코드가 별칭 사전으로 확정한다. 규칙 14개, 예시 7개. **2026-11-10 U3.5에서 고침**(첫 실행 전이라 새 폴더 대신 v2를 고침, QD-4는 이미 쓰인 버전에만 적용): 기장 kind, 원피스 → 드레스, 성별 제외·남녀, `item_terms` 범위, 예시 5 수정·예시 7 추가. 같은 날 평가 스크립트로 실제 LLM에 30회 보냄(기준 측정, [docs/query-structuring.md §14](../docs/query-structuring.md)). **다음 개선은 v3 폴더로** | `query-structuring-prompt/v2` |
| query_structuring | [v3](query_structuring/v3/) | `draft_v2` | 보관 (`archived`, 2026-11-11 v4가 생기면서. `meta.json`의 `status`만 바꿈 — OQ-6의 예외) | 2026-11-10 | v2의 기준 측정 실패 유형을 **일반 규칙으로** 보강(특정 평가 질의의 단어는 쓰지 않음, 작업 규칙 W-10): 두 이름표 목록이 다르다는 것과 "표현마다 넣을 곳" 표, 표준 품목은 언제나 category(상분류 이름 포함, item_terms는 대부분 빈 배열), 표준 속성 값 목록의 단어는 그 종류, 종류 이름만 있는 말(색상·컬러 등)은 조건 아님, "모두·둘 다"는 포함, 기장 규칙을 별도 규칙 8로, 세이프는 핏이 아님(규칙 11). 예시는 v2와 같은 7개. 결과: [docs/query-structuring.md §14.6](../docs/query-structuring.md) | `query-structuring-prompt/v3` |
| query_structuring | [v4](query_structuring/v4/) | `draft_v3` | 보관 (`archived`, 2026-11-11 v5로 대체). dev 17/30(조건을 품목 안에 중첩하자 조건을 덜 뽑음) | 2026-11-11 | **품목별 묶음**(결정 IT-1~IT-7): 출력이 `items[]`(찾는 품목마다 `raw`·`suggestion`·그 품목의 `conditions`·`unsupported`)와 `common`(질의 전체 조건, 코드가 모든 품목에 복사)으로 바뀜. "[품목과 조건을 넣을 곳]" 절: 품목 앞 꾸밈말은 바로 뒤 품목에만, "둘 다·모두"·별도 구절은 common, 품목 하나면 모두 그 품목에, 빼고 싶은 품목은 category exclude 조건, 범위 밖 품목도 찾으면 items(suggestion null, 범위는 코드가 판단). 규칙 10을 "연도가 숫자로 적혀 있을 때만 year"로 분명히 함. 나머지 규칙은 v3와 같음. 예시 11개(v3의 7개를 새 형식으로 + 품목마다 꾸밈말, 품목 없는 질의, 범위 밖 품목, 시즌 두 개). 같은 날 SQ-1·SQ-2로 출력 키 `season` → `seasons`(목록), `item_terms` → `product_model_names`, 규칙 10(시즌 여러 개) — 실행 전이라 새 폴더 없이 고침. 예시 단어는 평가 질의에서 가져오지 않음(W-10). dev 측정 후 동결 | `query-structuring-prompt/v4` |
| query_structuring | [v5](query_structuring/v5/) | `draft_v4` | 보관 (`archived`, 2026-11-11 v6으로 대체) | 2026-11-11 | **평평한 조건 + `item` 칸**(결정 FL-1): `items[{raw, suggestion}]`와 한 목록의 `conditions`·`unsupported`, 조건마다 `item`(품목의 raw, 질의 전체면 null). 묶기는 코드(`query/draft_v4.py`). "[item: 조건이 어느 품목의 것인지]" 절(IT-2, null인 경우, IT-9 첫 품목 앞 성별, 같은 이름 품목 둘). 규칙 14 새로(트렌드명은 고유한 이름만·이름 부분만). 규칙 4·9·13·15 보강. 예시 11개 = v4 예시를 새 형식으로 + 예시 8에 첫 품목 앞 성별. dev 21/30 ([docs/query-structuring.md §15.7](../docs/query-structuring.md)) | `query-structuring-prompt/v5` |
| query_structuring | [v6](query_structuring/v6/) | `draft_v4` | 현행 (`current`). `StructuredQuery` 경로(`query/structured.py` `STRUCTURED_PROMPT_VERSION`)의 기본값 — **화면 서버(2026-11-11 FW-1)와 평가 스크립트가 쓴다** | 2026-11-11 | v5와 형식·규칙이 같고 남은 실패를 겨냥해 보강: 품목은 옷의 구체적인 종류 이름("옷·제품·상품·아이템" 등 일반어는 품목 아님), 새 절 "[출력 전 확인]"(꾸밈말 둘 이상은 각각, 품목 이름 속 기장, 품목 확인, 포함/제외 확인). 예시 9("아이템"은 품목 아님)·예시 11(꾸밈말 둘 + 품목 이름 속 기장) 수정. dev 21/30 — v5와 같음 ([docs/query-structuring.md §15.8](../docs/query-structuring.md)) | `query-structuring-prompt/v6` |

## 한 버전 폴더의 파일
| 파일 | 직접 수정 | 내용 |
|---|---|---|
| `meta.json` | O (만들 때 한 번) | `{"format": "draft_v1" | "draft_v2" | "draft_v3" | "draft_v4", "status": "current" 또는 "archived"}`. `format`은 LLM 출력 형식으로, 검증 모델을 고른다(`draft_v1` → `query/legacy_v1.py`, `draft_v2` → `query/draft.py`, `draft_v3` → `query/draft_v3.py`, `draft_v4` → `query/draft_v4.py`). `status`는 문서용 표시이고 실행 버전은 코드·설정이 정한다: **화면 서버·평가 스크립트의 `StructuredQuery` 경로는 `query/structured.py`의 상수 `STRUCTURED_PROMPT_VERSION`(지금 `v6`)**, 보관된 v1 경로(`structure_query`)는 `configs/query_structuring.json`의 `prompt.version`(`v1`). `current`는 한 버전만 |
| `system_template.md` | O | 시스템 프롬프트. `{{VOCABULARY}}`(표준 어휘)와 `{{EXAMPLES}}`(예시) 자리를 코드가 채운다 |
| `user_template.md` | O | 사용자 메시지. `{{QUERY}}` 자리에 질의가 들어간다 (질의 안의 `<질의>` 태그는 전각 문자로 바뀐다) |
| `examples.json` | O | few-shot 예시 `[{title, query, draft}]`. `draft`는 `meta.json`의 `format` 형식이다 |
| `rendered_system.md` | **X (생성 파일)** | 실제로 LLM에 가는 시스템 프롬프트 전체. 읽기·검토용. `scripts/render_query_prompt.py`로 만든다 |

표준 어휘(카테고리·속성·성별 값)는 손으로 넣지 않는다. 계약 스냅샷에서 생성한 `vocabulary.py`가 `{{VOCABULARY}}`에 들어간다.

## 새 버전 만들기 (예: v2)
1. 새 폴더를 만들고(형식이 같으면 이전 버전을 복사하고 `rendered_system.md`를 지운다) `meta.json`을 쓴다. 이전 `current` 버전의 `meta.json`은 새 폴더를 만들 때 한 번만 `archived`로 바꾼다 (이 변경을 예외로 허용할지는 확인 대기 OQ-6, [query-decisions.md](../docs/query-decisions.md) §6).
2. `system_template.md`, `user_template.md`, `examples.json`을 고친다.
3. `configs/query_structuring.json`의 `prompt.version`을 `"v2"`로 바꾼다. 이전 버전 폴더는 **지우지 않는다** (어떤 결과가 어떤 프롬프트로 나왔는지 `prompt_version`으로 추적한다).
4. `PYTHONPATH=src python scripts/render_query_prompt.py`로 `rendered_system.md`를 만든다.
5. 테스트를 돌린다. `tests/test_query_prompt.py`가 모든 버전 폴더를 검사한다: 자리 표시자가 다 채워졌는지, 표준 값이 모두 들어갔는지, `rendered_system.md`가 최신인지. 예시 검사는 `meta.json`의 형식에 따라 나뉜다: draft_v1은 `legacy_v1`로 검증하고 경고 없이 조립되는지, draft_v2는 draft v2로 검증하고 `raw`가 질의의 부분 문자열인지, 제안이 표준 목록 안이고 G-2(상분류 제안은 사용자가 상분류 이름을 말했을 때만)를 지키는지.
6. 위 버전 목록 표와 `docs/query-structuring.md` §4를 갱신한다.

이미 쓰인 버전 폴더의 내용은 바꾸지 않는다. 고칠 일이 있으면 새 버전을 만든다.

**v2 (결정 Q3 = γ)**: 작성 완료 (U3, 2026-11-10). v1과 **출력 형식이 다르다**. v1은 LLM이 조건을 뽑으면서 값까지 고르는 형식이고, v2는 LLM이 **조건 표현·종류·포함/제외·표준 목록 안의 제안 값**만 낸다. 값 확정과 출처(`match`) 표시는 코드(`query/resolve.py`)가 별칭 사전으로 한다. 표준 목록은 `{{VOCABULARY}}`로 주입하고 별칭 사전은 넣지 않는다. 규칙 요약과 예시 목록은 [docs/query-structuring.md](../docs/query-structuring.md) §4.4. **실행 버전 전환(설정 `prompt.version` → v2)은 U4** ([docs/structured-query-plan.md](../docs/structured-query-plan.md)).
