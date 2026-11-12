"""Prewarm the sales-market H&M DuckDB cache from Cloudflare R2, then probe it read-only.

Runs inside the vogue/sales-market image (mounted read-only by compose.data.yaml) so the
probe uses P07's own DuckDB build — a file P07 cannot open fails here, before any request.
Prints sizes, table names and versions only; never credentials or object keys.
Exit 0 only when the cache is present and every required table opens.
"""
import json
import sys

import duckdb

from market_research_agent.sources.sales.hm_kaggle.hm_r2_duckdb_source import R2DuckDBCache

REQUIRED_ANY = ("transactions",)


def main() -> int:
    try:
        snapshot = R2DuckDBCache().prepare()
    except Exception as error:  # report the class only; messages may carry configuration detail
        print(json.dumps({"ok": False, "stage": "prepare", "error": type(error).__name__}))
        return 1
    try:
        con = duckdb.connect(str(snapshot.path), read_only=True)
        tables = sorted(row[0] for row in con.execute(
            "select table_name from information_schema.tables where table_schema = 'main'").fetchall())
        counts = {name: con.execute(f'select count(*) from "{name}"').fetchone()[0] for name in tables if name in REQUIRED_ANY}
        con.close()
    except Exception as error:
        print(json.dumps({"ok": False, "stage": "open", "duckdb": duckdb.__version__, "error": type(error).__name__}))
        return 1
    ok = all(name in tables for name in REQUIRED_ANY)
    print(json.dumps({"ok": ok, "duckdb": duckdb.__version__, "size_bytes": snapshot.size_bytes,
                      "tables": tables, "row_counts": counts}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
