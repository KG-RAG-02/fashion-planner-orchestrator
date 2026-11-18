# workflow_contract: 계약 기준 검증 도구

커밋된 스키마(`contracts/workflow/`)와 합성 fixture로 계약을 오프라인에서 검증한다. 서비스가 소유한 계약을 만들거나
다시 정의하지 않는다.

| 파일 | 역할 |
|---|---|
| `validate_contracts.py` | 기준 소비자: JSON Schema(Draft 2020-12, 원격 참조 없음)와 의미 규칙(ID, 상태, 해시, 근거 닫힘, 계보) 검사. `collect()`가 요약을 돌려주고, `main([out_dir])`는 `.local/contract-validation/`에 보고서를 쓴다 |
| `probe_semantic_gaps.py` | 모두 거절되어야 하는 계약 변형 프로브 |
| `support.py` | 검증기와 프로브가 함께 쓰는 경로·JSON 출력 도우미 |
| `fixtures/` | 합성 사례(`index.json`)와 내용 주소 raw 파일(`raw/`, 색인 `artifacts.json`). 모두 지어낸 문서이고 외부 호출이 없다 |

`fixtures/integrated-*.json`은 기획서·SWOT·트렌드 사진 렌더 테스트용 저장 결과다. 그 안의 research 결과는 예전 응답
형식(`review`, `review_identity`)이라 결합 검사 대상이 아니다. 현재 research 응답은 `research-handoff.response.json`을 본다.

```bash
pip install -e '.[workflow]'
PYTHONPATH=src python -m unittest tests.test_workflow_contract_reference -v
python scripts/freeze_workflow_contract.py --check
```

기준선(`contracts/workflow/baseline.json`)은 `scripts/freeze_workflow_contract.py`로만 쓴다. 동결은 다섯 레포가 깨끗한
checkout일 때만 하며, 기록에는 각 checkout 내용의 SHA-256만 남는다([docs/contracts.md](../../docs/contracts.md#7-기준선-동결)).
fixture를 고칠 때는 현재 스키마를 따르고, 내용 해시가 걸린 값(`content_sha256`, raw 파일 이름)을 함께 다시 계산한다.
