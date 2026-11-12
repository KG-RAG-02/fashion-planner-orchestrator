# 목표 워크플로우 계약 후보 — 0.1.1

> **Migrated 2026-11-13** from the local `_workflow/contracts/` workspace into `vogue-orchestrator/contracts/workflow/`. References below to `decisions/contract-baseline.json`, `handoffs/…` or `PLAN.md` are historical: the baseline now lives in `contracts/workflow/baseline.json` (written only by `scripts/freeze_workflow_contract.py`), the reference validator and fixtures in `tools/workflow_contract/`.

상태: **proposed / G1 미동결**. 사용자 목표는 `target-workflow.md`다. JSON Schema와 coordinator reference validator의 합성 fixture 통과는 현재 producer/consumer 또는 실제 서비스 통과가 아니다. 기계 판정은 `../decisions/contract-baseline.json`을 따른다.

## 1. 버전과 호출 책임

새 transport는 `vogue-workflow/0.1`, baseline 식별자는 `workflow-contract/0.1.1`으로 제안한다. 새 producer schema는 각각 `planning-draft/1.0`, `sales-market-review/1.0`, `integrated-result/1.0`이다. 기존 Trend1.1/Stock1.0/PlanningRequest1.0와 기존 transport handoff0.1/P06 handoff0.3는 바꾸지 않는다. 전체 현재·제안 matrix는 `source-consumer-matrix.json`이다.

| 순서 | 호출자 → 수신자 | 경로 | 입력 / 반환 |
|---|---|---|---|
| 1 | P08 → Trend | 기존 `POST /v1/agents/trend-analysis-agent/runs` | 기존 TrendQuery / Trend1.1 envelope |
| 2 | P08 → Stock | 기존 `POST /v1/agents/stock-agent/runs` | **Trend result 원본** / Stock1.0 envelope |
| 3 | P08 → P06 | 새 `POST /v1/agents/new-product-agent/drafts` | planning-draft-request / response |
| 4 | P08 → P07 | 새 `POST /v1/agents/sales-market-agent/reviews` | sales-market-review-request / response |
| 5 | P08 내부 | 새 통합 runner | Draft+Review 통합 후 최종 이미지 |
| 6 | P08 → UI | `POST /v1/orchestrations` 결과 | integrated-result |

P06 기존 `/runs`와 brief/variant/image 경로는 별도 호환 기능이다. P06→P07 bridge와 P08 이관 생성기는 목표 경로에 쓰지 않는다. 새 경로는 현재 존재하거나 동작한다고 주장하지 않는다.

## 2. 구조, 원본, hash

요청은 `{contract_version, request_id, input}`, 정상 domain 응답은 `{contract_version, request_id, producer, status, result}`다. 동일 요청의 outer/inner/source/stage ID, producer agent/schema, outer/inner status를 모두 비교한다. supported matrix 밖 version은 **추론/DB 조회 전에** 거부한다. Trend1.1 producer는 `ok/no_results/error`만 생성한다. HTTP handler의 partial 허용을 새 producer 기능으로 해석하지 않는다.

`retained_*`는 다음을 함께 보존한다.

- `raw_response`: 받은 전체 JSON envelope의 독립 복사. Evidence, errors, limitations, artifacts, 관측시점, synthetic, Stock null slot·trend별 결과를 삭제/이동/상태 재작성하지 않는다.
- `raw_artifact`: **실제 받은 bytes**의 immutable 저장 ref, bytes SHA-256, size, media_type. P08 private artifact store가 소유하고 읽는 역할만 접근한다. JSON 파싱 실패/HTTP 오류도 bytes artifact로 남긴다. 서비스가 envelope를 보내지 않았으면 producer envelope를 만들어 원문인 것처럼 기록하지 않는다.
- `content_sha256`: parsed 전체 envelope의 canonical-object hash. `hash_profile=python-json-sort/1`: Python 3.12 `json.dumps(sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')`의 SHA-256. 기존 P06/Stock hash와 같은 기반이지만 source body hash와 envelope hash를 구분한다. NaN/Infinity·중복 JSON key·비문자 key는 거부한다. RFC8785/JCS라고 부르지 않는다.
- `observation`: 보존 계층의 별도 관측 metadata. 원문에 없는 as_of/synthetic는 null/unknown과 이유다. 원문 sample/as_of를 current 값으로 대체하지 않는다. 여러 기준일은 단일 기준일로 꾸미지 않고 mixed/null과 원문별 값을 유지한다.

