"""Export the trend-analysis-agent alias dictionary as a JSON snapshot plus golden expectations.

Writes (in this repository):
  contracts/agents/trend/vocabulary/alias-map.snapshot.json  dictionary data of configs/alias_map/*.yaml (11 attributes +
                                          text_guard) with standard values and provenance
  contracts/agents/trend/vocabulary/alias-map.golden.json    outputs of the ORIGINAL trend code (AliasMap.normalize,
                                          normalize_tokens, normalize_color, split_tokens, norm_key and
                                          TextMatcher text matching) used to verify the port
  src/vogue_orchestrator/query/alias_data.py  regenerated from the snapshot (scripts/generate_alias_data.py)

This is the only step that needs the trend repository, its code and pyyaml. Run it with the trend
virtual environment, from the root of this repository:

  PYTHONDONTWRITEBYTECODE=1 <trend>/.venv/Scripts/python.exe scripts/export_alias_snapshot.py \
      --trend-repo ../trend-analysis-agent [--check]

--check exits 1 when any of the three files would change. The trend repository is only read
(read-only git commands; `--no-optional-locks` so no index refresh is written).
Description of the files: contracts/agents/trend/vocabulary/alias-map.md
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
SNAPSHOT_PATH = ROOT / "contracts" / "agents" / "trend" / "vocabulary" / "alias-map.snapshot.json"
GOLDEN_PATH = ROOT / "contracts" / "agents" / "trend" / "vocabulary" / "alias-map.golden.json"
QUERIES_PATH = ROOT / "data" / "query-structuring-eval" / "dev.json"

sys.path.insert(0, str(SCRIPTS_DIR))
import generate_alias_data as gen  # noqa: E402  (stdlib-only sibling script)

ATTRIBUTES = gen.ATTRIBUTES
EXCLUDED_FILES = ("category_otpensource", "set_components", "set_overrides")
ALIAS_DIR = Path("configs") / "alias_map"
CODE_FILES = (
    "src/trend_agent/ingestion/normalization/alias_map.py",
    "src/trend_agent/ingestion/normalization/text_match.py",
    "src/trend_agent/schemas/vocabulary.py",
)
NOTE = (
    "생성물이다. 직접 고치지 않는다. 원본은 trend-analysis-agent configs/alias_map/*.yaml이고 "
    "이 파일은 읽기 전용 복사본이다. 사전 변경은 trend 저장소에서 D-29 규칙으로만 한다."
)

# ---------------------------------------------------------------- golden inputs
UNKNOWN_WORDS = (
    "겨울", "여름", "고프코어", "오버핏", "루즈핏", "빈티지", "캐주얼", "비즈니스", "무드", "추천",
    "asdf", "xyz-123", "블랙핑크", "니트 소재", "벨벳 소재", "숏패딩", "롱패딩", "후드 티셔츠", "후드티셔츠",
    "티", "옷", "a", "1", "!!", "", "   ", None,
)
MULTI_VALUE_INPUTS: tuple[Any, ...] = (
    "블랙, 화이트", "블랙,화이트", "블랙/화이트", "블랙 + 화이트", "블랙&화이트", "블랙: 화이트",
    "울/폴리", "울 블렌드", "울블렌드", "울 혼방", "면 혼방", "캐시미어 블렌드", "린넨 혼방", "폴리 혼방",
    "실크블렌드", "블렌드", "혼방", "모직 혼방", "기모 블렌드", "울/캐시미어", "울/캐시미어 혼방",
    "시퀸/글리터", "비닐/PVC", "비닐/pvc", "VINYL/PVC", "울/캐시미어/면", "면/폴리/스판덱스",
    "후드+포켓", "후드, 포켓, 지퍼", "체크 & 스트라이프", "체크/스트라이프", "로고: 레터링",
    "슬랙스, 청바지", "자켓/코트", "원피스/스커트", "라운드넥/브이넥", "셔츠칼라, 폴로",
    "오버사이즈/루즈", "슬림/스키니", "A라인/H라인", "롱/미니", "반팔/긴팔", "3/4th-sleeve",
    "벨보텀/플레어", "bell-bottom/flare", "다크 네이비, 차콜 그레이", "2color/3color",
    ["후드", "포켓"], ["블랙", "화이트/그레이"], [["울", "면"], "실크"], [], "", "  , / ", None,
)
COLOR_INPUTS: tuple[Any, ...] = (
    "다크 네이비", "다크네이비", "차콜 그레이", "차콜그레이", "버터옐로우", "버터 옐로우", "블랙, 화이트",
    "2color", "3COLOR", "5 color", "6color", "12", "3.5", ".", "0", 2, 3.5,
    "멜란지", "melange", "MELANGE", "그레이 멜란지", "멜란지 그레이", "heather grey", "heather", "헤더 그레이",
    "라이트 핑크", "light pink", "딥 그린", "deep green", "워시드 블루", "washed blue", "미디엄 블루",
    "pale yellow", "웜 베이지", "웜그레이", "다크", "라이트", "다크 라이트", "로즈골드", "와인레드",
    "sage green", "dusty pink", "husky", "colored", "marigold", "icecream", "라이트블루", "다크블루",
    "다크 블루", "딥 다크 네이비", "오프 화이트", "off-white", "OFF_WHITE", "검정색", "검은", "블랙진",
    "네이비/화이트", "블랙+화이트+레드", "블랙 & 실버", "멀티", "멀티컬러", "투톤", "없음", "none",
    "UNKNOWN", "핑크색", "빨간색", "베이지색", "그레이시", "라벤더 퍼플", "민트그린", "스카이",
    "sky blue", "Sky-Blue", "골드/실버", "",
)
EXTRA_SENTENCES = (
    "겨울 코트",
    "비즈니스 캐주얼",
    "빈티지 브라운 니트",
    "오버핏 후드 티셔츠 보여줘",
    "루즈핏 슬랙스",
    "숏패딩 말고 롱패딩",
    "블랙 말고 네이비 코트",
    "다크 네이비 울 코트",
    "차콜 그레이 슬랙스 추천",
    "버터옐로우 가디건",
    "체크셔츠랑 데님 팬츠",
    "드롭 숄더 니트",
    "라쿤퍼 트리밍 패딩",
    "A-LINE 스커트",
    "STUDDED 데님 재킷",
    "POCKETS 많은 카고 팬츠",
    "PUFFER JACKET",
    "무지개 스트라이프 티셔츠",
    "원피스 추천해줘",
    "모자랑 가방 빼고 상의만",
    "울/캐시미어 코트",
    "시퀸 글리터 드레스",
    "트래블러 플리스 짚업",
    "커스터드 크림 니트",
    "3만원대 반팔 티셔츠",
    "20대 여성 크롭 가디건",
    "브이넥 니트 베스트",
    "슬림핏 청바지",
    "로고 맨투맨",
    "후드 티셔츠, 후드티셔츠, 후드티",
    "",
)


# ---------------------------------------------------------------- trend access
def _git(trend_repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(trend_repo), *args],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return result.stdout.strip()


def _load_trend(trend_repo: Path):
    src = trend_repo / "src"
    if not (src / "trend_agent").is_dir():
        raise SystemExit(f"not a trend-analysis-agent checkout: {trend_repo}")
    sys.path.insert(0, str(src))
    import yaml  # noqa: F401  (fails early when not run with the trend venv)
    from trend_agent.ingestion.normalization import alias_map, otpensource, text_match
    return alias_map, text_match, otpensource


# ---------------------------------------------------------------- snapshot
def build_snapshot(trend_repo: Path, alias_map) -> dict[str, Any]:
    import yaml

    attributes: dict[str, Any] = {}
    files: list[str] = []
    for name in sorted(ATTRIBUTES):
        rel = (ALIAS_DIR / f"{name}.yaml").as_posix()
        cfg = yaml.safe_load((trend_repo / rel).read_text(encoding="utf-8"))
        if cfg.get("attribute") != name:
            raise SystemExit(f"{rel}: attribute field is {cfg.get('attribute')!r}, expected {name!r}")
        files.append(rel)
        section = {key: value for key, value in cfg.items() if key != "attribute"}
        section["standard"] = [member.value for member in alias_map.STANDARD[name]]
        # Section names sorted; entries inside a section keep the YAML order (the original code
        # depends on it: `setdefault` makes the first spelling win).
        attributes[name] = {key: section[key] for key in sorted(section)}
    rel = (ALIAS_DIR / "text_guard.yaml").as_posix()
    guard_cfg = yaml.safe_load((trend_repo / rel).read_text(encoding="utf-8"))
    if guard_cfg.get("attribute") != "text_guard":
        raise SystemExit(f"{rel}: unexpected attribute field")
    files.append(rel)
    text_guard = {key: value for key, value in guard_cfg.items() if key != "attribute"}

    present = {path.stem for path in (trend_repo / ALIAS_DIR).glob("*.yaml")}
    unexpected = present - set(ATTRIBUTES) - {"text_guard"} - set(EXCLUDED_FILES)
    if unexpected:
        raise SystemExit(f"new alias files in trend, decide whether to export them: {sorted(unexpected)}")
    if set(alias_map.STANDARD) != set(ATTRIBUTES):
        raise SystemExit(f"trend AliasMap.STANDARD changed: {sorted(alias_map.STANDARD)}")

    data = {
        "attributes": attributes,
        "code_constants": {"compound_values": list(alias_map.COMPOUND_VALUES)},
        "text_guard": text_guard,
    }
    files += list(CODE_FILES)
    head = _git(trend_repo, "rev-parse", "--short", "HEAD")
    branch = _git(trend_repo, "rev-parse", "--abbrev-ref", "HEAD")
    last = _git(trend_repo, "log", "-1", "--format=%h", "--", *files)
    dirty = bool(_git(trend_repo, "status", "--porcelain", "--", *files))
    provenance = {
        "source_repo": "trend-analysis-agent",
        "source_branch": branch,
        "source_commit": head,
        "source_ref": f"{branch}@{head}",
        "files_last_commit": last,
        "source_dirty": dirty,
        "files": files,
        "excluded_files": [(ALIAS_DIR / f"{name}.yaml").as_posix() for name in EXCLUDED_FILES],
        "content_sha256": gen.content_sha256(data),
        "generated_by": "scripts/export_alias_snapshot.py",
        "note": NOTE,
    }
    return {"_provenance": provenance, **data}


# ---------------------------------------------------------------- golden
def _unique(items: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    out = []
    for item in items:
        marker = json.dumps(item, ensure_ascii=False)
        if marker not in seen:
            seen.add(marker)
            out.append(item)
    return out


def _variants(text: str) -> list[str]:
    """Spelling variants that norm_key should fold: case, spaces, hyphen, underscore."""
    out = [text, text.upper(), text.lower(), f"  {text} "]
    if len(text) >= 2:
        mid = len(text) // 2
        out += [text[:1] + " " + text[1:], text[:mid] + "-" + text[mid:], text[:mid] + "_" + text[mid:],
                " ".join(text), text.title()]
    return _unique(out)


def _section_keys(section: dict[str, Any]) -> list[str]:
    keys: list[str] = list(section["standard"])
    for mapping_name in ("aliases", "approx"):
        for value, raws in (section.get(mapping_name) or {}).items():
            keys.append(value)
            keys += list(raws)
    keys += list(section.get("null_values") or [])
    for mapping_name in ("route", "hint"):
        for key, target in (section.get(mapping_name) or {}).items():
            keys.append(key)
            if mapping_name == "route" and target.get("value"):
                keys.append(target["value"])
    keys += list(section.get("modifiers") or [])
    return _unique(keys)


# A Normalized result is stored as a row in this field order (keeps the golden file compact).
NORMALIZED_FIELDS = ("raw", "value", "approx", "route_to", "hint", "matched_by")


def _as_dict(normalized) -> list[Any]:
    fields = dataclasses.asdict(normalized)
    if tuple(fields) != NORMALIZED_FIELDS:
        raise SystemExit(f"trend Normalized fields changed: {tuple(fields)}")
    return [fields[name] for name in NORMALIZED_FIELDS]


def build_golden(snapshot: dict[str, Any], alias_dir: Path, alias_map, text_match, otpensource) -> dict[str, Any]:
    amap = alias_map.AliasMap(alias_dir)  # the original loader reads the trend YAML, not the snapshot
    attrs = snapshot["attributes"]
    guards = snapshot["text_guard"]["guards"]

    all_keys: list[str] = []
    for name in ATTRIBUTES:
        all_keys += _section_keys(attrs[name])
    for key, words in guards.items():
        all_keys += [key, *words]
    all_keys = _unique(all_keys)

    normalize: dict[str, list[dict[str, Any]]] = {}
    for name in ATTRIBUTES:
        inputs: list[Any] = []
        for key in _section_keys(attrs[name]):
            inputs += _variants(key)
        inputs += all_keys + list(UNKNOWN_WORDS)
        normalize[name] = [[raw, _as_dict(amap.normalize(name, raw))] for raw in _unique(inputs)]
    # attributes AliasMap does not know: everything is "none"
    normalize["season"] = [[raw, _as_dict(amap.normalize("season", raw))] for raw in ("SS", "FW", "블랙")]

    normalize_tokens: dict[str, list[dict[str, Any]]] = {}
    for name in ATTRIBUTES:
        inputs = list(MULTI_VALUE_INPUTS)
        if name == "material":
            inputs += [f"{key} 혼방" for key in _section_keys(attrs[name])]
            inputs += [f"{key}블렌드" for key in _section_keys(attrs[name])]
        normalize_tokens[name] = [
            [raw, [_as_dict(n) for n in amap.normalize_tokens(name, raw)]] for raw in _unique(inputs)
        ]

    color_keys = _section_keys(attrs["color"])
    color_inputs: list[Any] = list(COLOR_INPUTS) + list(MULTI_VALUE_INPUTS)
    for key in color_keys:
        color_inputs += _variants(key)
        color_inputs += [f"다크 {key}", f"light {key}", f"버터{key}", f"{key}멜란지", f"웜 {key} 톤"]
    color_inputs += all_keys + list(UNKNOWN_WORDS)
    normalize_color = [
        [raw, [_as_dict(n) for n in amap.normalize_color(raw)]] for raw in _unique(color_inputs)
    ]

    split_inputs = _unique([*MULTI_VALUE_INPUTS, *COLOR_INPUTS, "시퀸/글리터 & 비닐/PVC", "울/캐시미어:면",
                            "SEQUIN/GLITTER", "울 / 캐시미어", "a/b/c", 0])
    split_tokens = [[raw, amap.split_tokens(raw)] for raw in split_inputs]
    norm_key_inputs = _unique([*all_keys, "  A - B _ C  ", "Ａ Ｂ", "탭\t문자", "줄\n바꿈", "ÀÉ", 12, None, ""])
    norm_key = [[raw, alias_map.norm_key(raw)] for raw in norm_key_inputs]

    return {
        "format": (
            "Each case is [input, expected]. A Normalized result is a row in the order of normalized_fields; "
            "normalize_tokens and normalize_color expect a list of rows."
        ),
        "normalized_fields": list(NORMALIZED_FIELDS),
        "normalize": normalize,
        "normalize_tokens": normalize_tokens,
        "normalize_color": normalize_color,
        "split_tokens": split_tokens,
        "norm_key": norm_key,
        "text_match": build_text_match(amap, text_match, otpensource, guards),
    }


def build_text_match(amap, text_match, otpensource, guards) -> dict[str, Any]:
    """Expected TextMatcher outputs for the query scanner (plan S3). Not used by T1 tests."""
    queries = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))["items"]
    sentences = [{"id": item["id"], "text": item["query"]} for item in queries]
    sentences += [{"id": f"x{index:03d}", "text": text} for index, text in enumerate(EXTRA_SENTENCES, 1)]

    full = {
        name: text_match.TextMatcher(
            [(key, value, kind == "approx") for key, (value, kind) in amap._index[name].items()], guards,
        )
        for name in ATTRIBUTES
    }
    otpen = otpensource.OtpenNormalizer(alias=amap).matchers

    cases = []
    for sentence in sentences:
        text = sentence["text"]
        toks = text_match.tokenize(text)
        norms = [text_match.norm_key(tok) for tok in toks]
        key_hits = {}
        for name in ATTRIBUTES:
            hits = []
            for key, value, approx in full[name].keys:
                raw = full[name].match_key(key, toks, norms)
                if raw is not None:
                    hits.append([key, value, approx, raw])
            key_hits[name] = hits
        cases.append({
            "id": sentence["id"],
            "text": text,
            "tokens": toks,
            "token_keys": norms,
            "find_all_keys": {name: [list(hit) for hit in full[name].find(text)] for name in ATTRIBUTES},
            "key_hits": key_hits,
            "find_otpensource": {name: [list(hit) for hit in matcher.find(text)] for name, matcher in otpen.items()},
        })
    return {
        "about": (
            "find_all_keys: TextMatcher over every index key of the attribute (standard, aliases, approx; "
            "no length filter) with text_guard. key_hits: every key whose match_key is not None, in the "
            "matcher's key order, as [key, value, approx, raw]. find_otpensource: OtpenNormalizer's own "
            "matchers (pattern/detail/shape, keys of 2+ chars, skip keywords)."
        ),
        "matcher_config": {
            "split_pattern": text_match._SPLIT.pattern,
            "hangul_pattern": text_match._HANGUL.pattern,
            "en_suffixes": list(text_match.EN_SUFFIXES),
            "otpensource_attributes": sorted(otpen),
            "otpensource_min_key_length": 2,
            "otpensource_skip_keywords": sorted(otpensource._SKIP_KEYWORDS),
        },
        "cases": cases,
    }


# ---------------------------------------------------------------- output
def _dump_snapshot(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"


def _dump_golden(golden: dict[str, Any], provenance: dict[str, Any]) -> str:
    """Indented structure, one case per line (keeps the file diff-friendly)."""
    def line(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))

    def case_list(cases: list[Any], indent: str) -> str:
        if not cases:
            return "[]"
        return "[\n" + ",\n".join(f"{indent}  {line(case)}" for case in cases) + f"\n{indent}]"

    def by_attribute(mapping: dict[str, list[Any]], indent: str) -> str:
        body = ",\n".join(f"{indent}  {line(name)}: {case_list(cases, indent + '  ')}" for name, cases in mapping.items())
        return "{\n" + body + f"\n{indent}}}"

    tm = golden["text_match"]
    text_match_block = (
        "{\n"
        f"    \"about\": {line(tm['about'])},\n"
        f"    \"matcher_config\": {line(tm['matcher_config'])},\n"
        f"    \"cases\": {case_list(tm['cases'], '    ')}\n"
        "  }"
    )
    parts = [
        f"  \"_provenance\": {line(provenance)}",
        f"  \"format\": {line(golden['format'])}",
        f"  \"normalized_fields\": {line(golden['normalized_fields'])}",
        f"  \"normalize\": {by_attribute(golden['normalize'], '  ')}",
        f"  \"normalize_tokens\": {by_attribute(golden['normalize_tokens'], '  ')}",
        f"  \"normalize_color\": {case_list(golden['normalize_color'], '  ')}",
        f"  \"split_tokens\": {case_list(golden['split_tokens'], '  ')}",
        f"  \"norm_key\": {case_list(golden['norm_key'], '  ')}",
        f"  \"text_match\": {text_match_block}",
    ]
    return "{\n" + ",\n".join(parts) + "\n}\n"


def _current(path: Path) -> str | None:
    # newline=None folds CRLF from a Windows checkout, so --check compares content, not line endings
    return path.read_text(encoding="utf-8") if path.exists() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trend-repo", required=True, type=Path, help="path of the trend-analysis-agent checkout")
    parser.add_argument("--check", action="store_true", help="exit 1 if any output file would change")
    args = parser.parse_args(argv)

    trend_repo = args.trend_repo.resolve()
    alias_map, text_match, otpensource = _load_trend(trend_repo)
    snapshot = build_snapshot(trend_repo, alias_map)
    if snapshot["_provenance"]["source_dirty"]:
        print("warning: the exported trend files have uncommitted changes (source_dirty = true)")

    golden = build_golden(snapshot, trend_repo / ALIAS_DIR, alias_map, text_match, otpensource)
    golden_provenance = {
        "source_repo": snapshot["_provenance"]["source_repo"],
        "source_ref": snapshot["_provenance"]["source_ref"],
        "source_dirty": snapshot["_provenance"]["source_dirty"],
        "snapshot_content_sha256": snapshot["_provenance"]["content_sha256"],
        "generated_by": "scripts/export_alias_snapshot.py (original trend code: AliasMap, TextMatcher)",
        "note": "생성물이다. 직접 고치지 않는다. 기대값은 trend 원본 코드의 실행 결과다.",
    }

    outputs = {
        SNAPSHOT_PATH: _dump_snapshot(snapshot),
        GOLDEN_PATH: _dump_golden(golden, golden_provenance),
        gen.TARGET_PATH: gen.render(json.loads(_dump_snapshot(snapshot))),
    }
    changed = [path for path, text in outputs.items() if _current(path) != text]

    if args.check:
        for path in changed:
            print(f"out of date: {path.relative_to(ROOT).as_posix()}")
        if changed:
            print("run: <trend .venv python> scripts/export_alias_snapshot.py --trend-repo <path>")
            return 1
        print("alias snapshot, golden data and alias_data.py are up to date")
        return 0

    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
