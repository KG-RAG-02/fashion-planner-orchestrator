"""Snapshot each agent's input/output contract into contracts/agents/<agent-id>/ from the sibling checkouts.

The orchestrator maps every agent's input and output, so it keeps a code-based copy of each agent's
contract here. The owning repository stays the source of truth; these files are copies with
provenance (repo, branch, commit, source-file sha256) so a later change in the owner can be detected.

Methods
  copy      wrap a JSON Schema file that the agent repository already publishes
  pydantic  run the agent's own Python (its .venv) and call `model_json_schema()` on its model
  pinned    an existing snapshot that generated code depends on (trend-query: query/vocabulary.py is
            generated from it). Checked only; refresh it through its own workflow
  authored  written in this repository by reading the agent's code, because the agent publishes no
            JSON Schema (new-product-agent). Never rewritten; the check reports when its sources change

Also reads each agent's HTTP handoff `contract_version` constant from its source and compares it with the
value core.py expects for that agent (`CONTRACT_VERSION`, or `PROJECT06_CONTRACT_VERSION` for
new-product-agent; ORCHESTRATOR_VERSION_CONSTANT). A mismatch means the HTTP call to that agent fails.

Usage
  py scripts/export_agent_contracts.py            # write copy/pydantic snapshots (pinned/authored untouched)
  py scripts/export_agent_contracts.py --check    # exit 1 if a snapshot is out of date with its source

Sibling roots default to ../<repo-name>; override with VOGUE_<NAME>_ROOT (TREND, STOCK, SALES_MARKET,
NEW_PRODUCT). The trend Python defaults to <trend root>/.venv; override with VOGUE_TREND_PYTHON.
Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "agents"
GENERATED_BY = "scripts/export_agent_contracts.py"
NOTE = "생성물이다. 직접 고치지 않는다. 기준은 소유 레포이고 이 파일은 복사본이다."
AUTHORED_NOTE = ("소유 레포에 JSON Schema가 없어 코드를 읽고 이 저장소에서 작성했다. 스크립트가 다시 쓰지 않는다. "
                 "source_files의 sha256이 달라지면 코드를 다시 읽고 손으로 갱신한다.")

REPOS = {
    "trend-analysis-agent": ("VOGUE_TREND_ROOT", "trend-analysis-agent"),
    "stock-agent": ("VOGUE_STOCK_ROOT", "stock-agent"),
    "sales-market-agent": ("VOGUE_SALES_MARKET_ROOT", "sales-market-agent"),
    "new-product-agent": ("VOGUE_NEW_PRODUCT_ROOT", "new-product-agent"),
}


@dataclass(frozen=True)
class Envelope:
    route: str
    source_file: str
    constant: str  # name of the contract_version constant in source_file


@dataclass(frozen=True)
class Entry:
    agent_id: str
    direction: str  # input | output
    target: str  # file name under contracts/agents/<agent_id>/<direction>/
    contract: str
    payload_version: str | None
    method: str  # copy | pydantic | pinned | authored
    source_files: tuple[str, ...]
    source: str | None = None  # copy: schema path; pydantic: "module:Model"
    extra: dict[str, Any] = field(default_factory=dict)


ENVELOPES = {
    "trend-analysis-agent": Envelope("/v1/agents/trend-analysis-agent/runs", "src/trend_agent/api/server.py",
                                     "CONTRACT_VERSION"),
    "stock-agent": Envelope("/v1/agents/stock-agent/runs", "stock_agent/api.py", "HANDOFF_CONTRACT_VERSION"),
    "sales-market-agent": Envelope("/v1/agents/sales-market-agent/runs", "src/market_research_agent/http_server.py",
                                   "CONTRACT_VERSION"),
    "new-product-agent": Envelope("/v1/agents/new-product-agent/runs", "src/vogue_npp/adapters/handoff.py",
                                  "CONTRACT_VERSION"),
}

ENTRIES = (
    Entry("trend-analysis-agent", "input", "trend-query.contract.json", "TrendQuery", None, "pinned",
          ("src/trend_agent/schemas/trend_query.py", "src/trend_agent/schemas/vocabulary.py"),
          "trend_agent.schemas.trend_query:TrendQuery"),
    Entry("trend-analysis-agent", "output", "trend-analysis-result-1.1.contract.json", "TrendAnalysisResult", "1.1",
          "pydantic",
          ("src/trend_agent/schemas/trend_analysis_result.py", "src/trend_agent/schemas/trend_query.py",
           "src/trend_agent/schemas/vocabulary.py"),
          "trend_agent.schemas.trend_analysis_result:TrendAnalysisResult"),
    Entry("stock-agent", "input", "stock-agent-request-1.0.contract.json",
          "stock-agent input (TrendAnalysisResult as stock-agent reads it)", "1.0", "copy",
          ("contracts/schemas/stock-agent-request.schema.json", "stock_agent/trend_request.py"),
          "contracts/schemas/stock-agent-request.schema.json"),
    Entry("stock-agent", "output", "stock-agent-result-1.0.contract.json", "stock-agent result", "1.0", "copy",
          ("contracts/schemas/stock-agent-result.schema.json",),
          "contracts/schemas/stock-agent-result.schema.json"),
    Entry("sales-market-agent", "input", "sales-market-run-request-0.1.contract.json",
          "sales-market-agent /runs request (envelope + input)", None, "copy",
          ("contracts/schemas/vogue-handoff-0.1-request.schema.json", "src/market_research_agent/http_server.py"),
          "contracts/schemas/vogue-handoff-0.1-request.schema.json"),
    Entry("sales-market-agent", "output", "market-research-result-1.0.contract.json", "MarketResearchResult", "1.0",
          "copy", ("contracts/schemas/market-research-result.schema.json",),
          "contracts/schemas/market-research-result.schema.json"),
    Entry("sales-market-agent", "output", "sales-market-run-response-0.1.contract.json",
          "sales-market-agent /runs response envelope", None, "copy",
          ("contracts/schemas/vogue-handoff-0.1-response.schema.json",),
          "contracts/schemas/vogue-handoff-0.1-response.schema.json"),
    Entry("sales-market-agent", "output", "sales-market-run-error-0.1.contract.json",
          "sales-market-agent /runs error body", None, "copy",
          ("contracts/schemas/vogue-handoff-0.1-error.schema.json",),
          "contracts/schemas/vogue-handoff-0.1-error.schema.json"),
    Entry("new-product-agent", "input", "planning-draft-input-0.2.contract.json",
          "new-product-agent /runs input (four producer payloads)", None, "authored",
          ("src/vogue_npp/adapters/handoff.py", "src/vogue_npp/prototype/server.py")),
    Entry("new-product-agent", "output", "planning-draft-result-1.0.contract.json",
          "new-product-agent planning draft result", "1.0", "authored",
          ("src/vogue_npp/prototype/server.py",)),
)


def repo_root(agent_id: str) -> Path:
    env, name = REPOS[agent_id]
    return Path(os.environ.get(env, ROOT.parent / name)).resolve()


def sha256_normalized(path: Path) -> str:
    """sha256 of the file with CRLF folded to LF, so Windows and Unix checkouts agree."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def trend_python(root: Path) -> str:
    override = os.environ.get("VOGUE_TREND_PYTHON")
    if override:
        return override
    for candidate in (root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"trend Python not found under {root / '.venv'}; set VOGUE_TREND_PYTHON")


