"""Snapshot each agent's input/output contract into contracts/agents/<agent-id>/ from the sibling checkouts.

The orchestrator maps every agent's input and output, so it keeps a code-based copy of each agent's
contract here. The owning repository stays the source of truth; these files are copies with
provenance (repo, branch, commit, source-file sha256) so a later change in the owner can be detected.
Entries of this repository (agent id vogue-orchestrator) read their sources from this checkout.

Methods
  copy      wrap a JSON Schema file that the agent repository already publishes. A vogue-workflow/0.1
            schema is copied from the producer's vendored copy of contracts/workflow (P08: the canonical file)
  pydantic  run the agent's own Python (its .venv) and call `model_json_schema()` on its model
  pinned    an existing snapshot that generated code depends on (trend-query: query/vocabulary.py is
            generated from it). Checked only; refresh it through its own workflow
  authored  written in this repository by reading the agent's code, because the agent publishes no
            JSON Schema (new-product-agent /runs, the P08 MD decision request). Never rewritten; the
            check reports when its sources change

Also reads the `contract_version` constant of every agent route from its source (`NAME = "..."` or
`NAME = '...'`) and compares it with the value the orchestrator uses on that route: core.py for /runs
(`CONTRACT_VERSION`, or `PROJECT06_CONTRACT_VERSION` for new-product-agent; ORCHESTRATOR_VERSION_CONSTANT),
or the "<file>:<CONSTANT>" an Envelope names in `expected_from` (the vogue-workflow/0.1 routes compare with
src/vogue_orchestrator/workflow_contract.py:VERSION). A mismatch means the HTTP call on that route fails.

Both modes also compare the workflow contract copies: every file pinned in contracts/workflow/snapshot.json
must have its pinned sha256 (after CRLF -> LF) in contracts/workflow and in each vendored copy (VENDORED);
a sibling checkout that is absent is skipped with the reason. Only --check turns a difference into exit 1.

Usage
  py scripts/export_agent_contracts.py            # write copy/pydantic snapshots (pinned/authored untouched)
  py scripts/export_agent_contracts.py --check    # exit 1 if a snapshot or a workflow contract copy is out of date

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
WORKFLOW = ROOT / "contracts" / "workflow"
GENERATED_BY = "scripts/export_agent_contracts.py"
NOTE = "생성물이다. 직접 고치지 않는다. 기준은 소유 레포이고 이 파일은 복사본이다."
AUTHORED_NOTE = ("소유 레포에 JSON Schema가 없어 코드를 읽고 이 저장소에서 작성했다. 스크립트가 다시 쓰지 않는다. "
                 "source_files의 sha256이 달라지면 코드를 다시 읽고 손으로 갱신한다.")

SELF = "vogue-orchestrator"  # this repository: its entries read files under ROOT, not a sibling checkout
REPOS = {
    "trend-analysis-agent": ("VOGUE_TREND_ROOT", "trend-analysis-agent"),
    "stock-agent": ("VOGUE_STOCK_ROOT", "stock-agent"),
    "sales-market-agent": ("VOGUE_SALES_MARKET_ROOT", "sales-market-agent"),
    "new-product-agent": ("VOGUE_NEW_PRODUCT_ROOT", "new-product-agent"),
}


@dataclass(frozen=True)
class Envelope:
    route: str
    source_file: str  # in the agent repository
    constant: str  # name of the contract_version constant in source_file
    # "<file in this repository>:<CONSTANT>" holding the value the orchestrator uses on this route.
    # None keeps the /runs default: core.py with ORCHESTRATOR_VERSION_CONSTANT (else CONTRACT_VERSION).
    expected_from: str | None = None


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
    envelope: Envelope | None = None  # route the contract travels on; None: the agent's /runs (ENVELOPES)


ENVELOPES = {
    "trend-analysis-agent": Envelope("/v1/agents/trend-analysis-agent/runs", "src/trend_agent/api/server.py",
                                     "CONTRACT_VERSION"),
    "stock-agent": Envelope("/v1/agents/stock-agent/runs", "stock_agent/api.py", "HANDOFF_CONTRACT_VERSION"),
    "sales-market-agent": Envelope("/v1/agents/sales-market-agent/runs", "src/market_research_agent/http_server.py",
                                   "CONTRACT_VERSION"),
    "new-product-agent": Envelope("/v1/agents/new-product-agent/runs", "src/vogue_npp/adapters/handoff.py",
                                  "CONTRACT_VERSION"),
}

# vogue-workflow/0.1 routes (conventions section 1). The orchestrator sends workflow_contract.py:VERSION.
WORKFLOW_CONTRACT_PY = "src/vogue_orchestrator/workflow_contract.py"
WORKFLOW_ENVELOPES = {
    "new-product-agent": Envelope("/v1/agents/new-product-agent/drafts", "src/vogue_npp/adapters/planning_draft.py",
                                  "CONTRACT_VERSION", f"{WORKFLOW_CONTRACT_PY}:VERSION"),
    "sales-market-agent": Envelope("/v1/agents/sales-market-agent/reviews", "src/market_research_agent/reviews.py",
                                   "VERSION", f"{WORKFLOW_CONTRACT_PY}:VERSION"),
}
# Routes this repository serves. Read from this checkout; there is no other side to compare with.
RUN_ENVELOPE = Envelope("/api/workflow/run, /v1/orchestrations", WORKFLOW_CONTRACT_PY, "VERSION")
MD_DECISION_ENVELOPE = Envelope("/api/workflow/runs/{rid}/md-decision", WORKFLOW_CONTRACT_PY, "VERSION")

# Byte-identical copies of contracts/workflow that producers vendor (conventions section 1).
VENDORED = {
    "new-product-agent": "contracts/workflow-proposed",
    "sales-market-agent": "src/market_research_agent/resources/workflow_contract",
}
# Pinned files a vogue-workflow/0.1 schema's $ref resolves to (common.schema.json refers to upstream/).
WORKFLOW_REFS = ("common.schema.json", "upstream/trend-result-1.1.schema.json", "upstream/stock-result-1.0.schema.json")


def workflow_entry(agent_id: str, direction: str, name: str, contract: str, payload_version: str | None,
                   directory: str, envelope: Envelope) -> Entry:
    """Copy of one vogue-workflow/0.1 schema from `directory` of the agent's repository.

    The file name carries the boundary's producer schema version (planning-draft/1.0, sales-market-review/1.0,
    integrated-result/1.0). source_files add the pinned files its $ref resolves to.
    """
    source = f"{directory}/{name}.schema.json"
    return Entry(agent_id, direction, f"{name}-1.0.contract.json", contract, payload_version, "copy",
                 (source, *(f"{directory}/{rel}" for rel in WORKFLOW_REFS)), source, envelope=envelope)


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
    workflow_entry("sales-market-agent", "input", "sales-market-review-request",
                   "sales-market-agent /reviews request (P08 → P07 full-Draft review)", None,
                   VENDORED["sales-market-agent"], WORKFLOW_ENVELOPES["sales-market-agent"]),
    workflow_entry("sales-market-agent", "output", "sales-market-review-response",
                   "sales-market-agent /reviews response (SalesMarketReview envelope)", "sales-market-review/1.0",
                   VENDORED["sales-market-agent"], WORKFLOW_ENVELOPES["sales-market-agent"]),
    Entry("new-product-agent", "input", "planning-draft-input-0.3.contract.json",
          "new-product-agent /runs input (three producer payloads)", None, "authored",
          ("src/vogue_npp/adapters/handoff.py", "src/vogue_npp/prototype/server.py")),
    Entry("new-product-agent", "output", "planning-draft-result-1.0.contract.json",
          "new-product-agent planning draft result", "1.0", "authored",
          ("src/vogue_npp/prototype/server.py", "src/vogue_npp/adapters/handoff.py")),
    workflow_entry("new-product-agent", "input", "planning-draft-request",
                   "new-product-agent /drafts request (P08 → P06 Draft: draft_only | draft_with_visuals)", None,
                   VENDORED["new-product-agent"], WORKFLOW_ENVELOPES["new-product-agent"]),
    workflow_entry("new-product-agent", "output", "planning-draft-response",
                   "new-product-agent /drafts response (PlanningDraft envelope)", "planning-draft/1.0",
                   VENDORED["new-product-agent"], WORKFLOW_ENVELOPES["new-product-agent"]),
    workflow_entry(SELF, "output", "integrated-result", "vogue-orchestrator workflow run response (integrated result)",
                   "integrated-result/1.0", "contracts/workflow", RUN_ENVELOPE),
    Entry(SELF, "input", "md-decision-request-1.0.contract.json",
          "vogue-orchestrator MD decision request (POST /api/workflow/runs/{rid}/md-decision)", None, "authored",
          ("src/vogue_orchestrator/workflow_http.py", "src/vogue_orchestrator/workflow_store.py",
           WORKFLOW_CONTRACT_PY),
          envelope=MD_DECISION_ENVELOPE),
)


def repo_name(agent_id: str) -> str:
    return SELF if agent_id == SELF else REPOS[agent_id][1]


def repo_root(agent_id: str) -> Path:
    if agent_id == SELF:
        return ROOT
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
    # No bytecode: the agent checkout stays untouched (no __pycache__ in a sibling repository).
    env = {**os.environ, "PYTHONPATH": str(agent_root / "src"), "PYTHONIOENCODING": "utf-8",
           "PYTHONDONTWRITEBYTECODE": "1"}
    # Not `-I`: the model is imported from the agent's own src/ through PYTHONPATH.
    result = subprocess.run([trend_python(agent_root), "-c", code],
                            cwd=agent_root, env=env, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"pydantic export failed for {spec}:\n{result.stderr.strip()}")
    return json.loads(result.stdout)


# A module-level string constant: NAME = "..." or NAME = '...', optionally annotated (NAME: Final = ...).
CONSTANT_PATTERN = r"""^{name}\s*(?::[^=\n]*)?=\s*(?:"([^"\n]+)"|'([^'\n]+)')"""


