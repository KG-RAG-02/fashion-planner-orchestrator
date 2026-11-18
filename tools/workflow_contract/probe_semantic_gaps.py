"""Mutation probes against actual schema/reference validator; no providers."""
import copy
import json
from pathlib import Path
from support import write_json
from validate_contracts import FixtureStore, validators, semantic, SemanticError, read_json, fingerprint
HERE=Path(__file__).resolve().parent

def cases():
    out=[]
    def add(name, d): out.append((name,d))
    d=read_json(HERE/'fixtures/novelty-handoff.request.json'); d['contract_version']='trend-handoff'; add('novelty-wrong-contract-version',d)
    d=read_json(HERE/'fixtures/research-handoff.request.json'); d['input']['product']['planning_draft']={'draft':'must not be sent'}; add('research-full-draft-rejected',d)
    d=read_json(HERE/'fixtures/novelty-handoff.response.json'); d['result']['request_id']='different-request'; add('novelty-response-request-id-mismatch',d)
    d=read_json(HERE/'fixtures/research-handoff.response.json'); d['producer']['schema_version']='0.0.0'; add('research-producer-version-mismatch',d)
    return out

def collect():
    checks=validators(); store=FixtureStore(HERE/'fixtures'); results=[]
    for name,d in cases():
        schema_name = ('planning-draft-request.schema.json' if name.startswith('novelty-wrong') else
                       'research-review-request.schema.json' if name.startswith('research-full') else
                       'planning-draft-response.schema.json' if name.startswith('novelty-response') else
                       'research-review-response.schema.json')
        errors=list(checks[schema_name].iter_errors(d)); code=None
        if not errors:
            try: semantic(d,schema_name,store)
            except SemanticError as e: code=str(e)
        results.append({'case':name,'accepted':not errors and code is None,'schema_valid':not errors,'semantic_error':code,'expected_accept':False})
    return {'scope':'coordinator mutation probes; independent from fixture generator, not independent owner parsers','cases':len(results),'rejected':sum(not x['accepted'] for x in results),'results':results,'provider_calls':0,'service_calls':0}

def main(output):
    result=collect()
    write_json(Path(output),result); print(json.dumps(result))
if __name__=='__main__':
    import sys
    main(sys.argv[1])
