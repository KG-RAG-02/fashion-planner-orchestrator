# 데이터

이 레포에는 제3자 데이터(기사 원문, 사진, 상품·거래 데이터)가 들어 있지 않다. 테스트와 오프라인 실행에 쓰는 것은 모두
합성 데이터이고, 실제 데이터를 쓰려면 각 서비스의 문서를 따라 직접 준비해야 한다.

## 1. 이 레포에 있는 데이터

| 위치 | 내용 | 성격 |
|---|---|---|
| `data/query-structuring-eval/{dev,holdout}.json` | 질의 구조화 평가 질의 30개·20개와 기대 결과 | 합성. 라벨은 AI가 썼고 사람이 검증하지 않았다(`label_status: silver`) |
| `examples/fixtures/query/*.synthetic.json` | 오프라인 질의 구조화(`synthetic_fixture` 모드)가 돌려주는 초안 | 합성 |
| `tools/workflow_contract/fixtures/` | 계약 검증기의 합성 요청·응답과 내용 주소 raw 파일 | 합성 |
| `tests/fixtures/workflow/` | 워크플로 테스트의 합성 단계 응답 | 합성 |
| `contracts/agents/trend/vocabulary/alias-map.*.json` | trend 서비스의 별칭 사전 사본과 원본 코드 실행 결과 표본 | trend 레포의 설정 파일에서 만든 파생물([alias-map.md](../contracts/agents/trend/vocabulary/alias-map.md)) |
| `contracts/agents/*/` | 각 서비스 계약의 사본 | 각 서비스 레포의 스키마·코드에서 만든 파생물 |
| `web/img/atelier-silk.jpg` | UI 배경 | 이 레포의 `tools/ui-assets/silk.py`로 만든 그림 |
| `web/fonts/` | Jost, Bodoni Moda | SIL OFL 1.1, 라이선스 파일 동봉 |

합성 데이터에 들어 있는 브랜드·상품·기사 문구는 실제 대상을 가리키지 않는다. 기획서 렌더 테스트에 쓰는
`tools/workflow_contract/fixtures/integrated-*.json`의 research 결과는 예전 응답 형식(`review`, `review_identity`)을 담고
있다. 이 파일들은 화면·기획서 렌더 확인 전용이고, 현재 research 응답 형식은 `research-handoff.response.json`을 본다.

## 2. 실행할 때 쓰는 외부 데이터

orchestrator 자체는 데이터 소스를 읽지 않는다. 각 서비스가 아래 데이터를 쓰고, 프로필에 따라 준비할 것이 다르다.

| 프로필 | 필요한 것 | 안내 |
|---|---|---|
| stub | 없음. 합성 그래프, 합성 DuckDB, 합성 리뷰 소스, 합성 이미지, 합성 질의 구조화 | [deploy/README.md](../deploy/README.md) |
| rehearsal | 직접 만든 Neo4j(Aura 등)의 트렌드 그래프, 직접 만든 Cloudflare R2 버킷의 재고 DuckDB·이미지와 H&M 집계 스냅샷 | 각 서비스의 `docs/data.md` |
| live | rehearsal 전부 + 본인 OpenAI 키(비용 발생) | 같음 |

- 트렌드 그래프: [fashion-planner-trend](https://github.com/KG-RAG-02/fashion-planner-trend)의 데이터 문서.
- 재고 상품·이미지: [fashion-planner-inventory](https://github.com/KG-RAG-02/fashion-planner-inventory)의 데이터 문서. CLIP 인덱스가
  있으면(rehearsal·live) inventory가 기준 상품 이미지를 무신사 이미지 CDN(`msscdn.net`)에서 받아 메모리에서 임베딩만 하고,
  OpenCLIP 가중치는 처음 불러올 때 내려받는다. 어디서 받는지는 설치한 open_clip 버전이 정한다(open_clip_torch 4.2.2는
  Hugging Face Hub).
- H&M 집계: [fashion-planner-research](https://github.com/KG-RAG-02/fashion-planner-research)의 데이터 문서. H&M Kaggle 자료는
  비상업 연구에만 쓸 수 있다. research는 `RESEARCH_HM_NONCOMMERCIAL_RESEARCH=1`로 배포자가 동의했을 때만 이 자료를 읽는다.
- research가 타깃 가설을 위해 외부 LLM에 입력을 보내는 것도 `RESEARCH_TARGET_LLM_TRANSFER_AUTHORIZED=1`일 때만이다.

## 3. 기사 사진

6단계 기획서와 UI의 trend 카드는 trend 결과에 들어 있는 Vogue Korea 기사 사진 URL을 쓴다.

- 기획서: orchestrator가 실행할 때 초점 트렌드의 사진을 최대 3장 받아 실행 기록에 저장하고 기획서에 넣는다. 허용 호스트는
  `ORCHESTRATOR_TREND_PHOTO_HOSTS`(기본 `vogue.co.kr`과 하위 도메인)이고, `ORCHESTRATOR_TREND_PHOTOS_ENABLED=0`이면 받지 않는다.
- UI: 브라우저가 `img.vogue.co.kr`에서 직접 불러온다(Referer 없이).
- 이 사진들의 권리는 해당 권리자에게 있다. 레포에는 사진 파일이 없고, 사진이 들어간 기획서 PNG를 다시 배포하려면 권리를
  확인해야 한다. stub 프로필의 합성 trend 결과는 사진 URL이 `example.invalid`라 허용 목록에 걸려 받지 않고, 기획서에 사진이 들어가지 않는다.

## 4. 실행 기록

워크플로를 돌리면 `.local/workflow/`(컨테이너는 볼륨)에 서비스 응답 원문, 호출 요청, 기획서 PNG, 받은 기사 사진,
SWOT 초안, MD 결정이 쌓인다. 이 폴더는 git에서 제외되어 있다. 실제 데이터로 돌린 실행 기록을 공유할 때는 위 데이터의
이용 조건을 따른다.

## 5. 라이선스

MIT 라이선스는 이 레포의 코드에만 적용된다. 외부 데이터셋과 그 파생물(그래프, 캐시, 집계 스냅샷, 생성 이미지,
실행 기록)에는 적용되지 않는다.
