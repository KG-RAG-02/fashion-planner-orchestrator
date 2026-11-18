"""Independent P08 consumer for the proposed workflow snapshot; no network refs."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from .upstream_projection import p06_stock_views, p06_trend_views
from .workflow_store import digest, strict_json

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPO_ROOT / 'contracts/workflow'
BASELINE_FORMAT = 'workflow-baseline'
DEFAULT_CONTRACT_PACKAGE = 'workflow-contract'
VERSION = 'fashion-planner-workflow'
# One transport name per route (no version in the name); payload versions live in schema_version.
TREND_HANDOFF_VERSION = 'trend-handoff'
INVENTORY_HANDOFF_VERSION = 'inventory-handoff'
NOVELTY_HANDOFF_VERSION = 'novelty-handoff'
RESEARCH_HANDOFF_VERSION = 'research-handoff'
PAYLOAD_SCHEMA_VERSION = '1.0.0'
STAGES = ('trend', 'stock', 'planning_draft', 'sales_market_review', 'integration', 'final_image')
# planning-draft-request input.mode (0.2.0): draft_with_visuals asks P06 for the product and worn (outfit) images.
DRAFT_MODES = ('draft_only', 'draft_with_visuals')
SCHEMAS = {'trend': ('trend', PAYLOAD_SCHEMA_VERSION, TREND_HANDOFF_VERSION),
           'stock': ('inventory', PAYLOAD_SCHEMA_VERSION, INVENTORY_HANDOFF_VERSION),
           'planning_draft': ('novelty', PAYLOAD_SCHEMA_VERSION, NOVELTY_HANDOFF_VERSION),
           'sales_market_review': ('research', PAYLOAD_SCHEMA_VERSION, RESEARCH_HANDOFF_VERSION)}


class ContractViolation(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ContractViolation(code)


# P06 product profile fields forwarded to P07. Proposal claims are retained as review inputs.
FORWARDED_CLAIM_KINDS = frozenset({'user_requested', 'evidence_observation', 'proposal'})
P07_PRODUCT_FIELDS = ('product_type', 'gender', 'season', 'material')


def p07_review_input(draft, request_id):
    """The P07 0.3 review input: the allowlisted product fields and, for each, its provenance from P06's profile."""
    product, evidence = p07_product_projection(draft)
    return {'request_id': request_id, 'product': product, 'product_evidence': evidence}


