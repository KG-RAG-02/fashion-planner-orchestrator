"""Pin the default in-repo contract baseline to a `proposed` copy for gate/status tests.

contracts/workflow/baseline.json is written only by scripts/freeze_workflow_contract.py and is
`frozen` once the contract is accepted. Tests that exercise the closed/development gate must not
depend on that live state, so they resolve the default baseline to a temporary proposed copy with
the same snapshot hash and files map. An explicit VOGUE_WORKFLOW_CONTRACT_BASELINE still wins.
"""
import copy
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from fashion_planner_orchestrator import workflow_contract


def pin_proposed_baseline(testcase):
    live = json.loads((workflow_contract.CONTRACT_ROOT / 'baseline.json').read_text(encoding='utf-8'))
    proposed = copy.deepcopy(live)
    proposed.update(state='proposed', producer_consumer_validated=False, evidence={}, sources={})
    directory = Path(tempfile.mkdtemp(prefix='vogue-baseline-'))
    path = directory / 'baseline.json'
    path.write_text(json.dumps(proposed, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    real = workflow_contract.baseline_path

    def pinned(env=None, root=workflow_contract.CONTRACT_ROOT):
        resolved, source = real(env, root)
        if source == 'contracts/workflow/baseline.json' and Path(root) == workflow_contract.CONTRACT_ROOT:
            return path, source
        return resolved, source

    patcher = patch.object(workflow_contract, 'baseline_path', pinned)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    testcase.addCleanup(shutil.rmtree, directory, True)
    return proposed
