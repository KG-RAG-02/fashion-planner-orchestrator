"""Build data/user_queries.holdout.v2.json: the 20 holdout queries of holdout v1, labels in the per-item format.

The queries (h001-h020) were written by a separate Claude agent on 2026-11-10 and are the same as in
data/user_queries.holdout.v1.json; only the LABELS were converted to draft v3 / the per-item
StructuredQuery (decisions IT-1..IT-7, 2026-11-11), without changing any query text (work rule W-11).

STATUS: this set was already looked at once (the 2026-11-10 prompt v3 run), so it is NOT an unseen
holdout any more (W-10). It is kept as a second regression set. The final check of prompt v4 needs a NEW
holdout written by a separate agent (plan U3.6 item 6).

Usage:  PYTHONPATH=src py scripts/build_query_holdout_v2.py [--check]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "user_queries.holdout.v2.json"
REFERENCE_TODAY = "2026-11-10"

_spec = importlib.util.spec_from_file_location("build_query_dataset_v3", ROOT / "scripts" / "build_query_dataset_v3.py")
_dev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dev)
c, u, it, d, s, EX = _dev.c, _dev.u, _dev.it, _dev.d, _dev.s, _dev.EX

# (id, query, tags, draft, notes)
ITEMS = [
    ("h001", "2027년 봄여름 시즌에 나올 크롭 가디건 트렌드 알려줘", ["season_with_year", "body_length", "minor"],
     d(s(2027, "SS", "2027년 봄여름"), items=[it("가디건", "가디건", [c("크롭", "body_length", "크롭")])]), "'봄여름'은 SS."),
    ("h002", "겨울 시즌 롱패딩 기획해줘, 블랙 컬러는 제외하고", ["season_without_year", "subtype", "body_length", "exclude_color"],
     d(s(None, "FW", "겨울"), items=[it("롱패딩", "패딩", [c("롱", "body_length", "롱"), c("블랙", "color", "블랙", EX)])]),
     "OQ-8′·규칙 8: '롱패딩'은 사전에 없어 패딩(llm). 기장 '롱'은 떼어 쓸 수 있어 raw '롱'(exact). "
     "연도 없는 '겨울'은 코드가 연도를 정한다(사용자 뜻은 올겨울일 수 있음)."),
    ("h003", "남성 봄버재킷 트렌드 카키랑 네이비 위주로 정리해줘", ["gender", "subtype", "color"],
     d(items=[it("봄버재킷", "재킷", [c("남성", "gender", "남"), c("카키", "color", "카키"), c("네이비", "color", "네이비")])]),
     "G-2: '봄버재킷'은 하분류에 없는 하위 종류 → 재킷(llm)."),
    ("h004", "2026 FW 남녀 공용 아우터 쪽 트렌드 한번 훑어줘", ["season_with_year", "gender", "major_only"],
     d(s(2026, "FW", "2026 FW"), items=[it("아우터", "아우터", [c("남녀 공용", "gender", "공용")])]),
     "상분류 아우터는 사용자가 직접 말했으므로 상분류 품목(exact)."),
    ("h005", "가을 시즌 로퍼랑 크로스백 트렌드도 알 수 있을까?", ["season_without_year", "out_of_scope_item"],
     d(s(None, "FW", "가을"), items=[it("로퍼", None), it("크로스백", None)]),
     "IT-7: 찾는 품목이 모두 범위 밖(신발·가방) → items 없음, out_of_scope_items 2개. 연도 없는 '가을'은 코드가 연도를 정한다."),
    ("h006", "이번 주 MD 회의 자료로 쓸 트렌드 키워드 몇 개만 뽑아줘", ["no_condition", "no_item"],
     d(), "조건 없음: 'MD 회의 자료'는 조건이 아니다. 시즌은 default_next, IT-6으로 조건 없는 품목 1개."),
    ("h007", "유니클로 말고 7만원 아래 플리스 조끼 위주로 찾아줘", ["unsupported_brand", "exclude", "unsupported_price", "material", "alias"],
     d(items=[it("조끼", "베스트", [c("플리스", "material", "플리스")], [u("brand", "유니클로", EX), u("price", "7만원 아래")])]),
     "조끼 → 베스트(사전 별칭). 브랜드 제외는 unsupported polarity exclude."),
    ("h008", "일본 시장 겨냥해서 와이드 청바지 기획해봐", ["unsupported_market", "fit", "minor"],
     d(items=[it("청바지", "청바지", [c("와이드", "fit", "와이드")], [u("market", "일본 시장")])]), ""),
    ("h009", "홈쇼핑에서 팔 캐시미어 코트 트렌드 봐줄래", ["unsupported_channel", "material", "minor", "alias"],
     d(items=[it("코트", "코트", [c("캐시미어", "material", "울/캐시미어")], [u("channel", "홈쇼핑")])]),
     "캐시미어 → 울/캐시미어(사전 별칭)."),
    ("h010", "40대 여성 고객 대상으로 실크 블라우스 기획안 부탁해", ["unsupported_age", "gender", "material", "minor"],
     d(items=[it("블라우스", "블라우스", [c("여성", "gender", "여"), c("실크", "material", "실크")], [u("age", "40대")])]), ""),
    ("h011", "고급스러운 느낌 나는 아이보리 스커트 보여줘", ["abstract", "color", "alias", "minor"],
     d(items=[it("스커트", "스커트", [c("아이보리", "color", "화이트")], [u("abstract", "고급스러운 느낌")])]),
     "아이보리 → 화이트(사전 별칭). 추상 표현 raw는 '고급스러운 느낌'."),
    ("h012", "폴리 소재는 빼고 터틀넥 니트웨어로 추려줘", ["exclude_material", "detail", "minor", "alias"],
     d(items=[it("니트웨어", "니트웨어", [c("폴리 소재", "material", "합성섬유", EX), c("터틀넥", "detail", "터틀넥")])]),
     "'폴리 소재' → 표현 안 단어 '폴리'로 사전 조회 → 합성섬유(alias)."),
    ("h013", "2027 여름 민소매 맥시 원피스 트렌드 정리 부탁해", ["season_with_year", "sleeve_length", "body_length", "onepiece"],
     d(s(2027, "SS", "2027 여름"), items=[it("원피스", "드레스", [c("민소매", "sleeve_length", "민소매"), c("맥시", "body_length", "맥시")])]),
     "OQ-5′: 원피스 = 하분류 드레스. OQ-9′: 소매·몸판 기장은 조건."),
    ("h014", "여성 라인은 빼고 코듀로이 팬츠 기획해줘", ["gender_exclude", "material", "minor"],
     d(items=[it("팬츠", "팬츠", [c("여성", "gender", "여", EX), c("코듀로이", "material", "코듀로이")])]),
     "G-3: 성별 제외 [여] = 남·공용. 바꿔 쓰지 않는다. raw는 '여성'('라인'은 성별 표현이 아님)."),
    ("h015", "긴팔 스트라이프 티셔츠 볼 건데 레드 계열은 빼줘", ["sleeve_length", "pattern", "exclude_color", "minor"],
     d(items=[it("티셔츠", "티셔츠", [c("긴팔", "sleeve_length", "긴팔"), c("스트라이프", "pattern", "스트라이프"),
                                     c("레드", "color", "레드", EX)])]),
     "'레드 계열'의 raw는 '레드'(dev q003과 같은 관례)."),
    ("h016", "남녀 같이 입을 수 있는 버건디 후드 집업 기획해줘", ["gender", "color", "alias", "detail", "minor"],
     d(items=[it("집업", "짚업", [c("남녀", "gender", "공용"), c("버건디", "color", "와인"), c("후드", "detail", "후드")])]),
     "'남녀' = 공용(G-3). 버건디 → 와인(alias), 집업 → 짚업(alias). '후드 집업'의 '후드'는 품목 앞 꾸밈말이라 detail 후드로 따로 씀."),
    ("h017", "올드머니 무드로 2027 SS 화이트 슬랙스 기획해줘", ["style_name", "season_with_year", "color", "alias", "minor"],
     d(s(2027, "SS", "2027 SS"), ["올드머니"], [it("슬랙스", "팬츠", [c("화이트", "color", "화이트")])]),
     "트렌드 이름 raw는 '올드머니'('무드' 제외, dev q001 '고프코어'와 같은 관례)."),
    ("h018", "MA-1 점퍼 숏 기장으로 나온 것들 트렌드 좀 보여줘", ["subtype", "body_length", "english_term"],
     d(items=[it("MA-1 점퍼", "점퍼", [c("숏", "body_length", "크롭")])]),
     "'MA-1 점퍼'는 점퍼의 하위 종류라 품목 점퍼(표현 안 단어 '점퍼' exact), product_model_names에 넣지 않음(OQ-8′). "
     "'숏' 기장은 사전에 값이 없어 LLM 제안 크롭(llm)."),
    ("h019", "레오파드 패턴 없는 셔츠랑 스커트 같이 보고 싶어", ["exclude_pattern", "multi_item", "minor"],
     d(items=[it("셔츠", "셔츠", [c("레오파드", "pattern", "호피", EX)]), it("스커트", "스커트")]),
     "IT-2: 레오파드 제외는 바로 뒤 품목(셔츠)에만. 레오파드는 사전에 없어 LLM 제안 호피(llm). '없는' = 제외."),
    ("h020", "내년 봄 시즌 점프수트는 빼고 원피스 위주로 보여줘", ["season_without_year", "exclude", "onepiece"],
     d(s(None, "SS", "내년 봄"), items=[it("원피스", "드레스", [c("점프수트", "category", "점프수트", EX)])]),
     "OQ-5′: '원피스' = 하분류 드레스라 결과는 드레스 품목 + 점프수트 제외. '내년 봄'은 연도를 계산하지 않고 null."),
]


def build() -> dict:
    return {
        "_about": "홀드아웃 질의 20개(합성, data/user_queries.holdout.v1.json과 같은 질의, 별도 Claude Agent 작성). 라벨만 품목별 "
                  "형식(draft v3)으로 바꿨다. 2026-11-10 프롬프트 v3 측정에 한 번 썼으므로 더 이상 '안 본 세트'가 아니며 "
                  "회귀 확인용으로만 쓴다(최종 판단은 새 holdout). label_status=silver. 생성: scripts/build_query_holdout_v2.py",
        "dataset": "user_queries_holdout",
        "version": "v2",
        "created": "2026-11-11",
        "prompt_version": "query-structuring-prompt/v4",
        "reference_today": REFERENCE_TODAY,
        "items": _dev.build_items(ITEMS, lambda row: (row[0], row[1], row[2], "holdout", row[3], row[4]), REFERENCE_TODAY),
    }


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the data file is out of date")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else None
        if current != text:
            print(f"{TARGET.relative_to(ROOT)} is out of date; run scripts/build_query_holdout_v2.py")
            return 1
        print(f"{TARGET.relative_to(ROOT)} is up to date")
        return 0
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(ITEMS)} items)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