def p07_product_projection(draft):
    """The allowlisted product fields P07 receives, read from P06's validated draft.product_profile, and each
    field's provenance (kind, source_ref, evidence_ids).

    Category, product type, gender, season, and material are always present. Existing supported claims retain
    their values and provenance. Missing or non-forwardable profile values use deterministic review placeholders
    with kind 'proposal'; P07 receives the provenance and must treat these as proposals, not confirmed facts."""
    require(isinstance(draft, dict), 'P06_DRAFT_REQUIRED')
    draft_body = draft.get('draft') if isinstance(draft.get('draft'), dict) else None
    require(draft_body is not None, 'P06_DRAFT_REQUIRED')
    profile = draft_body.get('product_profile')
    require(isinstance(profile, dict), 'P06_PRODUCT_PROFILE_REQUIRED')
    draft_evidence_ids = draft.get('evidence_ids', [])
    require(isinstance(draft_evidence_ids, list) and all(isinstance(item, str) for item in draft_evidence_ids),
            'P06_PRODUCT_EVIDENCE_INVALID')

    def profile_value(field):
        claim = profile.get(field)
        require(isinstance(claim, dict) and set(claim) == {'value', 'kind', 'evidence_ids'},
                'P06_PRODUCT_CLAIM_INVALID')
        value, kind, evidence_ids = claim['value'], claim['kind'], claim['evidence_ids']
        require(kind in {'user_requested', 'evidence_observation', 'proposal', 'hypothesis', 'unknown',
                         'to_validate', 'interpretation'}, 'P06_PRODUCT_CLAIM_INVALID')
        require(isinstance(evidence_ids, list) and all(isinstance(item, str) for item in evidence_ids),
                'P06_PRODUCT_EVIDENCE_INVALID')
        require(set(evidence_ids).issubset(set(draft_evidence_ids)), 'P06_PRODUCT_EVIDENCE_REFERENCE_INVALID')
        if value is None or kind not in FORWARDED_CLAIM_KINDS:
            return None
        require(isinstance(value, str) and bool(value.strip()), 'P06_PRODUCT_CLAIM_INVALID')
        evidence[field] = {'kind': kind, 'source_ref': f'novelty.draft.product_profile.{field}',
                           'evidence_ids': list(dict.fromkeys(evidence_ids))}
        return value.strip()

    evidence = {}
    product = {'category': profile_value('category')}
    require(product['category'] and product['category'] == draft_body.get('category'), 'P06_PRODUCT_CATEGORY_REQUIRED')

    # P07 requires a usable product definition. Preserve sourced/model proposals when present;
    # fill missing fields deterministically and label each fallback as a proposal.
    category = product['category']
    fallbacks = {
        'product_type': category,
        'gender': 'unknown',
        'season': (draft_body.get('season') if isinstance(draft_body.get('season'), str)
                   and draft_body.get('season').strip() else '미정'),
        'material': '혼방 소재',
    }
    for key in P07_PRODUCT_FIELDS:
        claim = profile.get(key)
        value = profile_value(key)
        if value is None:
            fallback = fallbacks[key]
            if key == 'gender' and fallback not in {'women', 'men', 'unisex', 'unknown'}:
                fallback = 'unknown'
            value = fallback
            evidence[key] = {'kind': 'proposal', 'source_ref': f'novelty.draft.product_profile.{key}',
                             'evidence_ids': []}
        product[key] = value
    return product, evidence


def require_registered_p06_inputs(call_input, trend_result, stock_result):
    """P06 received the stored raw Trend/Stock result or a registered projection of it (upstream_projection).

    One rule for the check before the P06 call and for integrated(), so an omitted, altered or unregistered
    projection fails with the same code in both places."""
    require(call_input.get('trend_result') in p06_trend_views(trend_result), 'P06_TREND_RESULT_CHANGED')
    require(call_input.get('inventory_product_result') in p06_stock_views(stock_result), 'P06_STOCK_RESULT_CHANGED')


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def pointer(value, path):
    require(path.startswith('/'), 'EVIDENCE_POINTER_INVALID')
    try:
        for part in path[1:].split('/'):
            part = part.replace('~1', '/').replace('~0', '~')
            value = value[int(part)] if isinstance(value, list) else value[part]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ContractViolation('EVIDENCE_POINTER_INVALID') from None
    return value


def truthy(value):
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def snapshot_state(root=CONTRACT_ROOT):
    """Return (snapshot, sha256 of its exact bytes, per-file drift codes)."""
    root = Path(root)
    raw = (root / 'snapshot.json').read_bytes()
    snapshot = strict_json(raw)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('files'), dict):
        raise ValueError('SNAPSHOT_INVALID')
    errors = []
    for relative, expected in sorted(snapshot['files'].items()):
        path = root / relative
        if not path.is_file():
            errors.append('missing:' + relative)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append('hash:' + relative)
    return snapshot, hashlib.sha256(raw).hexdigest(), errors


