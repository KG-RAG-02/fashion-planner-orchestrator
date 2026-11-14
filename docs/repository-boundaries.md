# Repository boundaries

Project 08 is the orchestration boundary. It accepts user text and optional uploaded images, structures the query, calls Trend, Stock, New Product, and Sales Market in sequence, validates their contracts, stores run artifacts, and returns the integrated result and Planning Sheet.

Project 06 owns Draft generation and its product/outfit visuals. Project 07 owns Sales Market review. Trend and Stock own their respective producer result schemas. Shared workflow request/response schemas are pinned under `contracts/workflow/`; schema changes require producer/consumer synchronization and updated validation evidence.

The local UI defaults to loopback port 8765. The Docker deployment uses service URLs from `deploy/compose.yaml`; local sibling-service launcher settings are defined in `deploy/service-manifest.json`. Availability checks do not prove schema compatibility, source permissions, or operational approval.