def read_constant(path: Path, name: str) -> str | None:
    """Value of the first module-level string constant `name` in `path`, or None if absent or not a literal."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    match = re.search(CONSTANT_PATTERN.format(name=re.escape(name)), text, re.MULTILINE)
    if match is None:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def envelope_version(agent_id: str, envelope: Envelope | None = None) -> dict[str, Any]:
    """The contract_version an agent route accepts, read from the agent source (default: its /runs)."""
    envelope = envelope or ENVELOPES[agent_id]
    value = read_constant(repo_root(agent_id) / envelope.source_file, envelope.constant)
    return {"route": envelope.route, "contract_version": value,
            "detected_from": f"{envelope.source_file}:{envelope.constant}"}


# The core.py constant each agent's /runs version is compared with. new-product-agent has its own constant
# (main 559a39d, 2026-11-12: vogue-handoff/0.2); new-product-agent moved /runs to vogue-handoff/0.3 in 340a7d4
# (2026-11-12), so that row reports MISMATCH until core.py follows. P08 calls P06 on /drafts, not /runs.
ORCHESTRATOR_VERSION_CONSTANT = {"new-product-agent": "PROJECT06_CONTRACT_VERSION"}
CORE_PY = "src/vogue_orchestrator/core.py"


def expected_from(agent_id: str | None = None, envelope: Envelope | None = None) -> str:
    """"<file>:<CONSTANT>" in this repository holding the value the orchestrator uses on an agent route."""
    if envelope is not None and envelope.expected_from:
        return envelope.expected_from
    return f"{CORE_PY}:{ORCHESTRATOR_VERSION_CONSTANT.get(agent_id or '', 'CONTRACT_VERSION')}"


def orchestrator_contract_version(agent_id: str | None = None, envelope: Envelope | None = None) -> str:
    path, _, constant = expected_from(agent_id, envelope).rpartition(":")
    return read_constant(ROOT / path, constant) or "unknown"


def transports() -> list[tuple[str, Envelope]]:
    """Every agent route whose contract_version is compared with the orchestrator's value."""
    return [*ENVELOPES.items(), *WORKFLOW_ENVELOPES.items()]


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
        "source_repo": f"KG-RAG-02/{repo_name(entry.agent_id)}",
        "source_branch": branch,
        "source_commit": commit + ("+uncommitted" if dirty and dirty != "unknown" else ""),
        "source_schema": entry.source if entry.method == "copy" else None,
        "source_model": entry.source if entry.method in {"pydantic", "pinned"} else None,
        "source_files": source_hashes(agent_root, entry.source_files),
        "handoff": envelope_version(entry.agent_id, entry.envelope),
        "generated_by": GENERATED_BY,
        "note": NOTE,
    }
    return {"_provenance": {k: v for k, v in provenance.items() if v is not None}, "json_schema": schema}


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def pinned_differences(base: Path, pins: dict[str, str]) -> list[str]:
    """Pinned files under `base` that are missing or whose LF-normalized sha256 is not the pin."""
    out = []
    for rel, pin in sorted(pins.items()):
        path = base / rel
        if not path.is_file():
            out.append(f"{rel} (missing)")
        elif sha256_normalized(path) != pin:
            out.append(f"{rel} (sha256 differs from snapshot.json)")
    return out