def baseline_problems(baseline, snapshot, snapshot_sha, file_errors):
    """Baseline consistency, equivalent to (and at least as strict as)
    scripts/freeze_workflow_contract.check_baseline; tests keep the two in step."""
    problems = list(file_errors)
    if not isinstance(baseline, dict):
        return problems + ['baseline_not_object']
    if baseline.get('format') != BASELINE_FORMAT:
        problems.append('format')
    if baseline.get('snapshot_sha256') != snapshot_sha:
        problems.append('snapshot_sha256')
    if baseline.get('files') != dict(sorted(snapshot['files'].items())):
        problems.append('files')
    for key in ('contract_version', 'transport_version'):
        if baseline.get(key) != snapshot.get(key):
            problems.append(key)
    if baseline.get('state') == 'frozen':
        evidence = baseline.get('evidence') if isinstance(baseline.get('evidence'), dict) else {}
        if baseline.get('producer_consumer_validated') is not True:
            problems.append('frozen_without_validation')
        reference = evidence.get('reference_fixtures') if isinstance(evidence.get('reference_fixtures'), dict) else {}
        if reference.get('failed', 1) != 0:
            problems.append('frozen_with_reference_failures')
        suites = evidence.get('suites', [])
        if not isinstance(suites, list) or any(not isinstance(s, dict) or s.get('returncode') != 0 for s in suites):
            problems.append('frozen_with_suite_failures')
        mutations = evidence.get('mutation_probes') if isinstance(evidence.get('mutation_probes'), dict) else {}
        if mutations.get('accepted'):
            problems.append('frozen_with_accepted_mutations')
    return problems


def baseline_path(env=None, root=CONTRACT_ROOT):
    """`VOGUE_WORKFLOW_CONTRACT_BASELINE` overrides contracts/workflow/baseline.json (relative = repo root)."""
    env = os.environ if env is None else env
    override = str(env.get('VOGUE_WORKFLOW_CONTRACT_BASELINE') or '').strip()
    if not override:
        return Path(root) / 'baseline.json', 'contracts/workflow/baseline.json'
    path = Path(override).expanduser()
    return (path if path.is_absolute() else REPO_ROOT / path), 'VOGUE_WORKFLOW_CONTRACT_BASELINE'


def contract_gate_status(env=None, root=None):
    """Observe the in-repo contract gate (no network, never reads outside the configured paths).

    gate: `open` (frozen, validated, snapshot and files match, no recorded failures),
    `development` (anything else, with VOGUE_WORKFLOW_ALLOW_PROPOSED) or `closed`.
    """
    env = os.environ if env is None else env
    root = Path(root) if root else CONTRACT_ROOT
    path, source = baseline_path(env, root)
    allow = truthy(env.get('VOGUE_WORKFLOW_ALLOW_PROPOSED'))
    status = dict(source=source, state='unknown', contract_version=None, consistent=False, frozen=False,
                  problems=[], allow_proposed=allow, gate='closed')
    snapshot = None
    try:
        snapshot, snapshot_sha, file_errors = snapshot_state(root)
        status['contract_version'] = snapshot.get('contract_version')
    except (OSError, ValueError, TypeError, AttributeError):
        status['problems'].append('snapshot_unreadable')
    try:
        baseline = strict_json(path.read_bytes())
    except FileNotFoundError:
        status['state'] = 'missing'
        status['problems'].append('baseline_missing')
    except (OSError, ValueError):
        status['state'] = 'invalid'
        status['problems'].append('baseline_unreadable')
    else:
        state = baseline.get('state') if isinstance(baseline, dict) else None
        status['state'] = state if isinstance(state, str) and state else 'invalid'
        if snapshot is not None:
            status['problems'] = baseline_problems(baseline, snapshot, snapshot_sha, file_errors)
    status['consistent'] = not status['problems']
    status['frozen'] = status['consistent'] and status['state'] == 'frozen'
    status['gate'] = 'open' if status['frozen'] else 'development' if allow else 'closed'
    return status


def development_limitation(status):
    return (status.get('contract_version') or DEFAULT_CONTRACT_PACKAGE) + ' not frozen; development run'


