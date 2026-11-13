#!/usr/bin/env python3
"""Record the vogue-workflow/0.1 contract baseline: contracts/workflow/baseline.json.

This script is the only writer of baseline.json. The runtime gate in
vogue_orchestrator.workflow_http reads it; "frozen" is never hand-edited.

  --check                 verify baseline.json against snapshot.json and the schema bytes; exit 1 on drift
  --init                  write a `proposed` baseline for the current snapshot (claims no validation)
  --freeze [--suite CMD]  run the reference fixtures, mutation probes, vendored-copy check, sibling-repo
                          cleanliness check and every --suite command; write `frozen` only if all pass
                          (nothing is written on failure)

No network access. Sibling repos are read from the parent directory of this repo.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "workflow"
SNAPSHOT = CONTRACTS / "snapshot.json"
BASELINE = CONTRACTS / "baseline.json"
TOOLS = ROOT / "tools" / "workflow_contract"
FORMAT = "vogue-workflow-baseline/1"
SIBLINGS = ("trend-analysis-agent", "stock-agent", "new-product-agent", "sales-market-agent", "vogue-orchestrator")
# Byte-identical copies of the P08 schema files that producers vendor (repo, relative directory).
VENDORED = (
    ("new-product-agent", "contracts/workflow-proposed"),
    ("sales-market-agent", "src/market_research_agent/resources/workflow_contract"),
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot_state():
    raw = SNAPSHOT.read_bytes()
    snapshot = json.loads(raw)
    errors = []
    for rel, expected in sorted(snapshot["files"].items()):
        path = CONTRACTS / rel
        if not path.is_file():
            errors.append(f"missing:{rel}")
        elif sha256_bytes(path.read_bytes()) != expected:
            errors.append(f"hash:{rel}")
    return snapshot, sha256_bytes(raw), errors


def reference_suite():
    sys.path.insert(0, str(TOOLS))
    try:
        import probe_semantic_gaps  # noqa: E402
        import validate_contracts  # noqa: E402
        summary = validate_contracts.collect()
        mutations = probe_semantic_gaps.collect()
    finally:
        sys.path.remove(str(TOOLS))
    failed_cases = [r["case"] for r in summary["results"] if not r["pass"]]
    accepted_mutations = [r["case"] for r in mutations["results"] if r["accepted"]]
    return (
        {"cases": summary["fixtures"], "passed": summary["passed"], "failed": summary["failed"], "failed_cases": failed_cases},
        {"cases": mutations["cases"], "rejected": mutations["rejected"], "accepted": accepted_mutations},
    )


def vendored_check(snapshot):
    report, problems = {}, []
    for repo, rel_dir in VENDORED:
        base = ROOT.parent / repo / rel_dir
        entry = {"path": f"{repo}/{rel_dir}", "files": {}}
        if not base.is_dir():
            entry["status"] = "missing"
            problems.append(f"{repo}:missing")
        else:
            for rel in sorted(snapshot["files"]):
                ours = sha256_bytes(lf((CONTRACTS / rel).read_bytes()))
                theirs_path = base / rel
                if not theirs_path.is_file():
                    entry["files"][rel] = "missing"
                    problems.append(f"{repo}:{rel}:missing")
                elif sha256_bytes(lf(theirs_path.read_bytes())) != ours:
                    entry["files"][rel] = "STALE"
                    problems.append(f"{repo}:{rel}:STALE")
                else:
                    entry["files"][rel] = "match"
            entry["status"] = "match" if all(v == "match" for v in entry["files"].values()) else "drift"
        report[repo] = entry
    return report, problems


def git(repo: Path, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def sources():
    report, problems = {}, []
    for name in SIBLINGS:
        repo = ROOT if name == "vogue-orchestrator" else ROOT.parent / name
        if not (repo / ".git").exists():
            report[name] = {"status": "missing"}
            problems.append(f"{name}:missing")
            continue
        status = git(repo, "status", "--porcelain")
        clean = status == ""
        report[name] = {"branch": git(repo, "rev-parse", "--abbrev-ref", "HEAD"), "head": git(repo, "rev-parse", "HEAD"), "clean": clean}
        if not clean:
            problems.append(f"{name}:dirty")
    return report, problems


def run_suite(command: str):
    started = dt.datetime.now(dt.timezone.utc)
    proc = subprocess.run(shlex.split(command), cwd=ROOT, capture_output=True)
    output = proc.stdout + proc.stderr
    return {
        "command": command,
        "returncode": proc.returncode,
        "output_sha256": sha256_bytes(output),
        "output_tail": output.decode("utf-8", "replace").strip().splitlines()[-3:],
        "started_at": started.isoformat(timespec="seconds"),
    }


def write_atomic(path: Path, value):
    data = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".baseline-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def base_document(snapshot, snapshot_sha):
    return {
        "format": FORMAT,
        "contract_version": snapshot["contract_version"],
        "transport_version": snapshot["transport_version"],
        "state": "proposed",
        "producer_consumer_validated": False,
        "snapshot_sha256": snapshot_sha,
        "files": dict(sorted(snapshot["files"].items())),
        "evidence": {},
        "sources": {},
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "notes": [],
    }


def check_baseline(baseline, snapshot, snapshot_sha, file_errors):
    problems = list(file_errors)
    if baseline.get("format") != FORMAT:
        problems.append("format")
    if baseline.get("snapshot_sha256") != snapshot_sha:
        problems.append("snapshot_sha256")
    if baseline.get("files") != dict(sorted(snapshot["files"].items())):
        problems.append("files")
    for key in ("contract_version", "transport_version"):
        if baseline.get(key) != snapshot.get(key):
            problems.append(key)
    if baseline.get("state") == "frozen":
        evidence = baseline.get("evidence", {})
        if not baseline.get("producer_consumer_validated"):
            problems.append("frozen_without_validation")
        if evidence.get("reference_fixtures", {}).get("failed", 1) != 0:
            problems.append("frozen_with_reference_failures")
        if any(s.get("returncode") != 0 for s in evidence.get("suites", [])):
            problems.append("frozen_with_suite_failures")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--init", action="store_true")
    mode.add_argument("--freeze", action="store_true")
    parser.add_argument("--suite", action="append", default=[], help="extra test command that must exit 0 (repeatable)")
    args = parser.parse_args(argv)

    snapshot, snapshot_sha, file_errors = snapshot_state()

    if args.check:
        if not BASELINE.exists():
            print("baseline.json missing")
            return 1
        baseline = read_json(BASELINE)
        problems = check_baseline(baseline, snapshot, snapshot_sha, file_errors)
        print(json.dumps({"state": baseline.get("state"), "snapshot_sha256": snapshot_sha, "problems": problems}))
        return 1 if problems else 0

    if file_errors:
        print(json.dumps({"error": "snapshot does not match schema bytes", "problems": file_errors}))
        return 1

    document = base_document(snapshot, snapshot_sha)
    if args.init:
        document["notes"].append("Proposed baseline: no producer/consumer validation claimed. Run --freeze to record evidence.")
        write_atomic(BASELINE, document)
        print(json.dumps({"state": "proposed", "snapshot_sha256": snapshot_sha}))
        return 0

    reference, mutations = reference_suite()
    vendored, vendored_problems = vendored_check(snapshot)
    source_report, source_problems = sources()
    suites = [run_suite(command) for command in args.suite]
    problems = []
    if reference["failed"]:
        problems.append(f"reference_fixtures_failed:{reference['failed']}")
    if mutations["accepted"]:
        problems.append(f"mutations_accepted:{len(mutations['accepted'])}")
    problems += vendored_problems + source_problems
    problems += [f"suite_failed:{s['command']}" for s in suites if s["returncode"] != 0]
    if problems:
        print(json.dumps({"frozen": False, "problems": problems, "reference": reference, "mutations": mutations}, indent=2))
        return 1
    document.update(
        state="frozen",
        producer_consumer_validated=True,
        evidence={"reference_fixtures": reference, "mutation_probes": mutations, "vendored_copies": vendored, "suites": suites},
        sources=source_report,
    )
    document["notes"].append(
        "Frozen = contract evidence only (offline fixtures, owner serializer/parser boundary and in-process TCP suites). "
        "Live data, model quality and browser acceptance are recorded separately."
    )
    write_atomic(BASELINE, document)
    print(json.dumps({"frozen": True, "snapshot_sha256": snapshot_sha, "suites": len(suites)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
