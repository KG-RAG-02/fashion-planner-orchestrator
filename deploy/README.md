# VOGUE 통합 배포 (Docker Compose)

다섯 서비스를 한 번에 띄우는 배포 정의입니다. 대상 서비스는 trend-analysis-agent, stock-agent, new-product-agent(P06), sales-market-agent(P07), vogue-orchestrator(P08)입니다. 이 폴더는 orchestrator 저장소 안에 있습니다. 실행하려면 다섯 저장소가 같은 상위 폴더 아래에 나란히 체크아웃돼 있어야 합니다.

```
browser ──127.0.0.1:8765──► orchestrator (UI · workflow · artifact store · 최종 이미지)
                               │ backend 네트워크 (internal, bearer 인증, 허용된 호스트만 평문 HTTP)
        ┌──────────────┬───────┴──────┬────────────────┐
     trend:8768     stock:8002   new-product:8767  sales-market:8766
```

- **외부 공개 포트**는 orchestrator의 `127.0.0.1` 포트 하나뿐입니다. 에이전트끼리는 `backend` 내부 네트워크에서 공유 bearer 토큰으로 호출합니다.
- **artifact 교환**: P08 blob과 P07 review artifact는 읽기 전용 volume subpath로 주고받습니다. 크기와 sha256을 검증합니다(`docs/workflow-integration-conventions.md` §8).
- **실행 순서**: 단계는 순차로만 실행됩니다. Trend → Stock → Draft(P06) → Review(P07) → 통합 → 최종 이미지 순이며, 이미지는 리뷰 뒤에 최대 1회입니다.

## 프로필

| 명령 | 구성 | 외부 호출 | UI |
|---|---|---|---|
| `vogue stub` | `compose.yaml` + `compose.stub.yaml` | 없음 | `http://127.0.0.1:18765` |
| `vogue rehearsal` | + `compose.data.yaml` | Aura·R2 읽기, 유료 모델 0회 | `http://127.0.0.1:8765` |
| `vogue live "<기획 질의>"` | + `compose.paid.yaml` | 위 항목 + OpenAI | `http://127.0.0.1:8765` |

**stub 프로필 구성**
- 임시 Neo4j(tmpfs)에 합성 그래프를 적재합니다.
- 재고는 합성 DuckDB에 결정적 PolicyLLM을 붙여 계산합니다.
- 시장 검토는 합성 source를 씁니다(합성 표기).
- 이미지는 fixture 렌더러로 만듭니다.
- 예시 질의: `2027년 봄 발레코어에서 유행하는 핑크색 상의를 기획하고 싶어`

**rehearsal 프로필**
- 실데이터(Aura 그래프, R2 DuckDB)로 연결·데이터·지연·payload 크기를 확인합니다. 유료 모델은 부르지 않습니다.
- 질의 구조화도 생략합니다. 대신 `e2e/queries/rehearsal-trend-query.json`의 TrendQuery를 그대로 보냅니다.

**live 프로필**
- 한 번만 실행됩니다. guard 파일(`~/.config/vogue/live-run.done`)이 있으면 다시 실행되지 않습니다.
- 이미지 재시도는 꺼져 있습니다.

## 빠른 시작

명령은 한 줄씩 붙여 넣으세요.

```bash
cd "$HOME/path/to/workspace/vogue-orchestrator"
deploy/bin/vogue init-env
deploy/bin/vogue build
deploy/bin/vogue stub
```

**`init-env`**
- iCloud 밖에 `~/.config/vogue/vogue.env`(권한 600)를 만듭니다.
- bearer 토큰은 새로 생성합니다.
- R2·OpenAI 값은 서비스 `.env`에서 화면에 출력하지 않고 복사합니다.
- Aura 접속정보(`NEO4J_URI`·`NEO4J_USER`·`NEO4J_PASSWORD`)는 직접 채워야 합니다.
- 다시 실행하면 빠진 키만 채웁니다.

**rehearsal과 live**
```bash
deploy/bin/vogue preflight rehearsal
deploy/bin/vogue rehearsal
deploy/bin/vogue live "27SS 여성 스커트 기획 — 절제된 맥시멀리즘 무드의 칼럼 실루엣 캡슐"
```

`preflight`가 확인하는 항목:
- Docker 엔진
- env 파일 권한과 필수 키 (이름만 출력)
- 빌드 경로 안의 iCloud dataless 파일
- 디스크 여유와 포트 충돌
- `docker compose config --quiet`
- live에서는 추가로: OpenAI 모델 사용 가능 여부(`/v1/models`, 무과금), Aura TCP 도달성

**상태·로그·정지**
```bash
deploy/bin/vogue ps stub
deploy/bin/vogue logs stub orchestrator
deploy/bin/vogue down stub
```

`down`은 volume을 지우지 않습니다. `-v` 옵션을 쓰지 마세요. R2 캐시 volume(약 0.8 GB)과 실행 기록이 사라집니다.

## 증거와 기록

- end-to-end 결과는 `~/VOGUE-archive/e2e/<profile>-<시각>.json`에 남습니다.
  - 내용: 단계별 상태, 지연, 응답 크기, 이미지 호출 수, 검증 결과.
- 실행 기록은 P08 volume에 보존됩니다. UI의 Runs 화면이나 `GET /api/workflow/runs`로 다시 볼 수 있습니다.
- MD 결정(채택/수정/보류)은 append-only로 기록됩니다. 결과 원문은 바뀌지 않습니다.

## 원칙

- `no_results`는 0이 아닙니다.
- 재고 수량(가상 재고 2020-09-22 sample)과 H&M 과거 판매·합성 시나리오는 현재 자사 실적이 아닙니다.
- 가격·원가·수량·발주·최종 채택은 MD가 판단합니다.
- 비밀값은 compose 파일, 로그, 커밋에 넣지 않습니다. `docker compose config`는 `--quiet`로만 실행합니다.

## 문제 해결

| 증상 | 확인 |
|---|---|
| 빌드나 테스트가 CPU 0%로 멈춤 | iCloud가 비운(dataless) 파일 때문입니다. `vogue preflight`가 경로를 알려 줍니다. Finder에서 해당 폴더를 "다운로드"하세요. |
| trend가 재시작을 반복함 | Neo4j에 연결하지 못한 상태입니다. stub이면 `neo4j-stub`과 seed 로그, rehearsal/live면 Aura 정보와 네트워크를 확인하세요. |
| stock이 healthy가 되지 않음 | R2 캐시(`stock-cache-init` 로그)를 확인하세요. 로그에 DuckDB 버전과 파일 storage 버전이 함께 출력됩니다. |
| UI에서 403 | 브라우저 주소를 정확히 `http://127.0.0.1:<port>` 또는 `http://localhost:<port>`로 여세요. Host/Origin 허용 목록 때문입니다. |
| 409 `CONTRACT_NOT_FROZEN` | `contracts/workflow/baseline.json`이 frozen이 아닙니다. `scripts/freeze_workflow_contract.py --check`로 확인하세요. |
