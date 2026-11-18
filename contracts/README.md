# 계약 파일

이 폴더에는 orchestrator 경계의 계약이 있다. 규칙과 설명은 [docs/contracts.md](../docs/contracts.md)에 모았다.

| 위치 | 내용 |
|---|---|
| [`workflow/`](workflow/) | orchestrator가 소유하는 호출 쪽 스키마(novelty·research 요청·응답, 통합 결과, 공통 정의), trend·inventory 결과의 소비 쪽 사본(`upstream/`), 스키마 바이트 고정(`snapshot.json`), 계약 기준선(`baseline.json`), 호출 행렬(`source-consumer-matrix.json`) |
| [`agents/`](agents/) | 각 서비스 계약의 사본과 출처([`agents/SOURCES.md`](agents/SOURCES.md)), StructuredQuery 계약(`agents/orchestrator/output/`), trend 별칭 사전 사본(`agents/trend/vocabulary/`) |

서비스가 소유한 계약의 원본은 그 서비스 레포에 있다. 이 폴더의 사본은 어긋남을 찾기 위한 증거이고, 실행 경로가 아니다.

```bash
python scripts/freeze_workflow_contract.py --check   # 기준선·스냅샷
python scripts/export_agent_contracts.py --check     # 서비스 계약 사본(형제 레포가 있을 때)
```