`source_request`는 P06이 받은 요청 전체를 보존하고 `source_request_sha256`으로 연결한다. Stock `input_provenance.source_payload_sha256`은 **Trend result body**의 hash이며 Trend envelope hash와 다르다. Stock1.0 top-level additionalProperties=false를 변경하지 않고 snapshot metadata는 retained observation에 둔다. 원문 provenance가 없을 때 검증된 값으로 채워 넣지 않는다. 목표 profile은 수락 가능한 Stock ok/partial/no_results의 input_provenance를 필수로 요구한다. 생략된 legacy 응답은 raw를 보존한 계약 오류로 중단하며 legacy schema 자체를 고쳐 원문을 바꾸지 않는다. source unavailable/error 원문은 provenance가 없어도 오류 보존 대상으로 남긴다.

Stock wire 요청은 기존 handoff/0.1의 Trend result 원본 그대로다. 사용자 원문 전체를 Stock에 새 필드로 주입하지 않는다. 대신 P08이 `stock_call_context`에 PlanningRequest hash, Trend envelope hash, Stock input body hash, 실제 Stock 요청 envelope hash, Stock 응답 envelope hash를 함께 남겨 명시적 요청과 해당 호출을 연결한다. 이 sidecar는 Draft source_request에 보존되며 Stock 자체가 사용자 원문을 해석했다고 주장하지 않는다. 구조화된 사용자 요구와 적용 필터 충돌은 묵시적으로 덮어쓰지 않고 stop/limitation으로 반환한다. Stock 입력 의미 변경이 필요하면 별도 versioned 계약을 검토한다.

검색용 `derived_view`는 `purpose=search_only`, adapter_version, source envelope hash, JSON pointer를 갖는다. category/season은 해당 pointer 값과 일치해야 한다. full Draft를 이 view로 대체하지 않는다. display status가 필요하면 UI 별도 derived view에 두고 raw status를 보존한다.

## 3. Draft → Review → 이미지

Draft-only 요청은 PlanningRequest1.0 + 사용자 원문 + retained Trend/Stock만 받는다. P06 응답에는 `image_generation_calls=0`, `review_calls=0`이며 이미지 artifact는 금지한다. Draft의 JSON 본문과 `draft_identity={draft_id,revision,content_sha256}`를 반환한다. 본문 hash는 identity 자신을 제외한 **draft 객체 전체**에 대해 계산한다. draft_id는 planning thread에서 안정적이며 입력 또는 초안 변경 때 revision을 증가시킨다. no_results/error의 draft/identity는 null이다.

P07은 full retained Draft를 받아 immutable input artifact로 보존한다. `review.reviewed_draft`에 정확한 Draft identity, `draft_response_sha256`에 전체 Draft envelope hash를 기록한다. `review_identity` hash는 reviewed_draft를 포함한 **review 객체 전체**다. review revision/hash가 바뀌면 이전 이미지와 혼합하지 않는다. 숫자 metric은 기간·단위·통화·비교가능성·관측일·synthetic·근거를 갖는다. 미확인 metric은 `value=null`과 missing_reason이다. 실제 근거 있는 0은 허용하며 no_results에서 0을 합성하지 않는다. 외부 historical/synthetic 시장 자료는 현재 자사 실판매가 아니다.

Trend의 ok는 trends와 evidence가 비어 있지 않아야 한다. empty-ok는 원문을 no_results로 정규화하지 않고 계약 오류로 중단한다. Trend Evidence ID는 실제 producer 규칙 `trend:<article_id>:<block_idx>:<SHA256(quote UTF-8) 앞8자>`와 같아야 하고, 중복 ID 및 모든 style/description/item evidence 참조의 누락을 거부한다. 이 규칙은 원문 1.1을 재작성하거나 출처를 추정해 채우는 허가가 아니다.

