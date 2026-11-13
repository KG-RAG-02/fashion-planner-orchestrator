# workflow_contract — reference tooling for `vogue-workflow/0.1`

Moved from the local `_workflow/handoffs/coordinator/` workspace on 2026-11-13 so the
contract package, its synthetic fixtures and its reference validator live in version control.

| File | Purpose |
|---|---|
| `validate_contracts.py` | Reference consumer: JSON Schema (Draft 2020-12, no remote refs) + semantic invariants (IDs, status, hashes, Evidence closure, lineage). `collect()` returns a summary; `main([out_dir])` writes a report to `.local/contract-validation/`. |
| `probe_semantic_gaps.py` | Seven semantic mutation probes that must all be rejected. |
| `fixtures/` | 66 indexed synthetic cases (`index.json`) + content-addressed artifacts. All documents are fabricated; no external calls. |
| `build_contracts.py` | Generator that originally produced `contracts/workflow/*.schema.json`. **Do not rerun casually**: producers pin the schema bytes by SHA-256 (`new-product-agent/contracts/workflow-proposed/snapshot.json`), and re-exporting the Trend model (which gained `example_images`) would change pinned bytes. |
| `build_fixtures.py` | Generator for `fixtures/`. Same caution as above. |

Run (requires the `workflow` extra):

```
PYTHONPATH=src python -m unittest tests.test_workflow_contract_reference -v
python scripts/freeze_workflow_contract.py --check
```

The contract baseline (`contracts/workflow/baseline.json`) is written only by
`scripts/freeze_workflow_contract.py`; see `contracts/workflow/README.md`.
