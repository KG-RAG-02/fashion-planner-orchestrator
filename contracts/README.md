# Project_08 contracts

This folder contains contract snapshots and consumer-side API contracts used by Project_08. Snapshots copied from another repository record their source and are validated independently; Project_08 does not import sibling Python packages to use them.

- `trend-query.md` and `trend-query.contract.json` are read-only snapshots of the TrendQuery contract.
- `structured-query.md` and `structured-query.schema.json` define `StructuredQuery` 1.0, the query-structuring output owned by Project_08 (the schema is generated from `src/vogue_orchestrator/query/models.py`; do not edit it by hand).
- `alias-map.snapshot.json`, `alias-map.golden.json` and `alias-map.md` are a read-only snapshot of trend-analysis-agent's alias dictionary and the outputs of its original code, used to verify `src/vogue_orchestrator/query/lexicon.py`.
- Project_07 owns the producer result and shared-envelope JSON Schemas in its repository's `contracts/schemas/`.
- Project_06 documents its accepted producer versions and normalizer in its repository's `contracts/http-handoff-v0.1.md`.