fact claim은 검증 가능한 Evidence ref가 필수다. interpretation/proposal은 종류를 명시한다. Evidence ref는 source document hash와 JSON pointer·evidence_id를 연결한다. P07의 새 검색 자료도 원문 artifact와 출처·권한·시점을 보존해야 한다. 제한적인 기존 `to_dict()`나 proposal serializer를 새 Review로 이름만 바꿔 사용하지 않는다.

P08 final_image는 다음 lineage를 모두 갖는다: Draft identity+envelope hash, Review identity+envelope hash, integration_input_sha256, 최종 image artifact bytes hash. integration_input_sha256은 `{draft_response_sha256,review_response_sha256,integration}`의 canonical hash다. Review가 참조한 Draft와 이미지가 참조한 Draft는 정확히 같아야 한다. 정상 한 실행은 최종 이미지 provider **최대1회**, Draft/Review 전 이미지0회다. fixture의 provider_calls=1은 기대 trace이며 이번 실제 provider 호출은0회다.

영속 checkpoint 키는 request_id/stage/input_sha256/contract_version에 결합한다. 중복 이벤트/같은 입력 재전달은 저장된 artifact를 사용하며 생성 재실행을 유발하지 않는다. 이미지 재시도는 유효 Draft/Review hash가 같을 때 해당 단계만 별도 attempt로 수행한다. 현재 schema의 stage trace는 한 attempt다. attempt 간 중복 방지는 구현·QA에서 별도 검증한다.

## 4. 입력과 artifact 저장

user_input.text는 공백만인 값을 제외한1–8000문자, 이미지는0–1개다. PlanningRequest.query는 user_input.text와 같다. 이미지 MIME는 PNG/JPEG/WebP, 단일 파일 최대10,485,760 bytes다. P08에 별도 `POST /v1/input-images` 업로드 후 반환한 private `artifact://` ref/hash/size/MIME만 JSON 요청에 넣는다. 임의 URL·data URL·로컬 파일 경로를 입력 이미지 ref로 받지 않는다. 경로 이탈/symlink/타 사용자 artifact, 실제 bytes hash·MIME·decoder 실패는 거부한다. 원본 bytes는 보존하고 변환본은 별도 derived artifact로만 기록한다.

새 JSON endpoint는 UTF-8 body 최대8,388,608 bytes, 보존 response bytes는 최대16,777,216 bytes로 제안한다. 초과하면413이며 원문을 조용히 자르지 않는다. 기존 Trend/Stock/P06 legacy 한도는 각 현재 API대로다. 큰 Stock inline 이미지 때문에 새 한도를 넘으면 error를 남기고 schema 버전/명시적 artifact 모드 변경을 별도 검토한다. 임의 축소는 금지한다. 업로드·decoder·외부 전송·artifact 접근 구현은 아직 검증되지 않았다. reference validator는 fixture file hash와 magic signature 및 PNG chunk CRC만 확인한다. 실제 업로드 decoder·사용자 이미지 시각 품질 검증은 아니다.

## 5. 상태·오류·중단 정책

| 관측 | 다음 단계 / 이미지 | integrated 상태 |
|---|---|---|
| Trend ok | Stock → P06 → P07 순차 | 후속에 따름 |
| Trend no_results | Stock 포함 후속 호출0, 원문 보존 | no_results, 이미지 skipped |
| Stock partial/no_results | 증거/한계를 유지해 P06; 유효 Draft면 Review | 최소 partial; 빈 재고를0으로 만들지 않음 |
| Draft no_results | Review·이미지0 | no_results |
| Review ok/partial + eligible basis | 통합, 그 뒤 이미지 최대1회 | 전체 상태에 따름; partial 유지 |
| Review no_results | 텍스트 초안/누락 유지, 이미지0 | partial |
| 어느 단계든 error/인증실패/ID·producer·version·hash 위반 | 즉시 후속 중단, 원문 오류 보존, 이미지0 | error |
| 유효 Review 뒤 이미지 실패 | Draft/Review·실패 trace 유지, 이미지 단계만 재시도 가능 | partial, image failed |

