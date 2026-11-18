# 0001 · 서비스 다섯 개와 HTTP 계약

- 상태: 채택
- 범위: 시스템 전체

## 맥락

트렌드 분석, 재고 매칭, 신상품 기획, 판매·시장 검토는 필요한 데이터와 도구가 서로 다르다(그래프 DB, DuckDB와 CLIP,
LLM과 이미지 모델, 판매 집계). 팀원이 각자 맡아 따로 개발하고 시험할 수 있어야 했고, 한 단계의 결과를 다음 단계가
어떻게 쓰는지 추적할 수 있어야 했다.

## 결정

- 역할마다 레포와 서비스를 하나씩 둔다(trend, inventory, novelty, research). orchestrator가 순서대로 부르고, 서비스끼리는
  서로 부르지 않는다.
- 연결은 HTTP + JSON envelope(`{contract_version, request_id, input}` → `{…, producer, status, result}`) 하나로 통일한다.
- 각 서비스가 자기 계약을 소유하고, orchestrator는 호출 쪽 스키마와 각 계약의 사본을 두어 바뀌면 검사로 드러나게 한다
  (`scripts/export_agent_contracts.py --check`).

## 결과

- 서비스마다 혼자 실행하고 테스트할 수 있다. 각 레포 README의 빠른 시작이 그 레포만으로 돈다.
- 계약이 어긋나면 통합할 때가 아니라 검사에서 먼저 드러난다. 실제로 연결하면서 생긴 문제 대부분이 계약 경계에서
  나왔다([troubleshooting.md](../troubleshooting.md)).
- 서비스가 다섯이라 배포와 설정이 늘었다. Docker Compose 프로필로 묶었다([0006](0006-single-host-compose-profiles.md)).

## 검토한 대안

| 대안 | 택하지 않은 이유 |
|---|---|
| 한 프로세스 안의 모듈(modular monolith) | 운영은 단순하지만, 데이터·의존성이 크게 다른 네 역할을 각자 개발·시험하기 어렵고 소유 경계가 흐려진다. 작은 팀·단일 배포만 고려하면 다시 검토할 만하다 |
| 서비스끼리 직접 호출 | 호출 순서와 실패 처리가 여러 곳에 흩어져 실행 기록 하나로 추적할 수 없다 |
| 공유 DTO 패키지 | 다섯 레포가 같은 패키지 버전에 묶인다. JSON Schema 사본과 검사로 대신했다 |
