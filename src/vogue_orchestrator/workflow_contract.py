"""Independent P08 consumer for the proposed workflow snapshot; no network refs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from .workflow_store import digest, strict_json

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPO_ROOT / 'contracts/workflow'
BASELINE_FORMAT = 'vogue-workflow-baseline/1'
DEFAULT_CONTRACT_PACKAGE = 'workflow-contract/0.2.0'
VERSION = 'vogue-workflow/0.1'
STAGES = ('trend', 'stock', 'planning_draft', 'sales_market_review', 'integration', 'final_image')
# planning-draft-request input.mode (0.2.0): draft_with_visuals asks P06 for the product and worn (outfit) images.
DRAFT_MODES = ('draft_only', 'draft_with_visuals')
SCHEMAS = {'trend': ('trend-analysis-agent', '1.1', 'vogue-handoff/0.1'),
           'stock': ('stock-agent', '1.0', 'vogue-handoff/0.1'),
           'planning_draft': ('new-product-agent', 'planning-draft/1.0', VERSION),
           'sales_market_review': ('sales-market-agent', 'sales-market-review/1.0', VERSION)}


class ContractViolation(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ContractViolation(code)


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
        resources = [(d['$id'], Resource.from_contents(d)) for d in docs.values()]
        resources += [('https://contracts.vogue.invalid/workflow/0.1/' + name, Resource.from_contents(d)) for name, d in docs.items()]
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
            validator = self.validators['common.schema.json'].evolve(schema={'$ref': 'https://contracts.vogue.invalid/workflow/0.1/common.schema.json#/$defs/' + stage + '_response'})
            require(validator.is_valid(value), 'SCHEMA_INVALID:' + stage)
        else:
            self.schema(value, 'planning-draft-response.schema.json' if stage == 'planning_draft' else 'sales-market-review-response.schema.json')
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
                require(source is not None and source['producer']['agent_id'] == 'new-product-agent', 'REVIEW_DRAFT_SOURCE_MISSING')
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
                require(provenance['source_payload_sha256'] == digest(trend['result']) and provenance['source_schema_version'] == '1.1', 'STOCK_PROVENANCE_MISMATCH')
                expected = dict(planning_request_sha256=digest(node['planning_request']), trend_response_sha256=digest(trend),
                                stock_input_sha256=digest(trend['result']),
                                stock_wire_request_sha256=digest(dict(contract_version='vogue-handoff/0.1', request_id=rid, input=trend['result'])),
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
        if document.get('producer', {}).get('agent_id') == 'vogue-orchestrator':
            self.integrated(document)
        return lookup

    def integrated(self, document):
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
            origin = draft['raw_response']['result']['source_request']['input']
            require(origin['trend_response'] == sources['trend'] and origin['stock_response'] == sources['stock'], 'UPSTREAM_RAW_REWRITTEN')
            require(origin['user_input'] == body['user_input'] and origin['planning_request'] == body['planning_request'], 'INPUT_RAW_REWRITTEN')
        if review:
            require(draft is not None and review['raw_response']['result']['review']['draft_response_sha256'] == draft['content_sha256'], 'REVIEW_DRAFT_HASH_MISMATCH')
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
                if draft: expected = draft['raw_response']['result']['source_request_sha256']
                else:
                    t,s = sources['trend'],sources['stock']
                    wire = dict(contract_version='vogue-handoff/0.1', request_id=body['request_id'], input=t['raw_response']['result'])
                    context = dict(planning_request_sha256=digest(body['planning_request']),trend_response_sha256=t['content_sha256'],stock_input_sha256=digest(t['raw_response']['result']),stock_wire_request_sha256=digest(wire),stock_response_sha256=s['content_sha256'])
                    # Either Draft mode may have been sent (0.2.0); the trace must match one of them.
                    candidates = {digest(dict(contract_version=VERSION,request_id=body['request_id'],input=dict(mode=mode,user_input=body['user_input'],planning_request=body['planning_request'],trend_response=t,stock_response=s,stock_call_context=context))) for mode in DRAFT_MODES}
                    expected = stage['input_sha256'] if stage['input_sha256'] in candidates else None
            elif name == 'sales_market_review': expected = draft['content_sha256']
            elif name == 'integration': expected = digest(dict(draft_response_sha256=draft['content_sha256'],review_response_sha256=review['content_sha256']))
            else: expected = digest(dict(draft_response_sha256=draft['content_sha256'],review_response_sha256=review['content_sha256'],integration=body['integration']))
            require(stage['input_sha256'] == expected, 'STAGE_INPUT_HASH_MISMATCH')
        image = body['final_image']
        if image['status'] in {'generated','failed'}:
            require(draft is not None and review is not None and len(stages) == 6, 'IMAGE_BEFORE_REVIEW')
            require(draft['raw_response']['status'] in {'ok','partial'} and review['raw_response']['status'] in {'ok','partial'}, 'IMAGE_WITHOUT_VALID_REVIEW')
            line = image['lineage']
            expected = dict(draft_identity=draft['raw_response']['result']['draft_identity'], draft_response_sha256=draft['content_sha256'],
                            review_identity=review['raw_response']['result']['review_identity'], review_response_sha256=review['content_sha256'],
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
