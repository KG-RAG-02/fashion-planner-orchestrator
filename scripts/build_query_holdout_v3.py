"""Build data/user_queries.holdout.v3.json: 20 NEW holdout queries (h201-h220) for the final check of prompt v4.

The queries and labels were written on 2026-11-11 by a separate Claude agent that did NOT read the v4
prompt (no file under prompts/, no rendered prompt, no prompt examples) and did not look at data/eval_runs/.
The agent wrote the labels only from the label rules: the draft v3 format (query/draft_v3.py), the contract
(contracts/structured-query.md §2-§6) and the decisions (docs/query-decisions.md §2). So this set measures
how prompt v4 generalises to queries it was not tuned on (work rule W-10 ④). The queries are new, not
paraphrases of data/user_queries.v1-v3.json or the holdout v1/v2 sets. Labels are silver (not yet checked
by the user).

Usage:  PYTHONPATH=src py scripts/build_query_holdout_v3.py [--check]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "user_queries.holdout.v3.json"
REFERENCE_TODAY = "2026-11-10"

_spec = importlib.util.spec_from_file_location("build_query_dataset_v3", ROOT / "scripts" / "build_query_dataset_v3.py")
_dev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dev)
c, u, it, d, s, EX = _dev.c, _dev.u, _dev.it, _dev.d, _dev.s, _dev.EX

# (id, query, tags, draft, notes)
ITEMS = [
    ("h201", "봄 시즌이랑 가을 시즌에 같이 가져갈 베이지 트렌치코트 기획해줘",
     ["several_seasons", "season_without_year", "subtype", "color"],
     d(seasons=[s(None, "SS", "봄"), s(None, "FW", "가을")],
       items=[it("트렌치코트", "코트", [c("베이지", "color", "베이지")])]),
     "SQ-2: 시즌 두 개, 둘 다 연도 없음 → 코드가 연도를 정한다. G-2: '트렌치코트'는 하분류에 없는 하위 종류 → 코트(llm)."),
    ("h202", "꽈배기 니트 조끼랑 스트라이프 셔츠, 둘 다 그레이 컬러로 보여줘",
     ["multi_item", "common_condition", "detail", "material", "pattern", "color", "alias"],
     d(items=[it("조끼", "베스트", [c("꽈배기", "detail", "니트꽈배기"), c("니트", "material", "니트")]),
              it("셔츠", "셔츠", [c("스트라이프", "pattern", "스트라이프")])],
       common=[c("그레이", "color", "그레이")]),
     "IT-2: '꽈배기 니트'는 조끼에만, '스트라이프'는 셔츠에만. '둘 다 그레이' → common(IT-1, 코드가 두 품목에 복사). "
     "조끼 → 베스트, 꽈배기 → 니트꽈배기(사전 별칭)."),
    ("h203", "올겨울 레더 재킷이랑 내년 여름 린넨 팬츠 트렌드 비교해줘",
     ["multi_item", "several_seasons", "season_without_year", "material", "alias"],
     d(seasons=[s(None, "FW", "올겨울"), s(None, "SS", "내년 여름")],
       items=[it("재킷", "재킷", [c("레더", "material", "가죽")]), it("팬츠", "팬츠", [c("린넨", "material", "린넨")])]),
     "IT-5″/SQ-2: 시즌은 질의 전체 목록 [FW, SS]이고 품목과 짝짓지 않는다(한계). 연도는 숫자로 쓰지 않았으므로 null. "
     "코드(A-1)는 '올겨울'을 2027 FW로 정한다(사용자 뜻은 2026 FW일 수 있음, dev q002 '올여름'과 같은 한계). "
     "레더 → 가죽(사전 별칭)."),
    ("h204", "내년 봄에 데님 소재로 뭐가 뜰지 정리해줘",
     ["no_item", "season_without_year", "material"],
     d(s(None, "SS", "내년 봄"), common=[c("데님 소재", "material", "데님")]),
     "IT-6: 찾는 품목을 말하지 않음 → items [] + 조건은 common → 카테고리 없는 품목 1개(모든 카테고리 중 데님)."),
    ("h205", "블랙 첼시부츠랑 롱 스커트 같이 기획해줘",
     ["out_of_scope_item", "multi_item", "color", "body_length"],
     d(items=[it("첼시부츠", None, [c("블랙", "color", "블랙")]), it("스커트", "스커트", [c("롱", "body_length", "롱")])]),
     "IT-7: 첼시부츠는 범위 밖 → out_of_scope_items(conditions_raw [블랙]). 블랙은 부츠의 꾸밈말이라 스커트에 붙지 않는다(IT-2)."),
    ("h206", "남성 상품은 제외하고, 아우터 중에서 패딩만 빼고 보여줘",
     ["gender_exclude", "exclude", "major_only"],
     d(items=[it("아우터", "아우터", [c("남성", "gender", "남", EX), c("패딩", "category", "패딩", EX)])]),
     "품목 하나 → 조건은 모두 그 품목에. 상분류 아우터(exact) − 하분류 패딩(X3, 상분류는 남음). G-3: 성별 제외 [남] = 여·공용."),
    ("h207", "아르켓 스타일로 단정한 느낌의 울 코트, 가격은 30만원 이하로 잡아줘",
     ["unsupported_brand", "abstract", "unsupported_price", "material", "alias", "minor"],
     d(items=[it("코트", "코트", [c("울", "material", "울/캐시미어")],
                 [u("brand", "아르켓"), u("abstract", "단정한 느낌"), u("price", "30만원 이하")])]),
     "'아르켓 스타일'은 트렌드명이 아니라 브랜드(raw '아르켓'). 울 → 울/캐시미어(사전 별칭)."),
    ("h208", "자사몰 단독으로 낼 셔링 롱 원피스, 메인 타깃은 30대야",
     ["unsupported_channel", "unsupported_age", "detail", "body_length", "onepiece"],
     d(items=[it("원피스", "드레스", [c("셔링", "detail", "셔링"), c("롱", "body_length", "롱")],
                 [u("channel", "자사몰"), u("age", "30대")])]),
     "OQ-5′: 원피스 = 하분류 드레스. 채널·연령은 unsupported."),
    ("h209", "아크릴 혼방 말고 울 함량 높은 가디건으로 기획해줘, 컬러는 그레이나 네이비",
     ["exclude_material", "material", "color", "minor"],
     d(items=[it("가디건", "가디건", [c("아크릴 혼방", "material", "합성섬유", EX), c("울", "material", "울/캐시미어"),
                                     c("그레이", "color", "그레이"), c("네이비", "color", "네이비")])]),
     "'아크릴 혼방'은 사전에 없어 LLM 제안 합성섬유(llm). '울 함량 높은'의 raw는 '울'."),
    ("h210", "도트 무늬 들어간 건 빼고 7부 소매 티셔츠 트렌드 봐줘",
     ["exclude_pattern", "sleeve_length", "minor"],
     d(items=[it("티셔츠", "티셔츠", [c("도트", "pattern", "도트", EX), c("7부 소매", "sleeve_length", "7부소매")])]),
     "OQ-9′: '7부 소매'는 소매 기장 7부소매(사전 exact)."),
    ("h211", "남성 와이드 청바지랑 여성 크롭 가디건 같이 기획해줘",
     ["multi_item", "gender", "fit", "body_length"],
     d(items=[it("청바지", "청바지", [c("남성", "gender", "남"), c("와이드", "fit", "와이드")]),
              it("가디건", "가디건", [c("여성", "gender", "여"), c("크롭", "body_length", "크롭")])]),
     "IT-2·IT-3: 성별도 품목별. 청바지 = 남·와이드, 가디건 = 여·크롭. '같이'는 두 품목을 모두 원한다는 뜻(꾸밈말 아님)."),
    ("h212", "후드티랑 조거팬츠 세트로 볼 건데 둘 다 블랙은 빼줘",
     ["multi_item", "common_condition", "exclude_color"],
     d(items=[it("후드티", "후드티"), it("조거팬츠", "조거팬츠")], common=[c("블랙", "color", "블랙", EX)]),
     "'둘 다 블랙은 빼줘' → common 색 제외(코드가 두 품목에 복사). '세트로'는 조건이 아니다."),
    ("h213", "맨투맨이랑 트랙 팬츠, 오버사이즈 핏으로 2027년 SS 기획해줘",
     ["multi_item", "common_condition", "fit", "season_with_year", "alias", "subtype"],
     d(s(2027, "SS", "2027년 SS"), items=[it("맨투맨", "티셔츠"), it("트랙 팬츠", "팬츠")],
       common=[c("오버사이즈 핏", "fit", "오버사이즈")]),
     "IT-2: 품목 나열 뒤의 '~으로'는 전체를 꾸민다 → common. 맨투맨 → 티셔츠(사전 별칭). "
     "'트랙 팬츠'는 하위 종류라 품목 raw 전체로 두고(h018 'MA-1 점퍼'와 같은 관례) 표현 안 단어 '팬츠'로 팬츠(exact)."),
    ("h214", "코티지코어 감성의 퍼프 소매 블라우스 트렌드 정리해줘",
     ["style_name", "detail", "minor"],
     d(style=["코티지코어"], items=[it("블라우스", "블라우스", [c("퍼프 소매", "detail", "퍼프")])]),
     "트렌드명 raw는 '코티지코어'('감성' 제외). '퍼프 소매'는 디테일 퍼프(소매 기장 값이 아님)."),
    ("h215", "여름 상의 기획인데 브라탑이랑 민소매 탑은 제외해줘",
     ["season_without_year", "major_only", "exclude", "sleeve_length"],
     d(s(None, "SS", "여름"), items=[it("상의", "상의", [c("브라탑", "category", "브라탑", EX),
                                                        c("민소매", "sleeve_length", "민소매", EX),
                                                        c("탑", "category", "탑", EX)])]),
     "상분류 상의(exact) − 브라탑 − 탑. '민소매 탑' 제외 = 소매 기장 민소매 제외 + 탑 제외(dev q004 '크롭 티셔츠 말고'와 같은 한계: "
     "'민소매인 탑만 제외'라는 묶음은 표현하지 못하고 상의 전체에서 민소매가 빠진다)."),
    ("h216", "동남아 시장 겨냥해서 5만원 이하 린넨 반팔 셔츠 기획해줘",
     ["unsupported_market", "unsupported_price", "material", "sleeve_length", "minor"],
     d(items=[it("셔츠", "셔츠", [c("린넨", "material", "린넨"), c("반팔", "sleeve_length", "반팔")],
                 [u("market", "동남아 시장"), u("price", "5만원 이하")])]), ""),
    ("h217", "우먼즈는 제외하고 코치 재킷 트렌드 정리해줘",
     ["gender_exclude", "subtype"],
     d(items=[it("코치 재킷", "재킷", [c("우먼즈", "gender", "여", EX)])]),
     "G-3: 성별 제외 [여] = 남·공용. '우먼즈'는 성별 표에 없어 LLM 제안 여(llm). "
     "품목 하나라 '~는 제외하고' 절도 그 품목에. '코치 재킷'은 하위 종류 → 표현 안 단어 '재킷'(exact)."),
    ("h218", "맥시스커트랑 크롭자켓 내년 SS 기준으로 정리 부탁해",
     ["multi_item", "subtype", "body_length", "season_without_year"],
     d(s(None, "SS", "내년 SS"), items=[it("맥시스커트", "스커트", [c("맥시", "body_length", "맥시")]),
                                       it("크롭자켓", "재킷", [c("크롭", "body_length", "크롭")])]),
     "OQ-9′: 품목 이름 안의 기장은 기장 조건으로 따로(맥시 → 스커트에만, 크롭 → 재킷에만). 두 품목 모두 사전에 없어 llm. "
     "'내년'은 연도를 계산하지 않고 null(코드가 정함)."),
    ("h219", "스웨이드 소재 빼고 카멜 컬러 블루종 찾아줘",
     ["exclude_material", "color", "alias", "subtype"],
     d(items=[it("블루종", "점퍼", [c("스웨이드 소재", "material", "스웨이드", EX), c("카멜", "color", "브라운")])]),
     "G-2: 블루종 → 점퍼(llm). 카멜 → 브라운(사전 별칭)."),
    ("h220", "2026 가을겨울 슬림핏 터틀넥 니트 블랙 계열로 보여줘",
     ["season_with_year", "fit", "detail", "color", "approx", "minor"],
     d(s(2026, "FW", "2026 가을겨울"), items=[it("니트", "니트웨어", [c("슬림핏", "fit", "타이트"), c("터틀넥", "detail", "터틀넥"),
                                                                    c("블랙", "color", "블랙")])]),
     "품목 '니트' = 니트웨어(카테고리 사전에는 없어 llm). '슬림핏' → 타이트(사전 근사 approx). '블랙 계열'의 raw는 '블랙'."),
]


def build() -> dict:
    return {
        "_about": "홀드아웃 질의 20개(합성, 새 질의 h201~h220). 2026-11-11 프롬프트 v4를 읽지 않은 별도 Claude Agent가 "
                  "질의와 라벨을 작성했다(라벨 근거: draft_v3.py 형식, contracts/structured-query.md, docs/query-decisions.md). "
                  "프롬프트 v4의 최종 확인용(튜닝에 쓰지 않은 세트). label_status=silver: 사용자 확인 전. "
                  "생성: scripts/build_query_holdout_v3.py",
        "dataset": "user_queries_holdout",
        "version": "v3",
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
            print(f"{TARGET.relative_to(ROOT)} is out of date; run scripts/build_query_holdout_v3.py")
            return 1
        print(f"{TARGET.relative_to(ROOT)} is up to date")
        return 0
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(ITEMS)} items)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
