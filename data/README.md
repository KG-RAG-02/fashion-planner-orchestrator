# P08 query-structuring evaluation data

This directory contains synthetic development and holdout cases for evaluating how P08 converts a user's natural-language product request into a structured query for Trend Agent. The cases and recorded model outputs are evaluation material, not production user data or runtime configuration.

| Path | Purpose |
|---|---|
| `user_queries.v3.json` | Development cases (30, synthetic, labels unverified) |
| `user_queries.holdout.v3.json` | Holdout cases (20, synthetic, labels unverified) |

The active UI path uses prompt v6 from `prompts/query_structuring/v6/`. Evaluation scripts compare structured fields and warnings against these datasets; changing an evaluation file does not change the runtime prompt or select a workflow mode.