def vendored_check() -> tuple[list[str], list[str]]:
    """Compare contracts/workflow and every vendored copy with the pins in contracts/workflow/snapshot.json.

    Returns (report lines, problems). A sibling checkout that is not present is reported as SKIP, not a problem.
    """
    snapshot = WORKFLOW / "snapshot.json"
    if not snapshot.is_file():
        return [f"MISSING {snapshot.relative_to(ROOT).as_posix()}"], ["snapshot.json (missing)"]
    pins = json.loads(snapshot.read_text(encoding="utf-8"))["files"]
    targets = [(f"{SELF}/contracts/workflow", WORKFLOW, None)]
    targets += [(f"{agent_id}/{directory}", repo_root(agent_id) / directory, repo_root(agent_id))
                for agent_id, directory in VENDORED.items()]
    lines: list[str] = []
    problems: list[str] = []
    for label, base, checkout in targets:
        if checkout is not None and not checkout.exists():
            lines.append(f"SKIP   {label}: checkout not found at {checkout}")
            continue
        differences = pinned_differences(base, pins)
        problems += [f"{label}/{item}" for item in differences]
        lines += [f"STALE  {label}/{item}" for item in differences] or [f"OK     {label} ({len(pins)} files)"]
    return lines, problems


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
        "Agent 경로마다 Agent 소스의 값과 오케스트레이터가 그 경로에 쓰는 값을 비교한다. `/runs`(vogue-handoff)는 "
        "`src/vogue_orchestrator/core.py`의 상수와, vogue-workflow/0.1 경로(P06 `/drafts`, P07 `/reviews`)는 "
        "`src/vogue_orchestrator/workflow_contract.py`의 `VERSION`과 비교한다. route는 Agent 저장소가 제공하는 경로이며, "
        "오케스트레이터가 실제로 부르는 경로와는 다를 수 있다 (2026-11-13 `integration/docker-e2e`: 워크플로 실행은 "
        "Trend·Stock `/runs` 후 P06 `/drafts`, P07 `/reviews`를 부르고, New Product `/runs`는 부르지 않음).",
        "",
        "| Agent | route | Agent가 받는 contract_version | 읽은 위치 | 오케스트레이터 값 | 읽은 위치 (이 저장소) | 비교 |",
        "|---|---|---|---|---|---|---|",
    ]
    for agent_id, envelope in transports():
        if not repo_root(agent_id).exists():
            continue
        found = envelope_version(agent_id, envelope)
        expected = orchestrator_contract_version(agent_id, envelope)
        same = "같음" if found["contract_version"] == expected else "**다름 → 이 경로로 부르면 실패**"
        lines.append(f"| {agent_id} | `{found['route']}` | `{found['contract_version']}` | "
                     f"`{found['detected_from']}` | `{expected}` | `{expected_from(agent_id, envelope)}` | {same} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if a snapshot or a workflow contract copy is out of date")
    args = parser.parse_args(argv)

    stale: list[str] = []
    checked = 0
    for entry in ENTRIES:
        agent_root = repo_root(entry.agent_id)
        target = CONTRACTS / entry.agent_id / entry.direction / entry.target
        label = f"{entry.agent_id}/{entry.direction}/{entry.target}"
        if not agent_root.exists():
            print(f"SKIP   {label}: checkout not found at {agent_root}")
            continue
        checked += 1
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

    print("\nworkflow contract copies (pins: contracts/workflow/snapshot.json, LF-normalized sha256)")
    copy_lines, copy_problems = vendored_check()
    for line in copy_lines:
        print(f"  {line}")

    print("\nhandoff contract_version (agent source vs orchestrator)")
    for agent_id, envelope in transports():
        if not repo_root(agent_id).exists():
            continue
        found = envelope_version(agent_id, envelope)
        expected = orchestrator_contract_version(agent_id, envelope)
        flag = "same    " if found["contract_version"] == expected else "MISMATCH"
        print(f"  {flag} {envelope.route}: {found['contract_version']} ({found['detected_from']}) "
              f"vs {expected} ({expected_from(agent_id, envelope)})")

    if not args.check:
        return 0
    if stale:
        print(f"\n{len(stale)} snapshot(s) out of date: run py {GENERATED_BY}"
              " (pinned/authored ones are refreshed by hand)")
    if copy_problems:
        print(f"\n{len(copy_problems)} workflow contract file(s) differ from contracts/workflow/snapshot.json:"
              " the owning repository re-copies them (never edit pinned bytes here)")
    if stale or copy_problems:
        return 1
    skipped = len(ENTRIES) - checked
    print(f"\ncheck passed: {checked} snapshot(s) and SOURCES.md up to date"
          + (f" ({skipped} skipped: checkout not found)" if skipped else "")
          + "; workflow contract copies match snapshot.json (handoff MISMATCH lines do not fail the check)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
