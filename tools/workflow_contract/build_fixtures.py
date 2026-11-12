"""Synthetic contract documents. Counts in fixture traces are expectations, not live calls."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import struct
import sys
import zlib
from pathlib import Path

from build_contracts import ROOT, TRANSPORT, atomic, write_json
from validate_contracts import fingerprint

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
RID = "fixture:workflow:001"
STAMP = "2026-11-12T06:00:00Z"
MD = ["price", "cost", "quantity", "purchase_order", "adoption"]
ARTIFACTS = {}
CASES = []


def blob(data, mime="application/json", extension="json"):
    digest = hashlib.sha256(data).hexdigest()
    uri = f"artifact://fixtures/raw/{digest}.{extension}"
    path = f"raw/{digest}.{extension}"
    atomic(FIXTURES / path, data)
    ARTIFACTS[uri] = {"path": path, "media_type": mime}
    return {"artifact_id": "fixture:" + digest, "uri": uri, "sha256": digest,
            "media_type": mime, "size_bytes": len(data), "immutable": True}


def observation(kind="synthetic_fixture", as_of=None):
    return {"observed_at": STAMP, "as_of": as_of, "synthetic": True, "data_nature": kind,
            "source": "coordinator synthetic fixture; no real company data", "limitations": ["Fixture only; not current inventory or actual sales."]}


def retain(response, kind="synthetic_fixture", as_of=None):
    data = (json.dumps(response, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    return {"raw_response": copy.deepcopy(response), "content_sha256": fingerprint(response),
            "hash_profile": "python-json-sort/1", "raw_artifact": blob(data), "observation": observation(kind, as_of)}


def env(agent, version, result, contract=TRANSPORT):
    return {"contract_version": contract, "request_id": RID, "producer": {"agent_id": agent, "schema_version": version},
            "status": result["status"], "result": result}


def error(stage, code="FIXTURE_SOURCE_UNAVAILABLE", fatal=True):
    return {"code": code, "message": "Synthetic failure; no provider was contacted.", "stage": stage, "fatal": fatal, "retryable": False}


def case(case_id, schema, value, schema_ok=True, semantic_ok=True, code=None):
    name = case_id + ".json"
    write_json(FIXTURES / name, value)
    entry = {"id": case_id, "file": name, "schema": schema + ".schema.json", "expected_schema": schema_ok,
             "expected_semantic": semantic_ok if schema_ok else None}
    if code:
        entry["expected_code"] = code
    CASES.append(entry)


def make_trend(status="ok"):
    sys.path.insert(0, str(ROOT.parent / "trend-analysis-agent/src"))
    from trend_agent.schemas.trend_analysis_result import TrendAnalysisResult, evidence_id
    eid = evidence_id("fixture:article", 0, "합성 테스트 문장")
    payload = {"schema_version": "1.1", "request_id": RID, "status": status,
               "applied_filters": {"season": {"year": 2027, "season": "SS", "origin": "user_requested"}, "categories": {"include": [], "exclude": []}, "gender": None, "attributes": []},
               "trends": [], "evidence": [], "limitations": ["Synthetic fixture article, not an external citation."]}
    if status == "ok":
        payload["trends"] = [{"style": {"id": "fixture:style", "name": "합성 셔츠", "name_origin": "analyst", "definition": "Fixture style", "seasons": ["2027SS"], "focus_evidence_ids": [eid]},
                              "items": [{"id": "fixture:item", "name": "테스트 셔츠", "category_major": "상의", "category_minor": "셔츠", "evidence_ids": [eid]}]}]
        payload["evidence"] = [{"evidence_id": eid, "article_id": "fixture:article", "title": "Fixture article", "url": None,
                                "published_at": None, "block_idx": 0, "quote": "합성 테스트 문장"}]
    # Use the actual current producer serializer, not a reconstructed approximation.
    actual = TrendAnalysisResult.model_validate(payload).model_dump(mode="json")
    return retain(env("trend-analysis-agent", "1.1", actual, "vogue-handoff/0.1"))


def make_stock(trend, status="ok"):
    evidence = {"evidence_id": "stock-agent:fixture:sample", "style_id": "fixture:style", "source_id": "fixture:stock",
                "source_type": "synthetic_fixture", "source_name": "Fixture sample inventory", "locator": None,
                "published_at": None, "observed_at": "2020-09-22", "fetched_at": STAMP, "permission_status": "synthetic_test_only",
                "manifest_id": "fixture:manifest", "data_nature": "sample"}
    product = {"style_id": "fixture:style", "sku_ids": ["fixture:sku"], "category": "셔츠", "season": "2027SS",
               "attributes": {"color": "화이트", "material": ["면"], "fit": "노멀"},
               "inventory": {"quantity": 7, "unit": "pieces", "as_of": "2020-09-22", "definition": "Synthetic test quantity only", "data_nature": "sample"}, "image": None}
    tr = {"style": {"id": "fixture:style", "name": "합성 셔츠"}, "status": "ok", "product_index": 0, "rank": None,
          "reason": None, "summary": "Synthetic stock match", "attribute_summary": None, "search": {},
          "limitations": ["Sample inventory as of 2020-09-22; not current company stock."], "errors": [], "evidence_ids": [evidence["evidence_id"]]}
    payload = {"schema_version": "1.0", "request_id": RID, "agent_id": "stock-agent", "status": status,
               "summary": "Synthetic fixture only", "products": [], "trend_results": [], "season_basis": None, "evidence": [], "errors": [],
               "input_provenance": {"source_agent_id": "trend-analysis-agent", "source_schema_version": "1.1", "internal_schema_version": None,
                                    "adapter_version": "stock-trend-input-adapter/1.0", "transformed": True,
                                    "source_payload_sha256": fingerprint(trend["raw_response"]["result"])}}
    if status in {"ok", "partial"}:
        payload.update(products=[product], trend_results=[tr], evidence=[evidence])
        if status == "partial":
            absent = {**copy.deepcopy(tr), "style": {"id": "fixture:style:empty", "name": "빈 합성 트렌드"}, "status": "no_results", "product_index": 1, "evidence_ids": [], "limitations": ["No eligible product; null slot is not zero stock."]}
            payload["products"].append(None)
            payload["trend_results"].append(absent)
    if status == "error":
        payload["errors"] = [{"code": "SOURCE_UNAVAILABLE", "message": "Synthetic unavailable source", "reason": "fixture"}]
    return retain(env("stock-agent", "1.0", payload, "vogue-handoff/0.1"), "sample" if status in {"ok", "partial"} else "synthetic_fixture", "2020-09-22" if status in {"ok", "partial"} else None)


def make_request(trend=None, stock_status="ok", with_image=False):
    trend = trend or make_trend()
    stock = make_stock(trend, stock_status)
    images = []
    if with_image:
        images.append(image_fixture())
    user = {"text": "2027SS 합성 셔츠 검토용 초안", "images": images}
    planning = {"schema_version": "1.0", "request_id": RID, "query": user["text"], "context": {"category": "셔츠", "season": "2027SS"}}
    wire_request = {"contract_version": "vogue-handoff/0.1", "request_id": RID, "input": trend["raw_response"]["result"]}
    return {"contract_version": TRANSPORT, "request_id": RID,
            "input": {"mode": "draft_only", "user_input": user,
                      "planning_request": planning, "trend_response": trend, "stock_response": stock,
                      "stock_call_context": {"planning_request_sha256": fingerprint(planning), "trend_response_sha256": trend["content_sha256"],
                                             "stock_input_sha256": fingerprint(trend["raw_response"]["result"]), "stock_wire_request_sha256": fingerprint(wire_request),
                                             "stock_response_sha256": stock["content_sha256"]}}}


def evidence_ref(request):
    t = request["input"]["trend_response"]
    return {"evidence_id": t["raw_response"]["result"]["evidence"][0]["evidence_id"],
            "source_response_sha256": t["content_sha256"], "source_pointer": "/result/evidence/0"}


def make_draft(request, status="ok"):
    draft = {"title": "합성 검토용 셔츠 초안", "category": "셔츠", "season": "2027SS",
             "claims": [{"kind": "fact", "text": "Fixture evidence contains a test item.", "evidence_refs": [evidence_ref(request)]},
                        {"kind": "proposal", "text": "MD 검토용 합성 디자인 제안", "evidence_refs": []}],
             "open_questions": ["현재 자사 재고와 판매 근거는 미확인"], "md_decision_required": MD,
             "limitations": ["Synthetic fixture; price, cost and order quantity remain MD decisions."]}
    body = {"schema_version": "planning-draft/1.0", "agent_id": "new-product-agent", "request_id": RID, "status": status,
            "draft_identity": {"draft_id": "fixture:draft:001", "revision": 1, "content_sha256": fingerprint(draft)}, "draft": draft,
            "source_request": copy.deepcopy(request), "source_request_sha256": fingerprint(request),
            "limitations": ["Synthetic contract fixture only."], "errors": [], "artifacts": [], "image_generation_calls": 0, "review_calls": 0}
    if status in {"no_results", "error"}:
        body.update(draft_identity=None, draft=None)
    if status == "error":
        body["errors"] = [error("planning_draft")]
    return retain(env("new-product-agent", "planning-draft/1.0", body))


def make_review_request(draft, derived=False):
    view = None
    if derived:
        view = {"purpose": "search_only", "adapter_version": "draft-search-view/1.0", "source_response_sha256": draft["content_sha256"],
                "source_pointers": ["/result/draft/category", "/result/draft/season"], "product": {"category": "셔츠", "season": "2027SS"}}
    return {"contract_version": TRANSPORT, "request_id": RID, "input": {"planning_draft": copy.deepcopy(draft), "derived_view": view}}


def make_review(draft, status="ok"):
    body_draft = draft["raw_response"]["result"]
    review = {"reviewed_draft": copy.deepcopy(body_draft["draft_identity"]), "draft_response_sha256": draft["content_sha256"],
              "findings": [{"kind": "interpretation", "text": "Fixture-only qualitative assessment, not a sales forecast.", "evidence_refs": [evidence_ref(body_draft["source_request"])]}],
              "evidence": [], "metrics": [{"name": "current_company_sales", "value": None, "missing_reason": "No current company sales source in this fixture.",
                                          "unit": "pieces", "currency": None, "period": None, "comparability": "unknown", "observation": observation(), "evidence_refs": []}],
              "limitations": ["Historical/sample sources cannot establish current sales."], "observation": observation()}
    if status in {"no_results", "error"}:
        review["findings"] = []
    result = {"schema_version": "sales-market-review/1.0", "agent_id": "sales-market-agent", "request_id": RID, "status": status,
              "review_identity": {"review_id": "fixture:review:001", "revision": 1, "content_sha256": fingerprint(review)}, "review": review,
              "limitations": ["Synthetic fixture only; not current market validation."], "errors": [error("sales_market_review")] if status == "error" else [], "artifacts": []}
    return retain(env("sales-market-agent", "sales-market-review/1.0", result))


def image_fixture():
    # Fixed one-pixel RGBA PNG with computed CRCs; no model/provider is called.
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(b"\0\xff\xff\xff\xff")) + chunk(b"IEND", b"")
    return blob(data, "image/png", "png")


def make_integrated(request, draft=None, review=None, status="ok", image_status="generated", stop=None):
    sources = {"trend": copy.deepcopy(request["input"]["trend_response"]), "stock": copy.deepcopy(request["input"]["stock_response"]),
               "planning_draft": copy.deepcopy(draft), "sales_market_review": copy.deepcopy(review)}
    integration = {"claims": [{"kind": "proposal", "text": "Synthetic integrated planning draft for MD review.", "evidence_refs": []}], "limitations": ["Fixture only."]}
    image = {"status": "skipped", "artifact": None, "lineage": None, "reason": "No eligible reviewed draft; no generation.", "provider_calls": 0}
    if image_status in {"generated", "failed"}:
        image = {"status": image_status, "artifact": image_fixture() if image_status == "generated" else None,
                 "lineage": {"draft_identity": copy.deepcopy(draft["raw_response"]["result"]["draft_identity"]),
                             "draft_response_sha256": draft["content_sha256"], "review_identity": copy.deepcopy(review["raw_response"]["result"]["review_identity"]),
                             "review_response_sha256": review["content_sha256"],
                             "integration_input_sha256": fingerprint({"draft_response_sha256": draft["content_sha256"], "review_response_sha256": review["content_sha256"], "integration": integration})},
                 "reason": None if image_status == "generated" else "Synthetic renderer failure", "provider_calls": 1}
    names = ["trend", "stock", "planning_draft", "sales_market_review", "integration", "final_image"]
    if stop:
        names = names[:names.index(stop) + 1]
        for name in list(sources):
            if name not in names:
                sources[name] = None
        if stop not in {"integration", "final_image"}:
            integration = None
    elif image_status == "skipped":
        names = names[:-1]
    stages = []
    dependency_hashes = {
        "trend": fingerprint(request["input"]["planning_request"]),
        "stock": fingerprint(sources["trend"]["raw_response"]["result"]),
        "planning_draft": fingerprint(request),
        "sales_market_review": draft["content_sha256"] if draft else None,
        "integration": fingerprint({"draft_response_sha256": draft["content_sha256"], "review_response_sha256": review["content_sha256"]}) if draft and review else None,
        "final_image": image["lineage"]["integration_input_sha256"] if image["lineage"] else None,
    }
    for i, name in enumerate(names):
        source = sources.get(name)
        stage_status = source["raw_response"]["status"] if source else "ok"
        response_sha = source["content_sha256"] if source else fingerprint(integration)
        if name == "final_image":
            stage_status = "ok" if image_status == "generated" else "error"
            response_sha = image["artifact"]["sha256"] if image["artifact"] else None
        if name == "integration" and status == "partial":
            stage_status = "partial"
        stages.append({"sequence": i + 1, "stage": name, "request_id": RID, "status": stage_status,
                       "input_sha256": dependency_hashes[name], "response_sha256": response_sha,
                       "started_at": f"2026-11-12T06:00:{i*2:02d}Z", "completed_at": f"2026-11-12T06:00:{i*2+1:02d}Z",
                       "errors": [error(name)] if stage_status == "error" else []})
    body = {"schema_version": "integrated-result/1.0", "agent_id": "vogue-orchestrator", "request_id": RID, "status": status,
            "user_input": copy.deepcopy(request["input"]["user_input"]), "planning_request": copy.deepcopy(request["input"]["planning_request"]),
            "sources": sources, "stages": stages, "integration": integration, "final_image": image, "limitations": ["Synthetic expected trace; actual provider calls = 0."],
            "errors": [error(stop or "final_image", fatal=status == "error")] if status == "error" or image_status == "failed" else [], "rejected_response_artifacts": [],
            "md_review": {"status": "pending", "choices": ["adopt", "revise", "hold"], "reserved_decisions": MD}}
    return env("vogue-orchestrator", "integrated-result/1.0", body)


def main():
    request = make_request()
    draft = make_draft(request)
    review = make_review(draft)
    integrated = make_integrated(request, draft, review)
    case("draft-request-text-ok", "planning-draft-request", request)
    case("draft-request-image-ok", "planning-draft-request", make_request(with_image=True))
    partial_request = make_request(stock_status="partial")
    partial_draft = make_draft(partial_request, "partial")
    partial_review = make_review(partial_draft, "partial")
    case("draft-request-stock-partial-null-slot", "planning-draft-request", partial_request)
    case("draft-request-stock-no-results", "planning-draft-request", make_request(stock_status="no_results"))
    for status in ["ok", "partial", "no_results", "error"]:
        case("draft-response-" + status, "planning-draft-response", make_draft(request, status)["raw_response"])
        case("review-response-" + status, "sales-market-review-response", make_review(draft, status)["raw_response"])
    case("review-request-full-draft", "sales-market-review-request", make_review_request(draft))
    case("review-request-derived-view", "sales-market-review-request", make_review_request(draft, True))
    case("integrated-ok", "integrated-result", integrated)
    case("integrated-stock-partial", "integrated-result", make_integrated(partial_request, partial_draft, partial_review, "partial"))
    empty_trend_request = make_request(trend=make_trend("no_results"), stock_status="no_results")
    case("integrated-trend-no-results", "integrated-result", make_integrated(empty_trend_request, status="no_results", image_status="skipped", stop="trend"))
    empty_draft = make_draft(request, "no_results")
    case("integrated-draft-no-results", "integrated-result", make_integrated(request, empty_draft, status="no_results", image_status="skipped", stop="planning_draft"))
    case("integrated-review-no-results", "integrated-result", make_integrated(request, draft, make_review(draft, "no_results"), "partial", "skipped"))
    case("integrated-review-error", "integrated-result", make_integrated(request, draft, make_review(draft, "error"), "error", "skipped", "sales_market_review"))
    case("integrated-image-failed", "integrated-result", make_integrated(request, draft, review, "partial", "failed"))
    auth_error = make_integrated(request, status="error", image_status="skipped", stop="stock")
    auth_error["result"]["sources"]["stock"] = None
    auth_error["result"]["stages"][-1].update(status="error", response_sha256=None, errors=[error("stock", "UNAUTHORIZED")])
    auth_error["result"]["errors"] = [error("stock", "UNAUTHORIZED")]
    auth_error["result"]["rejected_response_artifacts"] = [blob(b'{"error":{"code":"UNAUTHORIZED"}}\n')]
    case("integrated-auth-error", "integrated-result", auth_error)
    bad = copy.deepcopy(auth_error); bad["status"] = bad["result"]["status"] = "partial"
    case("bad-error-downgraded", "integrated-result", bad, semantic_ok=False, code="FATAL_ERROR_HIDDEN")
    zero = copy.deepcopy(request)
    zero_stock = copy.deepcopy(zero["input"]["stock_response"]["raw_response"])
    zero_stock["result"]["products"][0]["inventory"]["quantity"] = 0
    zero["input"]["stock_response"] = retain(zero_stock, "sample", "2020-09-22")
    zero["input"]["stock_call_context"]["stock_response_sha256"] = zero["input"]["stock_response"]["content_sha256"]
    case("draft-request-observed-zero-fixture", "planning-draft-request", zero)
    bad = copy.deepcopy(request)
    bad_stock = copy.deepcopy(bad["input"]["stock_response"]["raw_response"])
    bad_stock["result"]["trend_results"] = []
    bad["input"]["stock_response"] = retain(bad_stock, "sample", "2020-09-22")
    case("bad-stock-slot-loss", "planning-draft-request", bad, semantic_ok=False, code="STOCK_SLOT_LENGTH_MISMATCH")
    bad = copy.deepcopy(request)
    bad_stock = copy.deepcopy(bad["input"]["stock_response"]["raw_response"])
    bad_stock["result"]["input_provenance"]["source_payload_sha256"] = "0" * 64
    bad["input"]["stock_response"] = retain(bad_stock, "sample", "2020-09-22")
    case("bad-stock-upstream-hash", "planning-draft-request", bad, semantic_ok=False, code="STOCK_UPSTREAM_HASH_MISMATCH")

    bad = copy.deepcopy(request); bad["input"]["planning_request"]["request_id"] = "wrong:request"
    case("bad-request-id", "planning-draft-request", bad, semantic_ok=False, code="REQUEST_ID_MISMATCH")
    bad = copy.deepcopy(request); bad["contract_version"] = "vogue-workflow/99.0"
    case("bad-transport-version", "planning-draft-request", bad, schema_ok=False)
    bad = copy.deepcopy(request); bad["input"]["trend_response"]["raw_response"]["producer"]["schema_version"] = "1.0"
    case("bad-upstream-producer-version", "planning-draft-request", bad, schema_ok=False)
    bad = copy.deepcopy(request); bad["input"]["stock_response"]["raw_response"]["producer"]["agent_id"] = "wrong-agent"
    case("bad-upstream-producer-id", "planning-draft-request", bad, schema_ok=False)
    bad = copy.deepcopy(request); bad["input"]["stock_response"]["content_sha256"] = "0" * 64
    case("bad-source-content-hash", "planning-draft-request", bad, semantic_ok=False, code="RESPONSE_HASH_MISMATCH")
    bad = copy.deepcopy(request); bad["input"]["trend_response"]["raw_artifact"]["sha256"] = "0" * 64
    case("bad-source-artifact-hash", "planning-draft-request", bad, semantic_ok=False, code="ARTIFACT_HASH_MISMATCH")
    bad = copy.deepcopy(request); bad["input"]["stock_response"]["observation"]["synthetic"] = False
    case("bad-sample-marked-real", "planning-draft-request", bad, semantic_ok=False, code="SAMPLE_PROVENANCE_LOST")
    bad = copy.deepcopy(request); bad["input"]["stock_response"]["observation"]["as_of"] = "2026-11-12"
    case("bad-as-of-rewritten", "planning-draft-request", bad, semantic_ok=False, code="AS_OF_MISMATCH")
    case("bad-upstream-error-continued", "planning-draft-request", make_request(stock_status="error"), semantic_ok=False, code="UPSTREAM_STOP_REQUIRED")
    case("bad-trend-no-results-continued", "planning-draft-request", empty_trend_request, semantic_ok=False, code="UPSTREAM_STOP_REQUIRED")
    bad = make_request(with_image=True); bad["input"]["user_input"]["images"][0]["media_type"] = "image/gif"
    case("bad-input-image-mime", "planning-draft-request", bad, schema_ok=False)
    bad = make_request(with_image=True); bad["input"]["user_input"]["images"][0]["size_bytes"] = 10_485_761
    case("bad-input-image-size", "planning-draft-request", bad, schema_ok=False)
    bad = copy.deepcopy(draft["raw_response"]); bad["result"]["image_generation_calls"] = 1
    case("bad-draft-image-call", "planning-draft-response", bad, schema_ok=False)
    bad = copy.deepcopy(draft["raw_response"]); bad["result"]["draft_identity"]["content_sha256"] = "0" * 64
    case("bad-draft-content-hash", "planning-draft-response", bad, semantic_ok=False, code="DRAFT_CONTENT_HASH_MISMATCH")
    bad = copy.deepcopy(draft["raw_response"]); bad["result"]["draft"]["claims"][0]["evidence_refs"][0]["evidence_id"] = "missing:evidence"
    bad["result"]["draft_identity"]["content_sha256"] = fingerprint(bad["result"]["draft"])
    case("bad-evidence-reference", "planning-draft-response", bad, semantic_ok=False, code="EVIDENCE_ID_MISMATCH")
    bad = make_review_request(draft, True); bad["input"]["derived_view"]["product"]["category"] = "코트"
    case("bad-derived-view-substitution", "sales-market-review-request", bad, semantic_ok=False, code="DERIVED_VIEW_NOT_SOURCE")
    case("bad-review-empty-draft", "sales-market-review-request", make_review_request(empty_draft), semantic_ok=False, code="UNREVIEWABLE_DRAFT")
    bad = copy.deepcopy(review["raw_response"]); bad["result"]["review"]["reviewed_draft"]["revision"] = 2
    bad["result"]["review_identity"]["content_sha256"] = fingerprint(bad["result"]["review"])
    case("bad-review-draft-revision", "sales-market-review-response", bad, semantic_ok=False, code="DRAFT_REVISION_MISMATCH")
    bad = make_review(draft, "no_results")["raw_response"]; bad["result"]["review"]["metrics"][0]["value"] = 0
    case("bad-no-results-as-zero", "sales-market-review-response", bad, schema_ok=False)
    bad = copy.deepcopy(integrated); bad["result"]["final_image"]["lineage"]["draft_identity"]["revision"] = 2
    case("bad-image-draft-revision", "integrated-result", bad, semantic_ok=False, code="IMAGE_DRAFT_REVISION_MISMATCH")
    bad = copy.deepcopy(integrated); bad["result"]["final_image"]["lineage"]["review_response_sha256"] = "0" * 64
    case("bad-image-review-hash", "integrated-result", bad, semantic_ok=False, code="IMAGE_SOURCE_HASH_MISMATCH")
    bad = copy.deepcopy(integrated); bad["result"]["stages"][2], bad["result"]["stages"][3] = bad["result"]["stages"][3], bad["result"]["stages"][2]
    case("bad-review-before-draft", "integrated-result", bad, semantic_ok=False, code="STAGE_ORDER_INVALID")
    bad = copy.deepcopy(integrated); bad["result"]["stages"][4]["status"] = "error"
    case("bad-image-after-error", "integrated-result", bad, semantic_ok=False, code="EXECUTED_AFTER_ERROR")
    bad = copy.deepcopy(integrated); bad["result"]["stages"][1]["started_at"] = "2026-11-12T06:00:00Z"
    case("bad-parallel-domain-calls", "integrated-result", bad, semantic_ok=False, code="STAGES_OVERLAP")
    bad = copy.deepcopy(integrated); bad["result"]["final_image"]["lineage"]["integration_input_sha256"] = "0" * 64
    case("bad-integration-image-binding", "integrated-result", bad, semantic_ok=False, code="IMAGE_INTEGRATION_HASH_MISMATCH")
    bad_trend = copy.deepcopy(request["input"]["trend_response"]["raw_response"])
    bad_trend["result"]["evidence"][0]["quote"] = "변조된 합성 인용문"
    case("bad-trend-evidence-quote-hash", "planning-draft-request", make_request(trend=retain(bad_trend)), semantic_ok=False, code="EVIDENCE_ID_QUOTE_MISMATCH")
    bad_trend = copy.deepcopy(request["input"]["trend_response"]["raw_response"])
    bad_trend["result"]["trends"][0]["items"][0]["evidence_ids"] = ["missing:trend:evidence"]
    case("bad-trend-dangling-evidence", "planning-draft-request", make_request(trend=retain(bad_trend)), semantic_ok=False, code="DANGLING_TREND_EVIDENCE")
    bad_trend = copy.deepcopy(request["input"]["trend_response"]["raw_response"])
    bad_trend["result"]["trends"] = []; bad_trend["result"]["evidence"] = []
    case("bad-trend-empty-ok", "planning-draft-request", make_request(trend=retain(bad_trend)), schema_ok=False)
    bad = copy.deepcopy(request)
    bad_stock = copy.deepcopy(bad["input"]["stock_response"]["raw_response"])
    bad_stock["result"].pop("input_provenance")
    bad["input"]["stock_response"] = retain(bad_stock, "sample", "2020-09-22")
    case("bad-stock-missing-target-provenance", "planning-draft-request", bad, schema_ok=False)
    bad = copy.deepcopy(request); bad["input"]["stock_call_context"]["planning_request_sha256"] = "0" * 64
    case("bad-stock-explicit-request-link", "planning-draft-request", bad, semantic_ok=False, code="STOCK_CALL_CONTEXT_MISMATCH")
    bad = copy.deepcopy(integrated); bad["result"]["stages"][2]["input_sha256"] = "0" * 64
    case("bad-stage-input-hash", "integrated-result", bad, semantic_ok=False, code="STAGE_INPUT_HASH_MISMATCH")
    bad = copy.deepcopy(integrated); bad["result"]["stages"][4]["response_sha256"] = "0" * 64
    case("bad-integration-response-hash", "integrated-result", bad, semantic_ok=False, code="INTEGRATION_RESPONSE_HASH_MISMATCH")
    bad = copy.deepcopy(integrated); bad["result"]["integration"] = None
    bad["result"]["final_image"]["lineage"]["integration_input_sha256"] = fingerprint({"draft_response_sha256": draft["content_sha256"], "review_response_sha256": review["content_sha256"], "integration": None})
    case("bad-image-null-integration", "integrated-result", bad, semantic_ok=False, code="MISSING_INTEGRATION")
    bad = make_integrated(empty_trend_request, status="no_results", image_status="skipped", stop="trend")
    bad["status"] = bad["result"]["status"] = "partial"
    case("bad-trend-no-results-aggregate", "integrated-result", bad, semantic_ok=False, code="TREND_NO_RESULTS_STATUS_INVALID")
    bad = make_integrated(request, empty_draft, status="no_results", image_status="skipped", stop="planning_draft")
    bad["status"] = bad["result"]["status"] = "partial"
    case("bad-draft-no-results-aggregate", "integrated-result", bad, semantic_ok=False, code="DRAFT_NO_RESULTS_STATUS_INVALID")
    bad = make_integrated(request, draft, make_review(draft, "no_results"), "partial", "skipped")
    bad["status"] = bad["result"]["status"] = "no_results"
    case("bad-review-no-results-aggregate", "integrated-result", bad, semantic_ok=False, code="REVIEW_NO_RESULTS_STATUS_INVALID")
    bad = copy.deepcopy(review["raw_response"]); bad["result"]["review"]["reviewed_draft"]["draft_id"] = "wrong:draft"
    bad["result"]["review_identity"]["content_sha256"] = fingerprint(bad["result"]["review"])
    case("bad-review-draft-id", "sales-market-review-response", bad, semantic_ok=False, code="DRAFT_REVISION_MISMATCH")
    bad = copy.deepcopy(review["raw_response"]); bad["result"]["review_identity"]["content_sha256"] = "0" * 64
    case("bad-review-content-hash", "sales-market-review-response", bad, semantic_ok=False, code="REVIEW_CONTENT_HASH_MISMATCH")
    bad = copy.deepcopy(draft["raw_response"]); bad["result"]["source_request_sha256"] = "0" * 64
    case("bad-draft-request-hash", "planning-draft-response", bad, semantic_ok=False, code="SOURCE_REQUEST_HASH_MISMATCH")
    bad = copy.deepcopy(request); raw = bad["input"]["stock_response"]["raw_response"]
    raw["result"]["products"][0]["inventory"]["quantity"] = 7.0
    bad["input"]["stock_response"]["content_sha256"] = fingerprint(raw)
    case("bad-raw-number-type-rewritten", "planning-draft-request", bad, semantic_ok=False, code="RAW_ARTIFACT_OBJECT_MISMATCH")
    write_json(FIXTURES / "artifacts.json", dict(sorted(ARTIFACTS.items())))
    write_json(FIXTURES / "index.json", {"synthetic": True, "note": "All documents are fabricated; trace provider_calls are expectations. Actual external calls = 0.", "cases": CASES})
    # Preserve historical/unreferenced blobs; a new manifest is not deletion authority.
    print(json.dumps({"fixtures": len(CASES), "artifact_files": len(ARTIFACTS), "actual_provider_calls": 0}))


if __name__ == "__main__":
    main()