class WorkflowContract:
    def __init__(self, root=None):
        from jsonschema import Draft202012Validator, FormatChecker
        from referencing import Registry, Resource
        self.root = Path(root or CONTRACT_ROOT)
        snapshot = strict_json((self.root / 'snapshot.json').read_bytes())
        docs = {}
        for relative, sha in snapshot['files'].items():
            raw = (self.root / relative).read_bytes()
            require(hashlib.sha256(raw).hexdigest() == sha, 'CONTRACT_SNAPSHOT_CHANGED')
            docs[relative] = strict_json(raw)
        resources = [(d['$id'], Resource.from_contents(d)) for d in docs.values() if isinstance(d, dict) and '$id' in d]
        resources += [('https://contracts.fashion-planner.invalid/orchestrator/' + name, Resource.from_contents(d))
                      for name, d in docs.items() if isinstance(d, dict) and d.get('$schema')]
        registry = Registry().with_resources(resources)
        self.validators = {name: Draft202012Validator(doc, registry=registry, format_checker=FormatChecker())
                           for name, doc in docs.items()}
        self.profile_sha256 = digest(snapshot)

    def schema(self, value, name):
        try:
            valid = self.validators[name].is_valid(value)
        except Exception:
            raise ContractViolation('SCHEMA_REFERENCE_INVALID') from None
        require(valid, 'SCHEMA_INVALID:' + name)

    def envelope(self, value, stage, rid):
        agent, version, transport = SCHEMAS[stage]
        require(isinstance(value, dict), 'ENVELOPE_REQUIRED')
        require(value.get('request_id') == rid, 'REQUEST_ID_MISMATCH')
        require(value.get('contract_version') == transport, 'TRANSPORT_VERSION_MISMATCH')
        require(value.get('producer') == {'agent_id': agent, 'schema_version': version}, 'PRODUCER_VERSION_MISMATCH')
        body = value.get('result')
        require(isinstance(body, dict), 'RESULT_REQUIRED')
        require(body.get('request_id') == rid and body.get('schema_version') == version, 'BODY_ID_VERSION_MISMATCH')
        require(body.get('agent_id', agent) == agent, 'BODY_PRODUCER_MISMATCH')
        require(value.get('status') == body.get('status') and body.get('status') in {'ok','partial','no_results','error'}, 'STATUS_MISMATCH')
        if stage in {'trend', 'stock'}:
            validator = self.validators['common.schema.json'].evolve(schema={'$ref': 'https://contracts.fashion-planner.invalid/orchestrator/common.schema.json#/$defs/' + stage + '_response'})
            require(validator.is_valid(value), 'SCHEMA_INVALID:' + stage)
        elif stage == 'planning_draft':
            self.schema(value, 'planning-draft-response.schema.json')
            require(isinstance(body.get('draft'), dict) or (body.get('draft') is None and body.get('status') in {'no_results','error'}), 'P06_DRAFT_REQUIRED')
            require(isinstance(body.get('input_evidence'), dict), 'P06_EVIDENCE_REQUIRED')
        else:
            self.schema(value, 'sales-market-review-response.schema.json')
            require(body.get('producer_id') == agent, 'BODY_PRODUCER_MISMATCH')
            require(isinstance(body.get('draft_context'), dict), 'P07_DRAFT_CONTEXT_REQUIRED')
            require(isinstance(body.get('limitations'), list), 'P07_LIMITATIONS_REQUIRED')
        return body

    def semantic(self, document, rid, store, known=None):
        """Check unchanged raw bytes, identity links and evidence closure independently."""
        lookup = dict(known or {})
        nodes = list(walk(document))
        for node in nodes:
            if 'request_id' in node:
                require(node['request_id'] == rid, 'REQUEST_ID_MISMATCH')
            if {'artifact_id', 'uri', 'immutable', 'sha256'}.issubset(node):
                artifact_bytes = store.verify(node)
                if node['media_type'] == 'application/json':
                    parsed = strict_json(artifact_bytes)
                    if isinstance(parsed, dict): lookup[digest(parsed)] = parsed
            if 'raw_response' in node and 'raw_artifact' in node:
                raw = node['raw_response']
                require(digest(raw) == node['content_sha256'], 'RAW_CONTENT_HASH_MISMATCH')
                require(digest(strict_json(store.verify(node['raw_artifact']))) == digest(raw), 'RAW_BYTES_OBJECT_MISMATCH')
                lookup[node['content_sha256']] = raw
                stage = next((k for k, v in SCHEMAS.items() if v[0] == raw['producer']['agent_id']), None)
                require(stage is not None, 'UNKNOWN_PRODUCER')
                body = self.envelope(raw, stage, rid)
                if stage == 'stock':
                    products, slots = body['products'], body['trend_results']
                    require(len(products) == len(slots), 'STOCK_SLOT_LENGTH_MISMATCH')
                    require(raw['status'] != 'no_results' or not any(p is not None for p in products), 'NO_RESULTS_HAS_PRODUCTS')
                    ids = {e['evidence_id'] for e in body['evidence']}
                    for i, slot in enumerate(slots):
                        require(slot['product_index'] == i and set(slot['evidence_ids']) <= ids, 'STOCK_SLOT_EVIDENCE_MISMATCH')
                    sample = [p['inventory'] for p in products if p and p['inventory']['data_nature'] == 'sample']
                    if sample:
                        obs = node['observation']
                        dates = {p['as_of'] for p in sample}
                        require(obs['synthetic'] is True and obs['data_nature'] in {'sample','mixed','synthetic_fixture'}, 'SAMPLE_PROVENANCE_LOST')
                        require(obs['as_of'] == next(iter(dates)) if len(dates) == 1 else obs['as_of'] is None, 'SOURCE_AS_OF_CHANGED')
                if stage == 'trend':
                    evidence = body['evidence']
                    ids = {e['evidence_id'] for e in evidence}
                    require(len(ids) == len(evidence), 'DUPLICATE_TREND_EVIDENCE')
                    for e in evidence:
                        expected = f"trend:{e['article_id']}:{e['block_idx']}:{hashlib.sha256(e['quote'].encode()).hexdigest()[:8]}"
                        require(e['evidence_id'] == expected, 'TREND_EVIDENCE_HASH_MISMATCH')
                    for element in walk(body['trends']):
                        for key in ('evidence_ids', 'focus_evidence_ids'):
                            require(set(element.get(key, [])) <= ids, 'DANGLING_TREND_EVIDENCE')
            if 'call_request_sha256' in node and 'call_request_artifact' in node:
                request_bytes = store.verify(node['call_request_artifact'])
                request_value = strict_json(request_bytes)
                require(digest(request_value) == node['call_request_sha256'], 'CALL_REQUEST_HASH_MISMATCH')
            if any(e.get('fatal') for e in node.get('errors', []) if isinstance(e, dict)):
                require(node.get('status') == 'error', 'FATAL_ERROR_HIDDEN')
        for node in nodes:
            if node.get('kind') == 'fact':
                require(bool(node.get('evidence_refs')), 'UNEVIDENCED_FACT')
            if {'source_response_sha256', 'source_pointer', 'evidence_id'} <= node.keys():
                source = lookup.get(node['source_response_sha256'])
                require(source is not None, 'EVIDENCE_SOURCE_MISSING')
                record = pointer(source, node['source_pointer'])
                require(isinstance(record, dict) and record.get('evidence_id') == node['evidence_id'], 'EVIDENCE_ID_MISMATCH')
            if 'source_request_sha256' in node:
                require(digest(node['source_request']) == node['source_request_sha256'], 'SOURCE_REQUEST_HASH_MISMATCH')
                # 0.2.0: the image call count is the sum of the visuals' own calls (0 for draft_only), and the
                # concept cites only evidence that exists in the request it was drawn from.
                draft = node.get('draft') if isinstance(node.get('draft'), dict) else {}
                visuals = draft.get('visuals') or {}
                require(node.get('image_generation_calls', 0) == sum(v['provider_calls'] for v in visuals.values()), 'IMAGE_CALLS_MISMATCH')
                if draft.get('concept') is not None:
                    known_ids = {n['evidence_id'] for n in walk(node['source_request']) if isinstance(n.get('evidence_id'), str)}
                    require({i for n in walk(draft['concept']) for i in n.get('evidence_ids', [])} <= known_ids, 'CONCEPT_EVIDENCE_UNKNOWN')
            if node.get('draft') is not None and 'draft_identity' in node:
                require(digest(node['draft']) == node['draft_identity']['content_sha256'], 'DRAFT_CONTENT_HASH_MISMATCH')
            if 'review_identity' in node and 'review' in node:
                review = node['review']
                require(digest(review) == node['review_identity']['content_sha256'], 'REVIEW_CONTENT_HASH_MISMATCH')
                source = lookup.get(review['draft_response_sha256'])
                require(source is not None and source['producer']['agent_id'] == 'novelty', 'REVIEW_DRAFT_SOURCE_MISSING')
                require(source['result']['draft_identity'] == review['reviewed_draft'], 'REVIEW_DRAFT_IDENTITY_MISMATCH')
                if node['status'] in {'ok', 'partial'}:
                    require(any(f['evidence_refs'] for f in review['findings']) or any(m['value'] is not None for m in review['metrics']), 'REVIEW_HAS_NO_ELIGIBLE_BASIS')
            if node.get('mode') in DRAFT_MODES:
                require(node['planning_request']['query'] == node['user_input']['text'], 'USER_TEXT_MISMATCH')
                trend = node['trend_response']['raw_response']
                stock = node['stock_response']['raw_response']
                require(trend['status'] == 'ok' and stock['status'] != 'error', 'UPSTREAM_STOP_REQUIRED')
                provenance = stock['result'].get('input_provenance')
                require(provenance is not None, 'STOCK_PROVENANCE_MISSING')
                require(provenance['source_payload_sha256'] == digest(trend['result']) and provenance['source_schema_version'] == PAYLOAD_SCHEMA_VERSION, 'STOCK_PROVENANCE_MISMATCH')
                expected = dict(planning_request_sha256=digest(node['planning_request']), trend_response_sha256=digest(trend),
                                stock_input_sha256=digest(trend['result']),
                                stock_wire_request_sha256=digest(dict(contract_version=INVENTORY_HANDOFF_VERSION, request_id=rid, input=trend['result'])),
                                stock_response_sha256=digest(stock))
                require(node['stock_call_context'] == expected, 'STOCK_CONTEXT_MISMATCH')
            if 'planning_draft' in node and 'derived_view' in node:
                require(node['planning_draft']['raw_response']['status'] in {'ok','partial'}, 'UNREVIEWABLE_DRAFT')
            if 'planning_draft' in node and 'derived_view' in node and node['derived_view'] is not None:
                source, view = node['planning_draft'], node['derived_view']
                require(view['source_response_sha256'] == source['content_sha256'], 'DERIVED_VIEW_HASH_MISMATCH')
                for field in ('category', 'season'):
                    path = '/result/draft/' + field
                    require(path in view['source_pointers'] and view['product'][field] == pointer(source['raw_response'], path), 'DERIVED_VIEW_MISMATCH')
        if document.get('producer', {}).get('agent_id') == 'orchestrator':
            self.integrated(document, store)
        return lookup

    def integrated(self, document, store):
        body = document['result']
        sources, stages = body['sources'], body['stages']
        names = [s['stage'] for s in stages]
        require(names == list(STAGES[:len(names)]), 'STAGE_ORDER_INVALID')
        require([s['sequence'] for s in stages] == list(range(1,len(stages)+1)), 'STAGE_SEQUENCE_INVALID')
        previous_end = None
        from datetime import datetime
        for i, stage in enumerate(stages):
            start, end = [datetime.fromisoformat(stage[k].replace('Z','+00:00')) for k in ('started_at','completed_at')]
            require(start <= end and (previous_end is None or start >= previous_end), 'STAGE_TIME_INVALID')
            previous_end = end
            if stage['status'] == 'error':
                require(i == len(stages)-1, 'EXECUTED_AFTER_ERROR')
            if stage['stage'] in sources and sources[stage['stage']]:
                source = sources[stage['stage']]
                require(source['content_sha256'] == stage['response_sha256'] and source['raw_response']['status'] == stage['status'], 'TRACE_SOURCE_MISMATCH')
            if stage['status'] == 'no_results' and stage['stage'] in {'trend','planning_draft'}:
                require(i == len(stages)-1, 'EXECUTED_AFTER_NO_RESULTS')
        for key, source in sources.items():
            require(source is None or key in names, 'UNTRACED_SOURCE')
        draft, review = sources['planning_draft'], sources['sales_market_review']
        if draft:
            draft_body = draft['raw_response']['result']
            if draft_body.get('schema_version') == 'planning-draft/1.0' and 'source_request' in draft_body:
                origin = draft_body['source_request']['input']
                require(origin['trend_response'] == sources['trend'] and origin['stock_response'] == sources['stock'], 'UPSTREAM_RAW_REWRITTEN')
                require(origin['user_input'] == body['user_input'] and origin['planning_request'] == body['planning_request'], 'INPUT_RAW_REWRITTEN')
            else:
                call = strict_json(store.verify(draft['call_request_artifact']))
                require(call.get('contract_version') == NOVELTY_HANDOFF_VERSION and call.get('request_id') == body['request_id'], 'P06_CALL_ENVELOPE_INVALID')
                inp = call.get('input', {})
                require(inp.get('planning_request') == body['planning_request'], 'P06_PLANNING_REQUEST_CHANGED')
                # The run keeps the raw results; P06 may have received them raw or as any registered projection.
                require_registered_p06_inputs(inp, sources['trend']['raw_response']['result'],
                                              sources['stock']['raw_response']['result'])
        if review:
            require(draft is not None, 'REVIEW_WITHOUT_DRAFT')
            review_body = review['raw_response']['result']
            if review_body.get('schema_version') == 'sales-market-review/1.0' and 'review' in review_body:
                require(review_body['review']['draft_response_sha256'] == draft['content_sha256'], 'REVIEW_DRAFT_HASH_MISMATCH')
            else:
                call = strict_json(store.verify(review['call_request_artifact']))
                require(call.get('contract_version') == RESEARCH_HANDOFF_VERSION and call.get('request_id') == body['request_id'], 'P07_CALL_ENVELOPE_INVALID')
                draft_value = draft['raw_response']['result']
                require(call.get('input') == p07_review_input(draft_value, body['request_id']), 'P07_PRODUCT_PROJECTION_MISMATCH')
        if sources['trend'] and sources['trend']['raw_response']['status'] == 'no_results':
            require(body['status'] == 'no_results', 'TREND_NO_RESULTS_HIDDEN')
        if draft and draft['raw_response']['status'] == 'no_results':
            require(body['status'] == 'no_results', 'DRAFT_NO_RESULTS_HIDDEN')
        if review and review['raw_response']['status'] == 'no_results':
            require(body['status'] == ('error' if stages[-1]['stage'] == 'integration' and stages[-1]['status'] == 'error' else 'partial'), 'REVIEW_NO_RESULTS_HIDDEN')
        if 'integration' in names:
            require(body['integration'] is not None or stages[4]['status'] == 'error', 'MISSING_INTEGRATION')
            if stages[4]['status'] != 'error':
                require(stages[4]['response_sha256'] == digest(body['integration']), 'INTEGRATION_HASH_MISMATCH')
        else:
            require(body['integration'] is None, 'UNTRACED_INTEGRATION')
        for stage in stages:
            name = stage['stage']
            if name == 'trend': expected = digest(body['planning_request'])
            elif name == 'stock': expected = digest(sources['trend']['raw_response']['result'])
            elif name == 'planning_draft':
                if draft and 'source_request_sha256' in draft['raw_response']['result']: expected = draft['raw_response']['result']['source_request_sha256']
                elif draft: expected = draft['call_request_sha256']
                else:
                    # No P06 response exists on an upstream failure, so only the locally computed
                    # request fingerprint is available in the trace.
                    expected = stage['input_sha256']
            elif name == 'sales_market_review': expected = draft['content_sha256']
            elif name == 'integration': expected = digest(dict(draft_response_sha256=draft['content_sha256'],review_response_sha256=review['content_sha256']))
            else: expected = digest(dict(draft_response_sha256=draft['content_sha256'],review_response_sha256=review['content_sha256'],integration=body['integration']))
            require(stage['input_sha256'] == expected, 'STAGE_INPUT_HASH_MISMATCH')
        image = body['final_image']
        if image['status'] in {'generated','failed'}:
            require(draft is not None and review is not None and len(stages) == 6, 'IMAGE_BEFORE_REVIEW')
            require(draft['raw_response']['status'] in {'ok','partial'} and review['raw_response']['status'] in {'ok','partial'}, 'IMAGE_WITHOUT_VALID_REVIEW')
            line = image['lineage']
            rid_tag = digest(body['request_id'])[:16]
            draft_identity = draft['raw_response']['result'].get('draft_identity') or dict(draft_id='p08-draft-' + rid_tag, revision=1, content_sha256=draft['content_sha256'])
            review_identity = review['raw_response']['result'].get('review_identity') or dict(review_id='p08-review-' + rid_tag, revision=1, content_sha256=review['content_sha256'])
            expected = dict(draft_identity=draft_identity, draft_response_sha256=draft['content_sha256'],
                            review_identity=review_identity, review_response_sha256=review['content_sha256'],
                            integration_input_sha256=digest(dict(draft_response_sha256=draft['content_sha256'], review_response_sha256=review['content_sha256'], integration=body['integration'])))
            require(line == expected, 'IMAGE_LINEAGE_MISMATCH')
            if image['status'] == 'generated':
                require(stages[-1]['status'] == 'ok' and stages[-1]['response_sha256'] == image['artifact']['sha256'], 'IMAGE_TRACE_HASH_MISMATCH')
            else:
                require(body['status'] == 'partial' and stages[-1]['status'] == 'error', 'IMAGE_FAILURE_STATUS_INVALID')
        else:
            require('final_image' not in names, 'SKIPPED_IMAGE_EXECUTED')
        statuses = [s['raw_response']['status'] for s in sources.values() if s]
        if body['status'] == 'ok':
            require(all(s == 'ok' for s in statuses) and len(stages) == 6 and image['status'] == 'generated', 'UPSTREAM_STATUS_HIDDEN')
        if 'error' in statuses or any(s['status'] == 'error' for s in stages[:-1]):
            require(body['status'] == 'error', 'TERMINAL_ERROR_HIDDEN')

        # The aggregate status must reflect every retained producer result. A producer's
        # partial/no_results must never be downgraded to `ok` or mislabeled as no_results
        # for the whole workflow. Trend/P06 no_results stop the workflow; all other
        # incomplete sources and a skipped/failed final image aggregate to `partial`.
        producer_status = {name: source['raw_response']['status']
                           for name, source in sources.items() if source is not None}
        if ('error' in producer_status.values()
                or any(stage['status'] == 'error' and stage['stage'] != 'final_image' for stage in stages)):
            expected_status = 'error'
        elif producer_status.get('trend') == 'no_results' or producer_status.get('planning_draft') == 'no_results':
            expected_status = 'no_results'
        elif (any(status in {'partial', 'no_results'} for status in producer_status.values())
              or image['status'] != 'generated'
              or any(stage['status'] in {'partial', 'no_results'} for stage in stages)):
            expected_status = 'partial'
        else:
            expected_status = 'ok'
        require(body['status'] == expected_status, 'AGGREGATE_STATUS_MISMATCH')
