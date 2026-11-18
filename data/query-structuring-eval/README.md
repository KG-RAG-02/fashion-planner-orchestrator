# 질의 구조화 평가 데이터

상품 기획 요청 문장을 trend 서비스가 받는 구조화된 질의로 바꾸는 과정을 평가하는 **합성** 데이터입니다.
실제 사용자 데이터가 아니고 런타임 설정도 아닙니다. 라벨은 AI가 작성했고 사람이 검증하지 않았습니다
(`label_status: silver`).

| 파일 | 내용 |
|---|---|
| `dev.json` | 개발용 질의 30개 |
| `holdout.json` | 홀드아웃 질의 20개. 개발용 질의와 프롬프트 예시와 겹치지 않으며, 프롬프트를 보지 않은 별도 AI 에이전트가 작성 |

각 항목은 `query`, `expected_draft`(품목별로 묶은 기대 draft), `expected_structured_query`(그 draft를 코드로
조립한 기대 결과), `expected_warnings`를 가집니다. `reference_today`는 시즌 계산의 기준 날짜입니다.

평가: `PYTHONPATH=src python scripts/evaluate_query_structuring.py --provider fixture`는 라벨을 모델 출력처럼 넣어
코드 부분(해석·조립·grouping)만 측정합니다(외부 호출 없음). `--provider llm`은 설정한 LLM을 실제로 호출하므로
비용이 듭니다. `tests/test_query_structured_path.py`가 코드가 모든 라벨을 재현하는지 확인합니다.
