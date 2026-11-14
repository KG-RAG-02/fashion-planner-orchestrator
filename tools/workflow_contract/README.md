# workflow_contract — reference tooling for the proposed workflow package

The committed schemas and synthetic fixtures are the validation inputs. This directory contains the offline reference validator and mutation probes; it does not generate or redefine producer-owned contracts.

| File | Purpose |
|---|---|
| `validate_contracts.py` | Reference consumer: JSON Schema (Draft 2020-12, no remote refs) + semantic invariants (IDs, status, hashes, Evidence closure, lineage). `collect()` returns a summary; `main([out_dir])` writes a report to `.local/contract-validation/`. |
| `probe_semantic_gaps.py` | Contract mutation probes that must all be rejected. |
| `support.py` | Shared paths and atomic JSON output helpers used by the validator and probes. |
| `fixtures/` | Committed synthetic cases (`index.json`) and content-addressed artifacts. All documents are fabricated; no external calls. Fixture updates must follow the current P08 schema and snapshot procedure; the retired `vogue-workflow/0.1` generators were removed. |

Run (requires the `workflow` extra):

```
PYTHONPATH=src python -m unittest tests.test_workflow_contract_reference -v
python scripts/freeze_workflow_contract.py --check
```

By default, freezing requires clean, recognized producer and consumer checkouts. When local integration work is intentionally uncommitted, `--allow-dirty-sources` can be used after validation. It records a SHA-256 over every non-ignored tracked and untracked file in each checkout, plus a hash of its Git status; the baseline does not include file contents. P08's baseline file is excluded from its own worktree digest to avoid a self-referential hash; its contract snapshot hash is recorded separately. This keeps the evidence tied to the exact local worktrees without requiring commits or changing sibling repositories.

The local Project_06 and Project_07 checkout names `project_06` and `project_07` are recognized as aliases for `new-product-agent` and `sales-market-agent`.

The contract baseline (`contracts/workflow/baseline.json`) is written only by
`scripts/freeze_workflow_contract.py`; see [`contracts/workflow/workflow-contract.md`](../../contracts/workflow/workflow-contract.md).