새 서비스: malformed JSON400, 의미적 계약위반422, 인증401/403, 크기413, MIME415, upstream 계약위반502, source unavailable503, timeout504. 문제 응답은 common.schema.json의 `$defs/problem`을 사용한다. 모르는 request_id는 null이며 원문 비밀/헤더를 에러 메시지에 넣지 않는다. 정상 partial/no_results는200이다. legacy API가 domain error를200에 담아도 P08은 status=error를 terminal로 처리한다. transport 실패를 review failure나 partial/200으로 바꾸지 않는다.

오류 bytes/기록은 private trace에 유지한다. rejected_response_artifacts는 검증 실패 원문이고 신뢰한 sources로 승격하지 않는다. configured, reachable, authenticated, schema-compatible, data-verified, end-to-end 판정을 독립적으로 유지한다.

## 6. 검증과 gate

필수 schema5개 + common/upstream snapshot3개를 local Draft202012Validator(외부 ref retrieval 금지)로 검사한다. synthetic fixtures/index.json과 JSON/PNG 실제 fixture artifact를 handoffs/coordinator에 보존한다. 구조 검증 뒤 reference consumer의 ID/status/hash/Evidence/null slot/시점/순서/중단/lineage 검증을 한다. 이 reference validator는 owner의 독립 consumer 구현 증거를 대신하지 않는다.

G1 동결에는 owner가 제공한 독립 producer serializer fixture와 consumer parser의 양방향 결과가 필요하다. plan 단계에서는 각 `handoffs/<role>/`에 순수 reference parser/serializer·fixture와 실행 증거를 둘 수 있으며 repo 구현을 선행 조건으로 요구하지 않는다. coordinator 자체 generator→자체 validator 통과만으로 producer_consumer_validated를 true로 쓰지 않는다. 현재 source 부정 호환 결과도 그대로 유지한다. 독립 검증이 성립하면 coordinator가 schema·목표·matrix 실제 SHA와 source_heads/validation_artifacts를 대사해 atomic frozen을 기록한다. 기존 API의 미구현은 해당 구현 owner의 작업이며, 목표 계약의 독립 검증 미완료와 별도 판정한다. 수동 독립 세션은 supervisor를 사용하지 않는다. 미동결은 독립 기동 복구·단위 구현을 막지 않으며, 저장소 간 연동의 확정 판정만 보류한다.

가격·원가·수량·발주·최종 채택은 MD 책임이다. 결과의 MD 상태는 pending이고 선택은 adopt/revise/hold다. 이 계약은 실제 데이터·현재 자사재고·실판매·모델 품질을 보증하지 않는다.


## 7. 0.1.1 변경과 단계 hash 규약

0.1.0 → 0.1.1은 동결 전 후보의 의미 검증 보완이다. transport `vogue-workflow/0.1`과 세 producer schema 이름은 유지한다. JSON 필드 구조를 추가·삭제하지 않았다. schema의 `$comment`는 이 후보 식별자를 명시하므로 bytes SHA가 달라진다. 이후 호환되지 않는 필드/의미 변경은 별도 transport/producer 버전으로 분리한다. 원 source schema snapshot 두 개는 유지한다.

변경 이유: 0.1.0 reference에 대한 mutation probe 7건 중 6건이 잘못 수락됐다(`handoffs/coordinator/semantic-gaps-before.json`). 0.1.1은 단계 input·통합 response hash, null 통합과 이미지, Trend/Draft/Review no_results 집계를 검증한다. typed canonical JSON 비교로 원문 숫자 `7`을 `7.0`으로 바꾸는 것도 거부한다. 이전 증거는 `handoffs/coordinator/history/` ZIP에 보존했다. 현재 fixture 66건과 gap probe 7건은 reference 범위이며 독립 owner 전체 수용 증거가 아니다.

