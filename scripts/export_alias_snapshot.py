"""Export the fashion-planner-trend alias dictionary as a JSON snapshot plus golden expectations.

Writes (in this repository):
  contracts/agents/trend/vocabulary/alias-map.snapshot.json  dictionary data of configs/alias_map/*.yaml (11 attributes +
                                          text_guard) with standard values and provenance
  contracts/agents/trend/vocabulary/alias-map.golden.json    a deterministic sample of outputs of the ORIGINAL trend
                                          code (AliasMap.normalize, normalize_tokens, normalize_color,
                                          split_tokens, norm_key) used to verify the port
  src/fashion_planner_orchestrator/query/alias_data.py  regenerated from the snapshot (scripts/generate_alias_data.py)

This is the only step that needs the trend repository, its code and pyyaml. Run it with a Python
environment that has the trend dependencies, from the root of this repository:

  PYTHONDONTWRITEBYTECODE=1 <trend python> scripts/export_alias_snapshot.py \
      --trend-repo ../fashion-planner-trend [--check]

The exported trend files must be committed (a dirty checkout is refused), and the provenance names
the repository and the content hash only, not a branch or commit. --check exits 1 when any of the
three files would change. The trend repository is only read (`--no-optional-locks` git commands).
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

sys.path.insert(0, str(SCRIPTS_DIR))
import generate_alias_data as gen  # noqa: E402  (stdlib-only sibling script)

ATTRIBUTES = gen.ATTRIBUTES
EXCLUDED_FILES = ("category_otpensource", "set_components", "set_overrides")
ALIAS_DIR = Path("configs") / "alias_map"
CODE_FILES = (
    "src/fashion_planner_trend/ingestion/normalization/alias_map.py",
    "src/fashion_planner_trend/ingestion/normalization/text_match.py",
    "src/fashion_planner_trend/schemas/vocabulary.py",
)
SOURCE_REPO = "fashion-planner-trend"
NOTE = (
    "생성물이다. 직접 고치지 않는다. 원본은 fashion-planner-trend의 configs/alias_map/*.yaml이고 "
    "이 파일은 읽기 전용 복사본이다. 사전은 trend 레포에서만 바꾼다."
)
# The golden file keeps a deterministic sample of every case list (the full lists are about 1 MB): the hand
# written inputs, the first case of every distinct outcome, and every STEP-th of the rest.
SAMPLE_STEP = {"normalize": 40, "normalize_tokens": 4, "normalize_color": 20, "split_tokens": 1, "norm_key": 4}

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

# ---------------------------------------------------------------- trend access
def _git(trend_repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(trend_repo), *args],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return result.stdout.strip()


def _load_trend(trend_repo: Path):
    src = trend_repo / "src"
    if not (src / "fashion_planner_trend").is_dir():
        raise SystemExit(f"not a fashion-planner-trend checkout: {trend_repo}")
    sys.path.insert(0, str(src))
    import yaml  # noqa: F401  (fails early without the trend dependencies)
    from fashion_planner_trend.ingestion.normalization import alias_map
    return alias_map


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
    if _git(trend_repo, "status", "--porcelain", "--", *files):
        raise SystemExit("the exported trend files have uncommitted changes; commit them first")
    provenance = {
        "source_repo": SOURCE_REPO,
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


def build_golden(snapshot: dict[str, Any], alias_dir: Path, alias_map) -> dict[str, Any]:
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
    }


def _signature(expected: Any) -> str:
    """The kind of outcome a case shows: for a Normalized row its matched_by and which of approx, route_to and
    hint are set; for a list of rows the same per row; otherwise the shape of the value."""
    def row(value: Any) -> Any:
        if isinstance(value, list) and len(value) == len(NORMALIZED_FIELDS):
            fields = dict(zip(NORMALIZED_FIELDS, value))
            return [fields["matched_by"], bool(fields["approx"]), fields["route_to"] is not None, bool(fields["hint"])]
        return None
    if row(expected) is not None:
        return json.dumps(row(expected))
    if isinstance(expected, list) and all(row(item) is not None for item in expected):
        return json.dumps([row(item) for item in expected])
    return json.dumps([type(expected).__name__, len(expected) if isinstance(expected, (list, str)) else None])


def sample_golden(golden: dict[str, Any]) -> dict[str, Any]:
    """A deterministic subset of every case list: hand-written inputs, the first case of each outcome kind,
    and every SAMPLE_STEP-th case of the rest, in the original order."""
    def marks(*groups: Iterable[Any]) -> set[str]:
        return {json.dumps(raw, ensure_ascii=False) for group in groups for raw in group}

    # hand-written inputs kept in full, per list (normalize_tokens repeats the same inputs for every attribute,
    # so there the step and the outcome kinds decide)
    special = {"normalize": marks(UNKNOWN_WORDS), "normalize_tokens": set(),
               "normalize_color": marks(COLOR_INPUTS, UNKNOWN_WORDS), "split_tokens": set(), "norm_key": set()}

    def sample(name: str, cases: list[Any]) -> list[Any]:
        seen: set[str] = set()
        out = []
        for index, (raw, expected) in enumerate(cases):
            kind = _signature(expected)
            if index % SAMPLE_STEP[name] == 0 or kind not in seen or json.dumps(raw, ensure_ascii=False) in special[name]:
                out.append([raw, expected])
            seen.add(kind)
        return out

    out = dict(golden)
    for name in ("normalize", "normalize_tokens"):
        out[name] = {attr: sample(name, cases) for attr, cases in golden[name].items()}
    for name in ("normalize_color", "split_tokens", "norm_key"):
        out[name] = sample(name, golden[name])
    return out


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

    parts = [
        f"  \"_provenance\": {line(provenance)}",
        f"  \"format\": {line(golden['format'])}",
        f"  \"normalized_fields\": {line(golden['normalized_fields'])}",
        f"  \"normalize\": {by_attribute(golden['normalize'], '  ')}",
        f"  \"normalize_tokens\": {by_attribute(golden['normalize_tokens'], '  ')}",
        f"  \"normalize_color\": {case_list(golden['normalize_color'], '  ')}",
        f"  \"split_tokens\": {case_list(golden['split_tokens'], '  ')}",
        f"  \"norm_key\": {case_list(golden['norm_key'], '  ')}",
    ]
    return "{\n" + ",\n".join(parts) + "\n}\n"


def _current(path: Path) -> str | None:
    # newline=None folds CRLF from a Windows checkout, so --check compares content, not line endings
    return path.read_text(encoding="utf-8") if path.exists() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trend-repo", required=True, type=Path, help="path of the fashion-planner-trend checkout")
    parser.add_argument("--check", action="store_true", help="exit 1 if any output file would change")
    args = parser.parse_args(argv)

    trend_repo = args.trend_repo.resolve()
    alias_map = _load_trend(trend_repo)
    snapshot = build_snapshot(trend_repo, alias_map)

    golden = sample_golden(build_golden(snapshot, trend_repo / ALIAS_DIR, alias_map))
    golden_provenance = {
        "source_repo": snapshot["_provenance"]["source_repo"],
        "snapshot_content_sha256": snapshot["_provenance"]["content_sha256"],
        "generated_by": "scripts/export_alias_snapshot.py (original trend code: AliasMap)",
        "sampling": "hand-written inputs, the first case of each outcome kind, and every n-th of the rest "
                    f"(n per list: {SAMPLE_STEP})",
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
            print("run: <trend python> scripts/export_alias_snapshot.py --trend-repo <path>")
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
