#!/usr/bin/env python3
"""Record the current workflow contract baseline: contracts/workflow/baseline.json.

This script is the only writer of baseline.json. The runtime gate in
fashion_planner_orchestrator.workflow_http reads it; "frozen" is never hand-edited.

  --check                 verify baseline.json against snapshot.json and the schema bytes; exit 1 on drift
  --init                  write a `proposed` baseline for the current snapshot (claims no validation)
  --freeze [--suite CMD]  run the reference fixtures, mutation probes, vendored-copy check, sibling-repo
                          cleanliness check and every --suite command; write `frozen` only if all pass
                          (nothing is written on failure)

No network access. Sibling repos are read from the parent directory of this repo. Each source is recorded by
its checkout cleanliness and the SHA-256 of its exact non-ignored contents, not by branch or commit: commit
SHAs change when a history is rewritten, the contents do not.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import stat
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
FORMAT = "workflow-baseline"
SOURCE_PATHS = {
    "trend": ("fashion-planner-trend",),
    "inventory": ("fashion-planner-inventory",),
    "novelty": ("fashion-planner-novelty",),
    "research": ("fashion-planner-research",),
    "orchestrator": (".",),
}
# novelty and research own their native wire contracts; orchestrator call-side schemas are not vendored there.
VENDORED = ()


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


def _worktree_fingerprint(repo: Path, exclude=()):
    """Hash exact non-ignored tracked and untracked checkout contents without exposing them."""
    excluded = {Path(item).as_posix() for item in exclude}
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all", "-z"],
        capture_output=True, check=True,
    ).stdout
    listed = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True, check=True,
    ).stdout
    digest = hashlib.sha256()
    for raw_path in sorted(set(filter(None, listed.split(b"\0")))):
        rel = raw_path.decode("utf-8", "surrogateescape")
        if Path(rel).as_posix() in excluded:
            continue
        path = repo / rel
        digest.update(raw_path + b"\0")
        try:
            info = path.lstat()
        except FileNotFoundError:
            digest.update(b"missing\0")
        else:
            if stat.S_ISLNK(info.st_mode):
                digest.update(b"symlink\0" + os.readlink(path).encode("utf-8", "surrogateescape"))
            elif stat.S_ISREG(info.st_mode):
                digest.update(b"file\0" + path.read_bytes())
            else:
                digest.update(b"other\0" + str(stat.S_IFMT(info.st_mode)).encode("ascii"))
        digest.update(b"\0")
    return status, digest.hexdigest()


def sources(allow_dirty=False):
    report, problems = {}, []
    for name, candidates in SOURCE_PATHS.items():
        repo = None
        for candidate in candidates:
            path = ROOT if candidate == "." else ROOT.parent / candidate
            if not path.is_dir():
                continue
            top = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True)
            if top.returncode == 0 and Path(top.stdout.strip()).resolve() == path.resolve():
                repo = path
                break
        if repo is None:
            report[name] = {"status": "missing"}
            problems.append(f"{name}:missing")
            continue
        excluded = ("contracts/workflow/baseline.json",) if repo == ROOT else ()
        status, worktree_sha = _worktree_fingerprint(repo, exclude=excluded)
        clean = status == b""
        report[name] = {
            "path": str(repo.relative_to(ROOT.parent)) if repo != ROOT else ".",
            "clean": clean,
            "worktree_sha256": worktree_sha,
            "status_sha256": hashlib.sha256(status).hexdigest(),
        }
        if excluded:
            report[name]["worktree_fingerprint_excludes"] = list(excluded)
        if not clean and not allow_dirty:
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
    parser.add_argument("--allow-dirty-sources", action="store_true",
                        help="allow dirty checkouts while recording exact non-ignored worktree SHA-256 values")
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
    source_report, source_problems = sources(allow_dirty=args.allow_dirty_sources)
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
    if args.allow_dirty_sources and any(not source.get("clean", True) for source in source_report.values()):
        document["notes"].append(
            "Dirty source checkouts were explicitly allowed; exact non-ignored worktree contents are fingerprinted in sources."
        )
    write_atomic(BASELINE, document)
    print(json.dumps({"frozen": True, "snapshot_sha256": snapshot_sha, "suites": len(suites)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
