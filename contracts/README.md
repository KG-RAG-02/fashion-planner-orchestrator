# Contracts

This directory documents the contracts at P08's boundaries: upstream producer schemas, P08's outbound request schemas, and the integrated response consumed by the UI. Agent-owned native contracts remain owned by their producer repositories.

## Workflow contract

`workflow/` contains the proposed `workflow-contract/0.5.0` schemas and public transport `vogue-workflow/0.2`. P08 calls P06 `vogue-handoff/0.3` at `/runs`, then P06's `/sales-market-reviews` bridge (`vogue-sales-market-review/0.1-proposal`) to validate and project the Draft. P08 validates that projection and calls P07 `vogue-sales-market-review/0.2-proposal` at `/reviews`. P07 receives only its allowlisted product fields. P08 retains the P06 response and field provenance locally; P07 does not receive that provenance or attest to the full Draft/hash. `snapshot.json` pins the current schema bytes. The package remains proposed until producer-consumer validation is complete.

## Agent registry

`agents/` contains the schema registry and source fingerprints used to inspect producer boundaries. `python scripts/export_agent_contracts.py --check` checks the registry against the configured producer sources. These files are contract evidence, not a second runtime path. The workflow baseline is currently proposed, not frozen.