def pydantic_schema(agent_root: Path, spec: str) -> dict[str, Any]:
    module, _, name = spec.partition(":")
    code = (f"import json,sys; from {module} import {name} as M; "
            "sys.stdout.write(json.dumps(M.model_json_schema(), ensure_ascii=False))")
    env = {**os.environ, "PYTHONPATH": str(agent_root / "src"), "PYTHONIOENCODING": "utf-8"}
    # Not `-I`: the model is imported from the agent's own src/ through PYTHONPATH.
    result = subprocess.run([trend_python(agent_root), "-c", code],
                            cwd=agent_root, env=env, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"pydantic export failed for {spec}:\n{result.stderr.strip()}")
    return json.loads(result.stdout)


def envelope_version(agent_id: str) -> dict[str, Any]:
    envelope = ENVELOPES[agent_id]
    path = repo_root(agent_id) / envelope.source_file
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    match = re.search(rf'^{envelope.constant}\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return {"route": envelope.route, "contract_version": match.group(1) if match else None,
            "detected_from": f"{envelope.source_file}:{envelope.constant}"}


# The orchestrator constant each agent's handoff version is compared with (core.py). new-product-agent
# moved to vogue-handoff/0.2 and core.py accepts it under its own constant (main 559a39d, 2026-11-12).
ORCHESTRATOR_VERSION_CONSTANT = {"new-product-agent": "PROJECT06_CONTRACT_VERSION"}


