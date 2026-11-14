# P08 통합 실행과 Compose 배포

이 디렉터리는 다섯 저장소의 개발·검증 스택을 구성합니다. P08은 UI와 workflow 조정자이며, Trend·Stock·P06 New Product·P07 Sales Market은 각자 데이터를 생산하거나 검토하는 Agent입니다. 실행하려면 다섯 저장소가 같은 상위 디렉터리에 있어야 합니다.

```text
Browser → P08 UI/API
             └─ P08 순차 호출: Trend → Stock → P06 /runs
                                → P06 /sales-market-reviews bridge
                                → P07 /reviews
                                → 통합 → Planning Sheet
```

Agent 간 요청은 P08이 순차 전달합니다. P06 bridge는 P07을 직접 호출하지 않습니다. 외부에 포트를 공개하는 서비스는 P08 UI뿐이며 Agent는 내부 backend 네트워크에서 통신합니다. 상품 이미지와 착장 이미지는 P06이 만들고, P08은 결과를 사용해 Planning Sheet PNG를 합성합니다.

## 현재 연결 상태

P08 Compose는 P07에 `GET /api/health` healthcheck를 설정하고 orchestrator가 P07 `service_healthy` 상태를 기다리게 합니다. 그러나 확인한 P07 `main`의 Dockerfile 기본 명령은 `python -m market_research_agent.runtime`이며 이 모듈은 HTTP 서버를 시작하지 않고 대기합니다. P07 HTTP handler도 loopback 이외의 bind를 거부합니다. 따라서 P07 담당자가 Docker 서비스 진입점과 내부 bind를 지원하기 전에는 전체 Compose workflow가 준비되지 않습니다.

P07 담당자 변경을 받은 뒤 서비스 진입점, `0.0.0.0:8766` bind, bearer 인증, healthcheck를 확인하고 아래 명령을 사용하세요. P07 API 계약은 `POST /v1/agents/sales-market-agent/reviews`이며 요청은 `vogue-sales-market-review/0.2-proposal`입니다.

## 프로필

| 프로필 | 구성 | 데이터·호출 특성 | UI |
|---|---|---|---|
| `stub` | Compose + 합성 Neo4j/Stock/P07 입력 | 외부 DB·유료 모델 호출 없이 통합 경로를 검증하도록 구성 | `http://127.0.0.1:18765` |
| `rehearsal` | stub 없는 Compose + `compose.data.yaml` | Aura·R2 데이터 사용, P06 이미지는 fixture 설정, 유료 모델 호출 없음 | `http://127.0.0.1:8765` |
| `live` | rehearsal + `compose.paid.yaml` | 운영 계정과 유료 LLM/이미지 호출 가능, 1회 실행 guard 적용 | `http://127.0.0.1:8765` |

비용이 발생할 수 있는 `live`를 실행하기 전에 입력, 연결된 실제 서비스, 예상 호출을 확인하세요. stub/rehearsal도 현재 P07 서비스 문제가 해결되기 전에는 완주하지 못합니다.

## 시작

```bash
deploy/bin/vogue init-env
deploy/bin/vogue preflight stub
deploy/bin/vogue build
deploy/bin/vogue stub
```

실행 wrapper는 `~/.config/vogue/vogue.env`를 사용합니다. `init-env`는 새 bearer token을 만들고 환경변수 존재 여부를 검사합니다. 실제 값은 채팅·로그·커밋에 붙여넣지 마세요. 다섯 저장소의 checkout 경로가 다르면 각 Compose build context를 현재 로컬 구조에 맞춰야 합니다.

P07 서비스가 준비된 뒤 rehearsal/live 실행 전 점검:

```bash
deploy/bin/vogue preflight rehearsal
deploy/bin/vogue rehearsal
deploy/bin/vogue preflight live
deploy/bin/vogue live "상품 기획 요청"
```

`live`는 단일 실행용 보호 장치가 있습니다. 다시 실행하려면 먼저 저장된 기록과 guard 정책을 확인하세요.

## 관찰·종료

```bash
deploy/bin/vogue ps stub
deploy/bin/vogue logs stub sales-market
deploy/bin/vogue logs stub orchestrator
deploy/bin/vogue down stub
```

`down`은 컨테이너만 내리고 데이터 volume은 보존합니다. 기록이나 캐시를 보존해야 하면 `docker compose down -v`를 사용하지 마세요. P08은 실행 기록·checkpoint·content-addressed artifact를 보관하고, P06/P07 데이터는 각 Agent가 소유합니다.

## 검증 자료

- `compose.yaml`, `compose.stub.yaml`, `compose.data.yaml`, `compose.paid.yaml`: 프로필별 서비스와 네트워크 설정
- `e2e/run_e2e.py`: workflow 응답·단계·아티팩트 통합 확인
- `e2e/probe_auth.py`: Agent 인증 및 P08 inbound trust 확인
- `e2e/image_retry.py`: 저장된 실행의 Planning Sheet 단계만 다시 구성
- `service-manifest.json`: 환경변수·포트·볼륨 등 배포 경계 기록

테스트 결과와 과거 실행 기록은 현재 서비스 가용성을 보증하지 않습니다. 실행 전 Compose 설정, 각 저장소의 현재 main, workflow baseline 상태를 확인하세요. 계약 baseline은 현재 `proposed`입니다.
