"""Mutation probes against actual schema/reference validator; no providers."""
import copy
import json
from pathlib import Path
from build_contracts import write_json
from validate_contracts import FixtureStore, validators, semantic, SemanticError, read_json, fingerprint
HERE=Path(__file__).resolve().parent

def cases():
    base=read_json(HERE/'fixtures/integrated-ok.json')
    out=[]
    def add(name, d): out.append((name,d))
    d=copy.deepcopy(base); d['result']['stages'][2]['input_sha256']='0'*64; add('unbound-stage-input',d)
    d=copy.deepcopy(base); d['result']['stages'][4]['response_sha256']='0'*64; add('unbound-integration-response',d)
    d=copy.deepcopy(base); d['result']['integration']=None
    d['result']['final_image']['lineage']['integration_input_sha256']=fingerprint({'draft_response_sha256':d['result']['sources']['planning_draft']['content_sha256'],'review_response_sha256':d['result']['sources']['sales_market_review']['content_sha256'],'integration':None})
    add('image-with-null-integration',d)
    d=read_json(HERE/'fixtures/integrated-trend-no-results.json'); d['status']=d['result']['status']='partial'; add('trend-no-results-hidden',d)
    d=read_json(HERE/'fixtures/integrated-draft-no-results.json'); d['status']=d['result']['status']='partial'; add('draft-no-results-hidden',d)
    d=read_json(HERE/'fixtures/integrated-review-no-results.json'); d['status']=d['result']['status']='no_results'; add('review-no-results-loses-draft',d)
    d=copy.deepcopy(base); d['result']['sources']['stock']['observation']['as_of']='02020-09-22'; add('invalid-observation-as-of',d)
    return out

def collect():
    checks=validators(); store=FixtureStore(HERE/'fixtures'); results=[]
    for name,d in cases():
        errors=list(checks['integrated-result.schema.json'].iter_errors(d)); code=None
        if not errors:
            try: semantic(d,'integrated-result.schema.json',store)
            except SemanticError as e: code=str(e)
        results.append({'case':name,'accepted':not errors and code is None,'schema_valid':not errors,'semantic_error':code,'expected_accept':False})
    return {'scope':'coordinator mutation probes; independent from fixture generator, not independent owner parsers','cases':len(results),'rejected':sum(not x['accepted'] for x in results),'results':results,'provider_calls':0,'service_calls':0}

def main(output):
    result=collect()
    write_json(Path(output),result); print(json.dumps(result))
if __name__=='__main__':
    import sys
    main(sys.argv[1])