`stages[].input_sha256`은 아래 **논리 입력 snapshot**의 canonical hash다. 실제 HTTP 전송 bytes hash라고 표시하지 않는다. 실제 발신 요청 bytes와 수신 bytes는 각각 private immutable artifact로 보존한다. Stock wire envelope hash는 별도 `stock_call_context.stock_wire_request_sha256`이다.

| stage | input_sha256의 대상 | response_sha256의 대상 |
|---|---|---|
| trend | PlanningRequest 객체 | 보존 Trend 전체 envelope |
| stock | Trend result body | 보존 Stock 전체 envelope |
| planning_draft | PlanningDraft 요청 전체 envelope | 보존 Draft 전체 envelope |
| sales_market_review | Draft 전체 envelope | 보존 Review 전체 envelope |
| integration | `{draft_response_sha256, review_response_sha256}` | integration 객체 |
| final_image | `{draft_response_sha256, review_response_sha256, integration}` | 생성 image bytes; 실패는 null |

TrendQuery wire 요청, Review derived_view 포함 요청은 위 논리 hash와 다를 수 있다. bytes artifact와 논리 hash를 혼용하지 않는다. 이 규약은 이전 fixture의 단순 previous-response hash 연쇄를 대체한다. P08 producer/trace와 QA consumer가 이 표를 함께 적용해야 한다. integration stage가 있으면 성공 시 본문과 그 hash가 필수다. 이미지에는 non-null 통합 본문 및 일치하는 lineage가 필수다.

Trend/Draft no_results로 멈춘 결과는 integrated no_results다. Review no_results는 유효 Draft를 유지한 partial이며 이미지0회다. 이 경우 텍스트 통합은 가능하며 통합 자체가 error면 error로 중단한다. 최종 이미지 실패만 partial/image.failed다. 원 producer status는 어느 경우에도 고쳐 쓰지 않는다.

Draft ID는 planning thread에 안정적이다. 선택적 `planning_request.planning_thread_id`는 common ID 문법을 따라야 하며 없으면 request_id를 thread 키로 사용한다. 같은 입력의 retry는 저장된 identity/응답을 재사용하고 새 revision을 만들지 않는다. source_request 또는 draft 내용이 달라지면 같은 draft_id의 revision을 증가시키고 이전 결과·Review·이미지를 덮어쓰지 않는다. Review는 정확한 Draft identity 전체와 envelope hash에 고정한다. 같은 Review 재전달은 review_id/revision/hash를 재사용하며 재평가는 새 review identity 또는 증가한 revision으로 구분한다. 같은 ID/revision에 다른 hash가 나타나면 충돌로 거부한다. 서로 다른 request_id를 가진 revision도 각 실행 내부의 ID 일치는 유지한다. 이 영속 이력 검증은 단일 fixture 검증과 별도로 owner/QA가 수행한다.

## 8. 수동 세션 전달·수용 조건

시작·계약 변경·검증 전후 `handoffs/<role>/status.json`을 읽고 exact SHA를 자신의 handoff에 남긴다. 파일 수신만으로 상태 파일을 저장하지 않는다. 의미 있는 변경만 compare-if-changed 후 atomic replace한다. supervisor의 phase gate, ACK, turn 한도를 독립 세션의 착수 조건으로 쓰지 않는다. 기존 자동 도구를 이 세션에서 실행하거나 수정하지 않는다.

P06/P07/P08/QA는 독립 serializer/parser, 양쪽 입력 fixture 및 소스 SHA, 실행 명령/결과를 자기 handoff에 제공한다. coordinator 코드를 import하는 검증은 reference 재사용으로 표시한다. owner parser가 코드에 나타난 것과, 그 parser가 전체 계약을 통과한 것은 별개다. 현재 source의 legacy parser 실패는 새 parser까지 미구현이라는 근거가 아니다. 각 경계를 개별 판정하고 실패를 구현 owner에게 배정한다. 기준 SHA가 바뀌면 이전 수용 증거를 자동 승격하지 않는다.