def orchestrator_contract_version(agent_id: str | None = None) -> str:
    constant = ORCHESTRATOR_VERSION_CONSTANT.get(agent_id or "", "CONTRACT_VERSION")
    text = (ROOT / "src" / "vogue_orchestrator" / "core.py").read_text(encoding="utf-8")
    match = re.search(rf'^{constant}\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "unknown"


def source_hashes(agent_root: Path, files: tuple[str, ...]) -> list[dict[str, str]]:
    out = []
    for rel in files:
        path = agent_root / rel
        out.append({"path": rel, "sha256": sha256_normalized(path) if path.exists() else "missing"})
    return out


def fresh_schema(entry: Entry, agent_root: Path) -> dict[str, Any] | None:
    if entry.method == "copy":
        return json.loads((agent_root / entry.source).read_text(encoding="utf-8"))
    if entry.method in {"pydantic", "pinned"}:
        return pydantic_schema(agent_root, entry.source)
    return None  # authored


def build(entry: Entry, agent_root: Path, schema: dict[str, Any]) -> dict[str, Any]:
    branch = git(agent_root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = git(agent_root, "rev-parse", "--short=12", "HEAD")
    dirty = git(agent_root, "status", "--porcelain", "--", *entry.source_files)
    provenance = {
        "agent_id": entry.agent_id,
        "contract": entry.contract,
        "direction": entry.direction,
        "payload_schema_version": entry.payload_version,
        "method": entry.method,
        "source_repo": f"KG-RAG-02/{REPOS[entry.agent_id][1]}",
        "source_branch": branch,
        "source_commit": commit + ("+uncommitted" if dirty and dirty != "unknown" else ""),
        "source_schema": entry.source if entry.method == "copy" else None,
        "source_model": entry.source if entry.method in {"pydantic", "pinned"} else None,
        "source_files": source_hashes(agent_root, entry.source_files),
        "handoff": envelope_version(entry.agent_id),
        "generated_by": GENERATED_BY,
        "note": NOTE,
    }
    return {"_provenance": {k: v for k, v in provenance.items() if v is not None}, "json_schema": schema}


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


METHOD_LABEL = {"copy": "복사", "pydantic": "pydantic 내보내기", "pinned": "고정(검사만)", "authored": "코드 읽고 작성"}


def render_sources() -> str:
    """contracts/agents/SOURCES.md, generated from each snapshot's _provenance."""
    lines = [
        "# Agent 계약 스냅샷 출처",
        "",
        f"생성물이다. 직접 고치지 않는다. 다시 만들기: `py {GENERATED_BY}`, 확인: `py {GENERATED_BY} --check`.",
        "구조와 갱신 절차는 [`../README.md`](../README.md)를 본다.",
        "",
        "## 스냅샷",
        "",
        "| Agent | 방향 | 파일 | 계약 | payload 버전 | 방식 | 출처 (branch @ commit) | 원본 파일 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for entry in ENTRIES:
        path = CONTRACTS / entry.agent_id / entry.direction / entry.target
        if not path.exists():
            continue
        prov = json.loads(path.read_text(encoding="utf-8"))["_provenance"]
        rel = f"{entry.agent_id}/{entry.direction}/{entry.target}"
        origin = (f"{prov.get('source_branch', '-')} @ {prov.get('source_commit', '-')}"
                  if "source_branch" in prov else prov.get("source_commit", "-"))
        source = entry.source if entry.method != "authored" else ", ".join(entry.source_files)
        lines.append(f"| {entry.agent_id} | {entry.direction} | [`{entry.target}`]({rel}) | {entry.contract} | "
                     f"{entry.payload_version or '-'} | {METHOD_LABEL[entry.method]} | `{origin}` | `{source}` |")
    lines += [
        "",
        "## HTTP handoff contract_version",
        "",
        "오케스트레이터(`src/vogue_orchestrator/core.py`)가 Agent마다 기대하는 값과 Agent 소스의 값을 비교한다. "
        "route는 Agent 저장소가 제공하는 `/runs`이며, 오케스트레이터가 실제로 부르는 경로와는 다를 수 있다"
        " (2026-11-12 `main`: v0.1 실행은 Trend·Stock `/runs` 후 P07 `/reviews`, New Product는 부르지 않음).",
        "",
        "| Agent | route | Agent가 받는 contract_version | 읽은 위치 | 오케스트레이터 기대값 (core.py) | 비교 |",
        "|---|---|---|---|---|---|",
    ]
    for agent_id in REPOS:
        if not repo_root(agent_id).exists():
            continue
        found = envelope_version(agent_id)
        expected = orchestrator_contract_version(agent_id)
        constant = ORCHESTRATOR_VERSION_CONSTANT.get(agent_id, "CONTRACT_VERSION")
        same = "같음" if found["contract_version"] == expected else "**다름 → 이 단계 호출 실패**"
        lines.append(f"| {agent_id} | `{found['route']}` | `{found['contract_version']}` | "
                     f"`{found['detected_from']}` | `{expected}` (`{constant}`) | {same} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a snapshot is out of date")
    args = parser.parse_args(argv)

    stale: list[str] = []
    for entry in ENTRIES:
        agent_root = repo_root(entry.agent_id)
        target = CONTRACTS / entry.agent_id / entry.direction / entry.target
        label = f"{entry.agent_id}/{entry.direction}/{entry.target}"
        if not agent_root.exists():
            print(f"SKIP   {label}: checkout not found at {agent_root}")
            continue
        current = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        schema = fresh_schema(entry, agent_root)

        if entry.method in {"pinned", "authored"}:
            if current is None:
                stale.append(label)
                print(f"MISSING {label}")
                continue
            recorded = {f["path"]: f["sha256"] for f in current["_provenance"].get("source_files", [])}
            now = {f["path"]: f["sha256"] for f in source_hashes(agent_root, entry.source_files)}
            if entry.method == "pinned":
                ok = current.get("json_schema") == schema
                print(f"{'OK    ' if ok else 'STALE '} {label} (pinned: json_schema vs current model)")
            else:
                changed = [p for p in now if recorded.get(p) != now[p]]
                ok = not changed
                print(f"{'OK    ' if ok else 'REVIEW'} {label} (authored"
                      + (f": source changed {changed})" if changed else ")"))
            if not ok:
                stale.append(label)
            continue

        fresh = build(entry, agent_root, schema)
        same_schema = current is not None and current.get("json_schema") == fresh["json_schema"]
        same_sources = (current is not None and current["_provenance"].get("source_files")
                        == fresh["_provenance"]["source_files"]
                        and current["_provenance"].get("handoff") == fresh["_provenance"]["handoff"])
        if args.check:
            ok = same_schema and same_sources
            print(f"{'OK    ' if ok else 'STALE '} {label}")
            if not ok:
                stale.append(label)
        elif same_schema and same_sources:
            print(f"same   {label}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(dump(fresh), encoding="utf-8", newline="\n")
            print(f"wrote  {label}")

    sources_path = CONTRACTS / "SOURCES.md"
    sources = render_sources()
    current_sources = sources_path.read_text(encoding="utf-8") if sources_path.exists() else None
    if args.check:
        ok = current_sources == sources
        print(f"{'OK    ' if ok else 'STALE '} SOURCES.md")
        if not ok:
            stale.append("SOURCES.md")
    elif current_sources != sources:
        sources_path.write_text(sources, encoding="utf-8", newline="\n")
        print("wrote  SOURCES.md")
    else:
        print("same   SOURCES.md")

    print("\nhandoff contract_version (agent source vs orchestrator core.py)")
    for agent_id in REPOS:
        if not repo_root(agent_id).exists():
            continue
        found = envelope_version(agent_id)
        expected = orchestrator_contract_version(agent_id)
        flag = "same    " if found["contract_version"] == expected else "MISMATCH"
        print(f"  {flag} {agent_id}: {found['contract_version']} ({found['detected_from']}) vs core.py {expected}")

    if args.check and stale:
        print(f"\n{len(stale)} snapshot(s) out of date: run py {GENERATED_BY}"
              " (pinned/authored ones are refreshed by hand)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
