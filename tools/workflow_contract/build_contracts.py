"""Generate proposed contracts only. Never import service entrypoints or call providers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]  # vogue-orchestrator repo root
CONTRACTS = ROOT / "contracts" / "workflow"
BASE = "https://contracts.vogue.invalid/workflow/0.1/"
TRANSPORT = "vogue-workflow/0.1"
SCHEMA = "https://json-schema.org/draft/2020-12/schema"


def atomic(path: Path, data: bytes) -> bool:
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return True


def write_json(path: Path, value) -> bool:
    return atomic(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def obj(properties, required=None, extra=False):
    return {"type": "object", "additionalProperties": extra,
            "required": list(properties) if required is None else required, "properties": properties}


def ref(name):
    return {"$ref": "common.schema.json#/$defs/" + name}


def arr(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


def nullable(value):
    return {"anyOf": [value, {"type": "null"}]}


def document(name, title, body):
    return {"$schema": SCHEMA, "$id": BASE + name, "title": title,
            "$comment": "PROPOSED workflow-contract/0.1.1: structural validation plus versioned semantic rules are required; see decisions/contract-baseline.json. Not service compatibility.", **body}


def producer(agent, version):
    return obj({"agent_id": {"const": agent}, "schema_version": {"const": version}})


def envelope(agent, version, body, contract=TRANSPORT):
    return obj({"contract_version": {"const": contract}, "request_id": ref("id"),
                "producer": producer(agent, version), "status": ref("status"), "result": body})


def retained(response):
    return obj({"raw_response": response, "content_sha256": ref("sha256"),
                "hash_profile": {"const": "python-json-sort/1"},
                "raw_artifact": ref("json_artifact"), "observation": ref("observation")})


def condition(statuses, properties):
    return {"if": {"properties": {"status": {"enum": statuses}}, "required": ["status"]},
            "then": {"properties": properties}}


def main():
    sys.path.insert(0, str(ROOT.parent / "trend-analysis-agent/src"))
    from trend_agent.schemas.trend_analysis_result import TrendAnalysisResult

    # Snapshot an actual producer model, without changing its fields/defaults.
    trend = TrendAnalysisResult.model_json_schema()
    trend["$schema"] = SCHEMA
    trend["$id"] = BASE + "upstream/trend-result-1.1.schema.json"
    trend["$comment"] = "Actual Pydantic export; outer wrapper additionally pins schema_version=1.1."
    write_json(CONTRACTS / "upstream/trend-result-1.1.schema.json", trend)
    stock_path = ROOT.parent / "stock-agent/contracts/schemas/stock-agent-result.schema.json"
    atomic(CONTRACTS / "upstream/stock-result-1.0.schema.json", stock_path.read_bytes())

    defs = {}
    defs["id"] = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]*$"}
    defs["sha256"] = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
    defs["status"] = {"enum": ["ok", "partial", "no_results", "error"]}
    defs["timestamp"] = {"type": "string", "format": "date-time"}
    defs["strings"] = arr({"type": "string", "minLength": 1})
    defs["artifact"] = obj({
        "artifact_id": ref("id"),
        "uri": {"type": "string", "pattern": "^artifact://[A-Za-z0-9_./-]+$"},
        "sha256": ref("sha256"), "media_type": {"type": "string", "minLength": 1},
        "size_bytes": {"type": "integer", "minimum": 1}, "immutable": {"const": True},
    })
    defs["json_artifact"] = {"allOf": [ref("artifact"), {"properties": {"media_type": {"const": "application/json"}}}]}
    defs["image_artifact"] = {"allOf": [ref("artifact"), {"properties": {
        "media_type": {"enum": ["image/png", "image/jpeg", "image/webp"]},
        "size_bytes": {"type": "integer", "minimum": 1, "maximum": 10_485_760},
    }}]}
    defs["observation"] = obj({
        "observed_at": ref("timestamp"), "as_of": nullable({"type": "string", "minLength": 1}),
        "synthetic": {"type": ["boolean", "null"]},
        "data_nature": {"enum": ["current_internal", "historical_external", "sample", "synthetic_fixture", "mixed", "unknown", "user_provided"]},
        "source": {"type": "string", "minLength": 1}, "limitations": ref("strings"),
    })
    defs["error"] = obj({
        "code": {"type": "string", "minLength": 1}, "message": {"type": "string", "minLength": 1},
        "stage": {"enum": ["input", "trend", "stock", "planning_draft", "sales_market_review", "integration", "final_image"]},
        "fatal": {"type": "boolean"}, "retryable": {"type": "boolean"},
    })
    defs["problem"] = obj({"contract_version": {"const": TRANSPORT}, "request_id": nullable(ref("id")),
                           "status": {"const": "error"}, "error": ref("error")})
    defs["evidence_ref"] = obj({"evidence_id": {"type": "string", "minLength": 1},
                                "source_response_sha256": ref("sha256"), "source_pointer": {"type": "string", "pattern": "^/"}})
    defs["user_input"] = obj({
        "text": {"type": "string", "minLength": 1, "maxLength": 8000, "pattern": "\\S"},
        "images": {"type": "array", "maxItems": 1, "items": ref("image_artifact")},
    })
    defs["planning_request"] = obj({"schema_version": {"const": "1.0"}, "request_id": ref("id"),
                                    "query": {"type": "string", "minLength": 1, "maxLength": 8000, "pattern": "\\S"}}, extra=True)
    trend_result = {"allOf": [{"$ref": "upstream/trend-result-1.1.schema.json"},
                              {"required": ["schema_version", "applied_filters", "trends", "evidence", "limitations"],
                               "properties": {"schema_version": {"const": "1.1"}}}]}
    defs["trend_response"] = envelope("trend-analysis-agent", "1.1", trend_result, "vogue-handoff/0.1")
    # Trend producer does not emit partial, even though its HTTP handler accepts it.
    defs["trend_response"]["properties"]["status"] = {"enum": ["ok", "no_results", "error"]}
    defs["trend_response"]["allOf"] = [condition(["ok"], {"result": {"properties": {"trends": {"minItems": 1}, "evidence": {"minItems": 1}}}})]
    stock_result = {"allOf": [{"$ref": "upstream/stock-result-1.0.schema.json"},
                              {"if": {"properties": {"status": {"enum": ["ok", "partial", "no_results"]}}},
                               "then": {"required": ["input_provenance"]}}]}
    defs["stock_response"] = envelope("stock-agent", "1.0", stock_result, "vogue-handoff/0.1")
    defs["retained_trend"] = retained(ref("trend_response"))
    defs["retained_stock"] = retained(ref("stock_response"))
    defs["draft_identity"] = obj({"draft_id": ref("id"), "revision": {"type": "integer", "minimum": 1}, "content_sha256": ref("sha256")})
    defs["review_identity"] = obj({"review_id": ref("id"), "revision": {"type": "integer", "minimum": 1}, "content_sha256": ref("sha256")})
    defs["claim"] = obj({"kind": {"enum": ["fact", "interpretation", "proposal"]},
                         "text": {"type": "string", "minLength": 1}, "evidence_refs": arr(ref("evidence_ref"))})
    defs["draft"] = obj({
        "title": {"type": "string", "minLength": 1}, "category": nullable({"type": "string", "minLength": 1}),
        "season": nullable({"type": "string", "minLength": 1}),
        "claims": arr(ref("claim"), 1), "open_questions": ref("strings"),
        "md_decision_required": {"const": ["price", "cost", "quantity", "purchase_order", "adoption"]},
        "limitations": ref("strings"),
    })
    defs["stock_call_context"] = obj({
        "planning_request_sha256": ref("sha256"), "trend_response_sha256": ref("sha256"),
        "stock_input_sha256": ref("sha256"), "stock_wire_request_sha256": ref("sha256"),
        "stock_response_sha256": ref("sha256"),
    })

    draft_request = document("planning-draft-request.schema.json", "P08 → P06 Draft-only request", obj({
        "contract_version": {"const": TRANSPORT}, "request_id": ref("id"),
        "input": obj({"mode": {"const": "draft_only"}, "user_input": ref("user_input"),
                      "planning_request": ref("planning_request"), "trend_response": ref("retained_trend"),
                      "stock_response": ref("retained_stock"), "stock_call_context": ref("stock_call_context")}),
    }))
    draft_body = obj({
        "schema_version": {"const": "planning-draft/1.0"}, "agent_id": {"const": "new-product-agent"},
        "request_id": ref("id"), "status": ref("status"), "draft_identity": nullable(ref("draft_identity")),
        "draft": nullable(ref("draft")), "source_request": {"$ref": "planning-draft-request.schema.json"},
        "source_request_sha256": ref("sha256"), "limitations": ref("strings"), "errors": arr(ref("error")),
        "artifacts": arr(ref("json_artifact")), "image_generation_calls": {"const": 0}, "review_calls": {"const": 0},
    })
    draft_body["allOf"] = [condition(["ok", "partial"], {"draft_identity": ref("draft_identity"), "draft": ref("draft")}),
                            condition(["no_results", "error"], {"draft_identity": {"type": "null"}, "draft": {"type": "null"}}),
                            condition(["error"], {"errors": arr(ref("error"), 1)}),
                            condition(["no_results"], {"limitations": {"type": "array", "minItems": 1}})]
    draft_response = document("planning-draft-response.schema.json", "P06 → P08 PlanningDraft envelope", envelope("new-product-agent", "planning-draft/1.0", draft_body))
    defs["retained_draft"] = retained({"$ref": "planning-draft-response.schema.json"})
    defs["derived_view"] = obj({
        "purpose": {"const": "search_only"}, "adapter_version": {"type": "string", "minLength": 1},
        "source_response_sha256": ref("sha256"), "source_pointers": arr({"type": "string", "pattern": "^/"}, 1),
        "product": obj({"category": nullable({"type": "string"}), "season": nullable({"type": "string"})}),
    })
    review_request = document("sales-market-review-request.schema.json", "P08 → P07 full-Draft direct review", obj({
        "contract_version": {"const": TRANSPORT}, "request_id": ref("id"),
        "input": obj({"planning_draft": ref("retained_draft"), "derived_view": nullable(ref("derived_view"))}),
    }))
    defs["metric"] = obj({
        "name": {"type": "string", "minLength": 1}, "value": nullable({"type": "number"}),
        "missing_reason": nullable({"type": "string", "minLength": 1}),
        "unit": {"type": "string", "minLength": 1}, "currency": nullable({"type": "string", "pattern": "^[A-Z]{3}$"}),
        "period": nullable({"type": "string", "minLength": 1}),
        "comparability": {"enum": ["comparable", "not_comparable", "unknown"]},
        "observation": ref("observation"), "evidence_refs": arr(ref("evidence_ref")),
    })
    defs["metric"]["allOf"] = [
        {"if": {"properties": {"value": {"type": "null"}}}, "then": {"properties": {"missing_reason": {"type": "string", "minLength": 1}}},
         "else": {"properties": {"missing_reason": {"type": "null"}, "evidence_refs": {"minItems": 1}}}}
    ]
    defs["review"] = obj({
        "reviewed_draft": ref("draft_identity"), "draft_response_sha256": ref("sha256"),
        "findings": arr(ref("claim")), "evidence": arr({"type": "object"}),
        "metrics": arr(ref("metric")), "limitations": ref("strings"),
        "observation": ref("observation"),
    })
    review_body = obj({
        "schema_version": {"const": "sales-market-review/1.0"}, "agent_id": {"const": "sales-market-agent"},
        "request_id": ref("id"), "status": ref("status"),
        "review_identity": ref("review_identity"), "review": ref("review"),
        "limitations": ref("strings"), "errors": arr(ref("error")), "artifacts": arr(ref("artifact")),
    })
    review_body["allOf"] = [condition(["error"], {"errors": arr(ref("error"), 1)}),
                             condition(["no_results"], {"limitations": {"minItems": 1}, "review": {"properties": {"findings": {"maxItems": 0}, "metrics": {"items": {"properties": {"value": {"type": "null"}}}}}}})]
    review_response = document("sales-market-review-response.schema.json", "P07 → P08 SalesMarketReview envelope", envelope("sales-market-agent", "sales-market-review/1.0", review_body))
    defs["retained_review"] = retained({"$ref": "sales-market-review-response.schema.json"})
    defs["image_lineage"] = obj({
        "draft_identity": ref("draft_identity"), "draft_response_sha256": ref("sha256"),
        "review_identity": ref("review_identity"), "review_response_sha256": ref("sha256"),
        "integration_input_sha256": ref("sha256"),
    })
    defs["final_image"] = obj({
        "status": {"enum": ["generated", "failed", "skipped"]}, "artifact": nullable(ref("image_artifact")),
        "lineage": nullable(ref("image_lineage")), "reason": nullable({"type": "string", "minLength": 1}),
        "provider_calls": {"type": "integer", "minimum": 0, "maximum": 1},
    })
    defs["final_image"]["allOf"] = [condition(["generated"], {"artifact": ref("image_artifact"), "lineage": ref("image_lineage"), "reason": {"type": "null"}, "provider_calls": {"const": 1}}),
                                  condition(["failed"], {"artifact": {"type": "null"}, "lineage": ref("image_lineage"), "reason": {"type": "string", "minLength": 1}, "provider_calls": {"const": 1}}),
                                  condition(["skipped"], {"artifact": {"type": "null"}, "lineage": {"type": "null"}, "reason": {"type": "string", "minLength": 1}, "provider_calls": {"const": 0}})]
    defs["stage"] = obj({
        "sequence": {"type": "integer", "minimum": 1, "maximum": 6},
        "stage": {"enum": ["trend", "stock", "planning_draft", "sales_market_review", "integration", "final_image"]},
        "request_id": ref("id"), "status": ref("status"), "input_sha256": ref("sha256"),
        "response_sha256": nullable(ref("sha256")), "started_at": ref("timestamp"), "completed_at": ref("timestamp"),
        "errors": arr(ref("error")),
    })
    integrated_body = obj({
        "schema_version": {"const": "integrated-result/1.0"}, "agent_id": {"const": "vogue-orchestrator"},
        "request_id": ref("id"), "status": ref("status"),
        "user_input": ref("user_input"), "planning_request": ref("planning_request"),
        "sources": obj({"trend": nullable(ref("retained_trend")), "stock": nullable(ref("retained_stock")),
                        "planning_draft": nullable(ref("retained_draft")), "sales_market_review": nullable(ref("retained_review"))}),
        "stages": arr(ref("stage"), 1), "integration": nullable(obj({"claims": arr(ref("claim"), 1), "limitations": ref("strings")})),
        "final_image": ref("final_image"), "limitations": ref("strings"), "errors": arr(ref("error")),
        "rejected_response_artifacts": arr(ref("artifact")),
        "md_review": obj({"status": {"const": "pending"}, "choices": {"const": ["adopt", "revise", "hold"]},
                          "reserved_decisions": {"const": ["price", "cost", "quantity", "purchase_order", "adoption"]}}),
    })
    integrated_body["allOf"] = [condition(["error", "no_results"], {"final_image": {"properties": {"status": {"const": "skipped"}}}}),
                               condition(["error"], {"errors": arr(ref("error"), 1)}),
                               condition(["ok"], {"final_image": {"properties": {"status": {"const": "generated"}}}})]
    integrated = document("integrated-result.schema.json", "P08 integrated result; MD decision pending", envelope("vogue-orchestrator", "integrated-result/1.0", integrated_body))
    common = document("common.schema.json", "Shared definitions; equality/hash/order require semantic validator", {"$defs": defs})
    for name, value in [
        ("common.schema.json", common), ("planning-draft-request.schema.json", draft_request),
        ("planning-draft-response.schema.json", draft_response), ("sales-market-review-request.schema.json", review_request),
        ("sales-market-review-response.schema.json", review_response), ("integrated-result.schema.json", integrated),
    ]:
        write_json(CONTRACTS / name, value)
    print(json.dumps({"schemas_written_or_unchanged": 8, "state": "proposed", "services_called": 0}))


if __name__ == "__main__":
    main()
