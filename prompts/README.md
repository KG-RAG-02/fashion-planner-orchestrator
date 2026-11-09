# prompts/ — 버전별 프롬프트

LLM에 보내는 프롬프트의 **원본**이다. 코드(`src/vogue_orchestrator/query/prompt.py`)는 이 폴더의 파일을 읽어 프롬프트를 만들고, 코드 안에는 프롬프트 문장이 없다. 어느 버전을 쓸지는 [`configs/query_structuring.json`](../configs/query_structuring.json)의 `prompt.version`이 정한다.

## 버전 목록
| 프롬프트 | 버전 | 상태 | 날짜 | 내용 | 결과에 찍히는 ID |
|---|---|---|---|---|---|
| query_structuring | [v1](query_structuring/v1/) | **사용 중** | 2026-11-09 | 최초 버전. 규칙 12개, 예시 6개. 실제 LLM에 보낸 적 없음 | `query-structuring-prompt/v1` |

## 한 버전 폴더의 파일
| 파일 | 직접 수정 | 내용 |
|---|---|---|
| `system_template.md` | O | 시스템 프롬프트. `{{VOCABULARY}}`(표준 어휘)와 `{{EXAMPLES}}`(예시) 자리를 코드가 채운다 |
| `user_template.md` | O | 사용자 메시지. `{{QUERY}}` 자리에 질의가 들어간다 (질의 안의 `<질의>` 태그는 전각 문자로 바뀐다) |
| `examples.json` | O | few-shot 예시 `[{title, query, draft}]`. `draft`는 `QueryDraft` 형식이다 |
| `rendered_system.md` | **X (생성 파일)** | 실제로 LLM에 가는 시스템 프롬프트 전체. 읽기·검토용. `scripts/render_query_prompt.py`로 만든다 |

표준 어휘(카테고리·속성·성별 값)는 손으로 넣지 않는다. 계약 스냅샷에서 생성한 `vocabulary.py`가 `{{VOCABULARY}}`에 들어간다.

## 새 버전 만들기 (예: v2)
1. `query_structuring/v1/`을 `query_structuring/v2/`로 복사하고 `rendered_system.md`를 지운다.
2. `system_template.md`, `user_template.md`, `examples.json`을 고친다.
3. `configs/query_structuring.json`의 `prompt.version`을 `"v2"`로 바꾼다. 이전 버전 폴더는 **지우지 않는다** (어떤 결과가 어떤 프롬프트로 나왔는지 `prompt_version`으로 추적한다).
4. `PYTHONPATH=src python scripts/render_query_prompt.py`로 `rendered_system.md`를 만든다.
5. 테스트를 돌린다. `tests/test_query_prompt.py`가 모든 버전 폴더를 검사한다: 자리 표시자가 다 채워졌는지, 표준 값이 모두 들어갔는지, 예시가 유효한 draft이고 경고 없이 조립되는지, `rendered_system.md`가 최신인지.
6. 위 버전 목록 표와 `docs/query-structuring.md` §4를 갱신한다.

이미 쓰인 버전 폴더의 내용은 바꾸지 않는다. 고칠 일이 있으면 새 버전을 만든다.

**예정 (결정 V1, 구현 전)**: 다음 버전 v2는 v1과 **형식이 다르다**. v1은 LLM이 조건을 추출하는 "추출형"이고, v2는 코드가 찾은 후보를 LLM이 확인하는 "검수형"이다. 그래서 v2는 v1을 복사하지 않고 새로 작성하며, 자리 표시자(`{{CANDIDATES}}`)와 테스트 기준도 달라진다. v1은 보관 버전으로 남긴다. 세부는 [docs/query-candidate-review-plan.md](../docs/query-candidate-review-plan.md) S4·P-1.
