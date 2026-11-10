# prompts/ — 버전별 프롬프트

LLM에 보내는 프롬프트의 **원본**이다. 코드(`src/vogue_orchestrator/query/prompt.py`)는 이 폴더의 파일을 읽어 프롬프트를 만들고, 코드 안에는 프롬프트 문장이 없다. 어느 버전을 쓸지는 [`configs/query_structuring.json`](../configs/query_structuring.json)의 `prompt.version`이 정한다.

## 버전 목록
| 프롬프트 | 버전 | 형식 (`meta.json`) | 상태 | 날짜 | 내용 | 결과에 찍히는 ID |
|---|---|---|---|---|---|---|
| query_structuring | [v1](query_structuring/v1/) | `draft_v1` | 보관 (`archived`). 단, **U4 전까지는 설정이 아직 이 버전을 실행**한다 | 2026-11-09 | 최초 버전. LLM이 값까지 고르는 추출형. 규칙 12개, 예시 6개. 실제 LLM에 보낸 적 없음 | `query-structuring-prompt/v1` |
| query_structuring | [v2](query_structuring/v2/) | `draft_v2` | 현행 (`current`). 실행 전환은 U4 | 2026-11-10 | 결정 Q3 = γ. LLM은 조건 표현·종류·포함/제외·표준 목록 안 제안 값만 내고, 값과 `match`는 코드가 별칭 사전으로 확정한다. 규칙 14개, 예시 6개, 렌더링 7,059자. 실제 LLM에 보낸 적 없음 | `query-structuring-prompt/v2` |

## 한 버전 폴더의 파일
| 파일 | 직접 수정 | 내용 |
|---|---|---|
| `meta.json` | O (만들 때 한 번) | `{"format": "draft_v1" 또는 "draft_v2", "status": "current" 또는 "archived"}`. `format`은 LLM 출력 형식으로, 검증 모델을 고른다(`draft_v1` → `query/legacy_v1.py`, `draft_v2` → `query/draft.py`). `status`는 문서용 표시이고 **실행 버전은 설정이 정한다**. `current`는 한 버전만 |
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
