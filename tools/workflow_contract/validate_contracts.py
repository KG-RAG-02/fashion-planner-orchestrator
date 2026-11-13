"""Offline proposal validator, not a replacement for owner service parsers.

Run with an existing Python that has jsonschema/referencing. No downloads,
network, credentials, providers, DB access, or modifications to owner repos.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib
from datetime import datetime
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from build_contracts import BASE, CONTRACTS, ROOT, write_json, atomic

HERE = Path(__file__).resolve().parent


def canonical(value):
    def check(node):
        if isinstance(node, dict):
            if any(not isinstance(key, str) for key in node):
                raise ValueError("NON_STRING_KEY")
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def read_json(path):
    value = json.loads(path.read_bytes(), object_pairs_hook=unique_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_NUMBER")))
    canonical(value)  # Reject exponent overflow (e.g. 1e999) as well as literal NaN.
    return value


class SemanticError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise SemanticError(code)


def pointer(document, path):
    result = document
    for part in path.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        result = result[int(part)] if isinstance(result, list) else result[part]
    return result


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


class FixtureStore:
    def __init__(self, directory):
        self.directory = directory.resolve()
        self.index = read_json(directory / "artifacts.json")
        self.documents = {}
        for uri, entry in self.index.items():
            if entry["media_type"] == "application/json":
                value = read_json(self.directory / entry["path"])
                self.documents[fingerprint(value)] = value

    def verify(self, artifact):
        uri = artifact["uri"]
        require(".." not in urlsplit(uri).path.split("/"), "ARTIFACT_PATH_ESCAPE")
        require(uri in self.index, "ARTIFACT_NOT_FOUND")
        entry = self.index[uri]
        path = (self.directory / entry["path"]).resolve()
        require(path.is_relative_to(self.directory), "ARTIFACT_PATH_ESCAPE")
        data = path.read_bytes()
        require(len(data) == artifact["size_bytes"], "ARTIFACT_SIZE_MISMATCH")
        require(hashlib.sha256(data).hexdigest() == artifact["sha256"], "ARTIFACT_HASH_MISMATCH")
        require(entry["media_type"] == artifact["media_type"], "ARTIFACT_MIME_MISMATCH")
        mime = artifact["media_type"]
        if mime == "image/png":
            require(data.startswith(b"\x89PNG\r\n\x1a\n"), "IMAGE_SIGNATURE_MISMATCH")
            offset, ended = 8, False
            while offset < len(data):
                require(offset + 12 <= len(data), "IMAGE_PNG_TRUNCATED")
                size = struct.unpack(">I", data[offset:offset + 4])[0]
                require(offset + 12 + size <= len(data), "IMAGE_PNG_TRUNCATED")
                kind = data[offset + 4:offset + 8]
                payload = data[offset + 8:offset + 8 + size]
                crc = struct.unpack(">I", data[offset + 8 + size:offset + 12 + size])[0]
                require(zlib.crc32(kind + payload) == crc, "IMAGE_PNG_CRC_INVALID")
                offset += size + 12
                if kind == b"IEND":
                    ended = True
                    break
            require(ended and offset == len(data), "IMAGE_PNG_TRUNCATED")
        elif mime == "image/jpeg":
            require(data.startswith(b"\xff\xd8\xff"), "IMAGE_SIGNATURE_MISMATCH")
        elif mime == "image/webp":
            require(data[:4] == b"RIFF" and data[8:12] == b"WEBP", "IMAGE_SIGNATURE_MISMATCH")
        return data


def semantic(document, schema_name, store):
    """Cross-field checks that JSON Schema cannot express. Every failure is terminal."""
    rid = document["request_id"]
    nodes = list(walk(document))
    retained_docs = {}
    for node in nodes:
        if "request_id" in node:
            require(node["request_id"] == rid, "REQUEST_ID_MISMATCH")
        if {"producer", "result", "contract_version", "status"}.issubset(node):
            body = node["result"]
            require(node["producer"]["schema_version"] == body.get("schema_version"), "PRODUCER_VERSION_MISMATCH")
            require(body.get("agent_id", node["producer"]["agent_id"]) == node["producer"]["agent_id"], "PRODUCER_MISMATCH")
            require(node["status"] == body.get("status"), "STATUS_MISMATCH")
        if {"artifact_id", "uri", "sha256", "media_type", "size_bytes", "immutable"}.issubset(node):
            store.verify(node)
        if {"raw_response", "content_sha256", "raw_artifact"}.issubset(node):
            raw = node["raw_response"]
            require(fingerprint(raw) == node["content_sha256"], "RESPONSE_HASH_MISMATCH")
            original = json.loads(store.verify(node["raw_artifact"]), object_pairs_hook=unique_pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_NUMBER")))
            # Python equality conflates true/1 and 1/1.0. Compare typed JSON bytes.
            require(canonical(original) == canonical(raw), "RAW_ARTIFACT_OBJECT_MISMATCH")
            retained_docs[node["content_sha256"]] = raw
            if raw["producer"]["agent_id"] == "trend-analysis-agent":
                source = raw["result"]
                require(source["status"] in {"ok", "no_results", "error"}, "UNSUPPORTED_TREND_STATUS")
                if source["status"] == "ok":
                    require(bool(source["trends"]) and bool(source["evidence"]), "EMPTY_OK")
                evidence_ids = set()
                for evidence in source["evidence"]:
                    expected = f"trend:{evidence['article_id']}:{evidence['block_idx']}:{hashlib.sha256(evidence['quote'].encode('utf-8')).hexdigest()[:8]}"
                    require(evidence["evidence_id"] == expected, "EVIDENCE_ID_QUOTE_MISMATCH")
                    require(evidence["evidence_id"] not in evidence_ids, "DUPLICATE_TREND_EVIDENCE")
                    evidence_ids.add(evidence["evidence_id"])
                for element in walk(source["trends"]):
                    for field in ["evidence_ids", "focus_evidence_ids"]:
                        if field in element:
                            require(set(element[field]).issubset(evidence_ids), "DANGLING_TREND_EVIDENCE")
            if raw["producer"]["agent_id"] == "stock-agent":
                products = raw["result"]["products"]
                trend_results = raw["result"]["trend_results"]
                require(len(products) == len(trend_results), "STOCK_SLOT_LENGTH_MISMATCH")
                require(raw["status"] != "no_results" or not any(p is not None for p in products), "STOCK_NO_RESULTS_HAS_PRODUCT")
                evidence_ids = {e["evidence_id"] for e in raw["result"]["evidence"]}
                for i, tr in enumerate(trend_results):
                    require(tr["product_index"] == i, "STOCK_SLOT_INDEX_MISMATCH")
                    require(set(tr["evidence_ids"]).issubset(evidence_ids), "STOCK_EVIDENCE_MISSING")
                sample = [p for p in products if p and p["inventory"]["data_nature"] == "sample"]
                if sample:
                    observation = node["observation"]
                    require(observation["synthetic"] is True and observation["data_nature"] in {"sample", "mixed", "synthetic_fixture"}, "SAMPLE_PROVENANCE_LOST")
                    dates = {p["inventory"]["as_of"] for p in sample}
                    require(observation["as_of"] in dates if len(dates) == 1 else observation["as_of"] is None, "AS_OF_MISMATCH")

    lookup = {**store.documents, **retained_docs}
    for node in nodes:
        if {"kind", "text", "evidence_refs"}.issubset(node) and node["kind"] == "fact":
            require(bool(node["evidence_refs"]), "UNEVIDENCED_FACT")
        if {"evidence_id", "source_response_sha256", "source_pointer"}.issubset(node):
            source = lookup.get(node["source_response_sha256"])
            require(source is not None, "EVIDENCE_SOURCE_MISSING")
            try:
                record = pointer(source, node["source_pointer"])
            except (KeyError, IndexError, ValueError, TypeError):
                raise SemanticError("EVIDENCE_POINTER_INVALID") from None
            require(isinstance(record, dict) and record.get("evidence_id") == node["evidence_id"], "EVIDENCE_ID_MISMATCH")
        if "source_request" in node and "source_request_sha256" in node:
            require(fingerprint(node["source_request"]) == node["source_request_sha256"], "SOURCE_REQUEST_HASH_MISMATCH")
            upstream_status = node["source_request"]["input"]["stock_response"]["raw_response"]["status"]
            require(node["status"] != "ok" or upstream_status == "ok", "UPSTREAM_STATUS_HIDDEN")
            # 0.2.0: the image call count is the sum of the visuals' own calls (0 for draft_only).
            visuals = (node.get("draft") or {}).get("visuals") or {}
            require(node.get("image_generation_calls", 0) == sum(v["provider_calls"] for v in visuals.values()), "IMAGE_CALLS_MISMATCH")
            concept = (node.get("draft") or {}).get("concept")
            if concept is not None:
                known_ids = {n["evidence_id"] for n in walk(node["source_request"]) if isinstance(n.get("evidence_id"), str)}
                cited = {i for n in walk(concept) for i in n.get("evidence_ids", [])}
                require(cited <= known_ids, "CONCEPT_EVIDENCE_UNKNOWN")
        if "status" in node and "errors" in node:
            if any(e.get("fatal") is True for e in node["errors"] if isinstance(e, dict)):
                require(node["status"] == "error", "FATAL_ERROR_HIDDEN")
        if "draft_identity" in node and "draft" in node and node["draft"] is not None:
            require(node["draft_identity"]["content_sha256"] == fingerprint(node["draft"]), "DRAFT_CONTENT_HASH_MISMATCH")
        if "review_identity" in node and "review" in node:
            review = node["review"]
            require(node["review_identity"]["content_sha256"] == fingerprint(review), "REVIEW_CONTENT_HASH_MISMATCH")
            source = lookup.get(review["draft_response_sha256"])
            require(source is not None, "REVIEW_DRAFT_SOURCE_MISSING")
            require(source.get("producer", {}).get("agent_id") == "new-product-agent", "REVIEW_SOURCE_PRODUCER_MISMATCH")
            require(source["status"] in {"ok", "partial"}, "UNREVIEWABLE_DRAFT")
            require(review["reviewed_draft"] == source["result"]["draft_identity"], "DRAFT_REVISION_MISMATCH")
            if node["status"] in {"ok", "partial"}:
                require(any(f["evidence_refs"] for f in review["findings"]) or any(m["value"] is not None for m in review["metrics"]), "REVIEW_HAS_NO_ELIGIBLE_BASIS")
        if "mode" in node and node["mode"] in {"draft_only", "draft_with_visuals"}:
            require(node["planning_request"]["query"] == node["user_input"]["text"], "USER_INPUT_MISMATCH")
            trend = node["trend_response"]["raw_response"]
            stock = node["stock_response"]["raw_response"]
            require(trend["status"] == "ok" and stock["status"] != "error", "UPSTREAM_STOP_REQUIRED")
            provenance = stock["result"].get("input_provenance")
            require(provenance is not None, "STOCK_UPSTREAM_PROVENANCE_MISSING")
            if provenance is not None:
                require(provenance["source_payload_sha256"] == fingerprint(trend["result"]), "STOCK_UPSTREAM_HASH_MISMATCH")
                require(provenance["source_schema_version"] == trend["producer"]["schema_version"], "STOCK_UPSTREAM_VERSION_MISMATCH")
            context = node["stock_call_context"]
            wire = {"contract_version": "vogue-handoff/0.1", "request_id": rid, "input": trend["result"]}
            expected_context = {"planning_request_sha256": fingerprint(node["planning_request"]), "trend_response_sha256": node["trend_response"]["content_sha256"],
                                "stock_input_sha256": fingerprint(trend["result"]), "stock_wire_request_sha256": fingerprint(wire),
                                "stock_response_sha256": node["stock_response"]["content_sha256"]}
            require(context == expected_context, "STOCK_CALL_CONTEXT_MISMATCH")
        if "planning_draft" in node and "derived_view" in node:
            retained = node["planning_draft"]
            require(retained["raw_response"]["status"] in {"ok", "partial"}, "UNREVIEWABLE_DRAFT")
            view = node["derived_view"]
            if view is not None:
                require(view["source_response_sha256"] == retained["content_sha256"], "DERIVED_VIEW_HASH_MISMATCH")
                for field in ("category", "season"):
                    path = "/result/draft/" + field
                    require(path in view["source_pointers"] and view["product"][field] == pointer(retained["raw_response"], path), "DERIVED_VIEW_NOT_SOURCE")

    if schema_name != "integrated-result.schema.json":
        return
    body = document["result"]
    sources = body["sources"]
    stages = body["stages"]
    order = ["trend", "stock", "planning_draft", "sales_market_review", "integration", "final_image"]
    names = [s["stage"] for s in stages]
    require(names == order[:len(names)], "STAGE_ORDER_INVALID")
    require([s["sequence"] for s in stages] == list(range(1, len(stages) + 1)), "STAGE_SEQUENCE_INVALID")
    previous_end = None
    for index, stage in enumerate(stages):
        started = datetime.fromisoformat(stage["started_at"].replace("Z", "+00:00"))
        completed = datetime.fromisoformat(stage["completed_at"].replace("Z", "+00:00"))
        require(started <= completed, "STAGE_TIME_INVALID")
        require(previous_end is None or previous_end <= started, "STAGES_OVERLAP")
        previous_end = completed
        if stage["stage"] in sources:
            source = sources[stage["stage"]]
            if source is not None:
                require(source["raw_response"]["status"] == stage["status"], "STAGE_STATUS_MISMATCH")
                require(source["content_sha256"] == stage["response_sha256"], "STAGE_RESPONSE_HASH_MISMATCH")
            else:
                require(stage["status"] == "error" and stage["errors"], "MISSING_STAGE_RESPONSE")
        if stage["status"] == "error":
            require(index == len(stages) - 1, "EXECUTED_AFTER_ERROR")
        if stage["status"] == "no_results" and stage["stage"] in {"trend", "planning_draft"}:
            require(index == len(stages) - 1, "EXECUTED_AFTER_NO_RESULTS_STOP")
    for key, source in sources.items():
        require(source is None or key in names, "UNTRACED_SOURCE")
    draft = sources["planning_draft"]
    review = sources["sales_market_review"]
    if draft is not None:
        original = draft["raw_response"]["result"]["source_request"]["input"]
        for name, key in [("trend_response", "trend"), ("stock_response", "stock")]:
            require(original[name] == sources[key], "UPSTREAM_RAW_REWRITTEN")
        require(original["user_input"] == body["user_input"] and original["planning_request"] == body["planning_request"], "INPUT_RAW_REWRITTEN")
    if review is not None:
        require(draft is not None, "REVIEW_WITHOUT_DRAFT")
        require(review["raw_response"]["result"]["review"]["draft_response_sha256"] == draft["content_sha256"], "REVIEW_DRAFT_HASH_MISMATCH")
    image = body["final_image"]
    if sources["trend"] is not None and sources["trend"]["raw_response"]["status"] == "no_results":
        require(body["status"] == "no_results", "TREND_NO_RESULTS_STATUS_INVALID")
    if draft is not None and draft["raw_response"]["status"] == "no_results":
        require(body["status"] == "no_results", "DRAFT_NO_RESULTS_STATUS_INVALID")
    if review is not None and review["raw_response"]["status"] == "no_results":
        integration_failed = stages[-1]["stage"] == "integration" and stages[-1]["status"] == "error"
        require(body["status"] == ("error" if integration_failed else "partial"), "REVIEW_NO_RESULTS_STATUS_INVALID")
    if "integration" in names:
        require(body["integration"] is not None or stages[4]["status"] == "error", "MISSING_INTEGRATION")
        if stages[4]["status"] != "error":
            require(stages[4]["response_sha256"] == fingerprint(body["integration"]), "INTEGRATION_RESPONSE_HASH_MISMATCH")
    else:
        require(body["integration"] is None, "UNTRACED_INTEGRATION")
    if image["status"] in {"generated", "failed"}:
        require(draft is not None and review is not None, "IMAGE_BEFORE_REVIEW")
        require(draft["raw_response"]["status"] in {"ok", "partial"} and review["raw_response"]["status"] in {"ok", "partial"}, "IMAGE_WITHOUT_ELIGIBLE_REVIEW")
        require(len(stages) == 6, "IMAGE_UNTRACED")
        lineage = image["lineage"]
        require(lineage["draft_identity"] == draft["raw_response"]["result"]["draft_identity"], "IMAGE_DRAFT_REVISION_MISMATCH")
        require(lineage["review_identity"] == review["raw_response"]["result"]["review_identity"], "IMAGE_REVIEW_REVISION_MISMATCH")
        require(lineage["draft_response_sha256"] == draft["content_sha256"] and lineage["review_response_sha256"] == review["content_sha256"], "IMAGE_SOURCE_HASH_MISMATCH")
        expected = fingerprint({"draft_response_sha256": draft["content_sha256"], "review_response_sha256": review["content_sha256"], "integration": body["integration"]})
        require(lineage["integration_input_sha256"] == expected, "IMAGE_INTEGRATION_HASH_MISMATCH")
        if image["status"] == "generated":
            require(stages[-1]["status"] == "ok" and stages[-1]["response_sha256"] == image["artifact"]["sha256"], "IMAGE_ARTIFACT_TRACE_MISMATCH")
        else:
            require(body["status"] == "partial" and stages[-1]["status"] == "error", "IMAGE_FAILURE_STATUS_INVALID")
    else:
        require("final_image" not in names, "SKIPPED_IMAGE_RECORDED_AS_EXECUTED")
    if body["status"] == "ok":
        require(len(stages) == 6 and all(s["status"] == "ok" for s in stages), "FALSE_OK")
    if any(s["status"] == "error" for s in stages[:-1]) or (stages[-1]["status"] == "error" and stages[-1]["stage"] != "final_image"):
        require(body["status"] == "error", "ERROR_DOWNGRADED")

    # Logical stage dependency snapshots, explicitly defined in workflow-contract.md.
    # These do not claim to be HTTP wire-request hashes (Stock has a separate sidecar).
    for stage in stages:
        name = stage["stage"]
        expected = None
        if name == "trend":
            expected = fingerprint(body["planning_request"])
        elif name == "stock" and sources["trend"] is not None:
            expected = fingerprint(sources["trend"]["raw_response"]["result"])
        elif name == "planning_draft":
            if draft is not None:
                expected = draft["raw_response"]["result"]["source_request_sha256"]
            elif sources["trend"] is not None and sources["stock"] is not None:
                # Canonical outgoing Draft request is reconstructable from retained inputs.
                t, s = sources["trend"], sources["stock"]
                wire = {"contract_version": "vogue-handoff/0.1", "request_id": rid, "input": t["raw_response"]["result"]}
                context = {"planning_request_sha256": fingerprint(body["planning_request"]), "trend_response_sha256": t["content_sha256"],
                           "stock_input_sha256": fingerprint(t["raw_response"]["result"]), "stock_wire_request_sha256": fingerprint(wire), "stock_response_sha256": s["content_sha256"]}
                # Either Draft mode (0.2.0) may have been sent; the trace must match one of them.
                candidates = {fingerprint({"contract_version": document["contract_version"], "request_id": rid,
                    "input": {"mode": mode, "user_input": body["user_input"], "planning_request": body["planning_request"],
                              "trend_response": t, "stock_response": s, "stock_call_context": context}})
                    for mode in ("draft_only", "draft_with_visuals")}
                expected = stage["input_sha256"] if stage["input_sha256"] in candidates else next(iter(sorted(candidates)))
        elif name == "sales_market_review" and draft is not None:
            expected = draft["content_sha256"]
        elif name == "integration" and draft is not None and review is not None:
            expected = fingerprint({"draft_response_sha256": draft["content_sha256"], "review_response_sha256": review["content_sha256"]})
        elif name == "final_image" and image["lineage"] is not None:
            expected = image["lineage"]["integration_input_sha256"]
        require(expected is not None and stage["input_sha256"] == expected, "STAGE_INPUT_HASH_MISMATCH")


def validators():
    registry = Registry(retrieve=lambda _: (_ for _ in ()).throw(ValueError("REMOTE_SCHEMA_DISABLED")))
    schemas = {}
    for path in sorted(CONTRACTS.rglob("*.schema.json")):
        value = read_json(path)
        Draft202012Validator.check_schema(value)
        relative = path.relative_to(CONTRACTS).as_posix()
        resource = Resource.from_contents(value)
        registry = registry.with_resource(BASE + relative, resource).with_resource(value["$id"], resource)
        schemas[relative] = value
    return {name: Draft202012Validator(value, registry=registry, format_checker=FormatChecker()) for name, value in schemas.items()}


def collect():
    """Validate every indexed fixture; return the summary without writing files."""
    checks = validators()
    index = read_json(HERE / "fixtures/index.json")
    store = FixtureStore(HERE / "fixtures")
    results = []
    for case in index["cases"]:
        document = read_json(HERE / "fixtures" / case["file"])
        errors = list(checks[case["schema"]].iter_errors(document))
        schema_ok = not errors
        semantic_ok, code = None, None
        if schema_ok:
            try:
                semantic(document, case["schema"], store)
                semantic_ok = True
            except SemanticError as exc:
                semantic_ok, code = False, str(exc)
        passed = schema_ok == case["expected_schema"] and (not schema_ok or semantic_ok == case["expected_semantic"])
        if case.get("expected_code"):
            passed = passed and code == case["expected_code"]
        results.append({"case": case["id"], "schema": case["schema"], "schema_valid": schema_ok,
                        "semantic_valid": semantic_ok, "semantic_error": code,
                        "schema_errors": [{"pointer": "/" + "/".join(map(str, e.absolute_path)), "validator": e.validator} for e in errors[:4]],
                        "expected": {"schema": case["expected_schema"], "semantic": case["expected_semantic"]}, "pass": passed})
    summary = {"scope": "offline proposed schema + reference consumer invariants; NOT current owner API compatibility",
               "schemas_checked": len(checks), "fixtures": len(results), "passed": sum(r["pass"] for r in results),
               "failed": sum(not r["pass"] for r in results), "provider_calls": 0, "service_calls": 0, "results": results}
    return summary


def main(out_dir=None):
    summary = collect()
    results = summary["results"]
    out = Path(out_dir) if out_dir else ROOT / ".local" / "contract-validation"
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "contract-validation.json", summary)
    atomic(out / "contract-validation.log", ("\n".join(("PASS " if r["pass"] else "FAIL ") + r["case"] + " schema=" + str(r["schema_valid"]) + " semantic=" + str(r["semantic_valid"]) + " code=" + str(r["semantic_error"]) for r in results) + "\n" + json.dumps({k:v for k,v in summary.items() if k != "results"}) + "\n").encode())
    print(json.dumps({k:v for k,v in summary.items() if k != "results"}))
    if summary["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
