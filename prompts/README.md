# 프롬프트

## 질의 구조화 (`query_structuring/`)

상품 기획 요청 문장을 trend 서비스가 받는 질의로 구조화할 때 모델에 보내는 프롬프트입니다.
`src/fashion_planner_orchestrator/query/structured.py`가 쓰고, 폴더 이름은 `configs/query_structuring.json`의 `prompt.name`입니다.

| 파일 | 내용 |
|---|---|
| `system_template.md` | 시스템 문장. `{{VOCABULARY}}`(표준 어휘)와 `{{EXAMPLES}}`(예시)를 코드가 채운다 |
| `user_template.md` | 사용자 문장. `{{QUERY}}`에 요청이 들어간다 |
| `examples.json` | few-shot 예시. 각 `draft`는 모델이 쓰는 평면 형식(`query/draft.py`의 `QueryDraft`)이다 |
| `rendered_system.md` | 실제로 보내는 시스템 문장 전체(검토용 생성물). `scripts/render_query_prompt.py`로 다시 만든다 |

버전 폴더는 두지 않습니다. 결과와 평가 보고서는 프롬프트를 `prompt_id`(`query-structuring`)와 템플릿 세 파일의
sha256(`prompt_sha256`)으로 식별하므로, 프롬프트를 고치면 해시가 바뀝니다. 프롬프트를 고친 뒤에는
`scripts/render_query_prompt.py`를 실행하고 `tests/test_query_prompt.py`로 예시 규칙을 확인하세요.

## 기획서 SWOT (`swot/`)

`swot/system.md`는 기획서의 SWOT·프로모션·차별화 포인트 초안을 만드는 시스템 프롬프트입니다
(`src/fashion_planner_orchestrator/swot.py`). 사용자 메시지는 `swot.swot_input()`이 만든 실행 기록 요약 JSON이고,
답은 `swot.parse_swot()`이 검증합니다. 차별화 포인트는 novelty 초안의 `draft.differentiation`이 비어 있을 때만
씁니다. 저장 레코드는 프롬프트를 `prompt_sha256`(`system.md`의 sha256)으로 식별합니다. `meta.json`은 입력과
출력 형식을 설명합니다.
