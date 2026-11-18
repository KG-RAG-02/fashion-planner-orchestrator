# 변경 기록

이 문서는 [Keep a Changelog 1.1](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고, 버전은 [유의적 버전](https://semver.org/lang/ko/)을 따른다.

## [1.0.0] - 2026-11-18

첫 공개 릴리스. 팀 내부에서 쓰던 조정자와 UI를 공개용으로 정리했다.

### 추가
- 6단계 워크플로(trend → inventory → novelty → research → 통합 → 기획서)와 계약 게이트, SQLite 체크포인트, 내용 주소 실행 기록.
- 질의 구조화(StructuredQuery, schema_version `1.0.0`)와 TrendQuery 변환, 오프라인 합성 초안 모드.
- 실행 기록만으로 그리는 기획서 PNG(가로 3200 px), 기사 사진 받기(허용 목록), SWOT·프로모션 초안(선택).
- 정적 웹 UI(Fashion Planner Atelier): 실행 진행, 단계별 결과, 근거·원문 JSON, 기획서, MD 결정.
- Docker Compose 배포 프로필 stub·rehearsal·live와 실행기 `deploy/bin/fashion-planner`, 종단 간 검사.
- research의 배포자 동의 설정 두 개(`RESEARCH_HM_NONCOMMERCIAL_RESEARCH`, `RESEARCH_TARGET_LLM_TRANSFER_AUTHORIZED`)를 데이터·유료
  프로필에 추가. 기본은 꺼짐.
- 계약 레지스트리(`contracts/agents/`)와 검사 스크립트, 동결된 계약 기준선.
- 문서: 구조, 계약, 질의 구조화, 데이터, 트러블슈팅(11건), 설계 결정(6건), 다이어그램 4종과 근거표.
- 공개 표준 파일(LICENSE, AUTHORS, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY), 이슈·PR 템플릿, 오프라인 CI(Python 3.11·3.13).

### 변경
- 패키지와 import 이름을 `fashion-planner-orchestrator`/`fashion_planner_orchestrator`로, 콘솔 스크립트를
  `fashion-planner-orchestrator`로 바꿨다.
- 서비스 식별자를 역할 이름으로 바꿨다: agent_id `trend`·`inventory`·`novelty`·`research`·`orchestrator`, 경로
  `/v1/agents/<서비스>/…`, 전송 이름 `fashion-planner-workflow`·`trend-handoff`·`inventory-handoff`·`novelty-handoff`·
  `research-handoff`. 이름에는 버전을 넣지 않고 페이로드 버전은 `schema_version` `1.0.0` 한 곳에만 둔다.
- 환경변수를 서비스 접두 규칙으로 바꿨다: orchestrator는 `ORCHESTRATOR_*`, 공용·배포 도구는 `FASHION_PLANNER_*`.
- 오류 코드 접두를 서비스 이름으로(`NOVELTY_*`, `RESEARCH_*`).
- novelty에 보내는 투영을 하나(`novelty-input-projection`)로 줄였다.
- 프롬프트 버전 폴더를 없애고 `prompt_id`와 `prompt_sha256`로 식별한다.
- 기획서 형식 ID를 `planning-sheet`로, PNG 메타데이터 키를 `fashion-planner:*`로 바꿨다.

### 삭제
- 옛 저장 레코드 형식을 받던 분기, 투영 raw 전송 모드, 쓰이지 않는 compose 설정과 마운트, 옛 예제 서버, 내부 상태 문서.
