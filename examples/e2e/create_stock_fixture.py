"""Create a tiny synthetic DuckDB for local Stock HTTP E2E only."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb


path = Path(os.environ.get("STOCK_DB_PATH", "/tmp/vogue-stock-e2e.duckdb"))
path.parent.mkdir(parents=True, exist_ok=True)
if path.exists():
    raise FileExistsError(f"Refusing to overwrite existing file: {path}")

con = duckdb.connect(str(path))
con.execute("""CREATE TABLE article_attrs AS SELECT * FROM (VALUES
    ('a1','Ladieswear','상의','탑','핑크',[]::VARCHAR[],NULL,['봄','여름']),
    ('a2','Ladieswear','상의','블라우스','화이트',[]::VARCHAR[],NULL,['봄','여름']),
    ('a3','Ladieswear','하의','팬츠','그레이',['면'],NULL,['봄','여름'])
) v(article_id,segment,kf_area,kf_category,kf_color,materials,fit,season_fit)""")
con.execute("""CREATE TABLE stock_v1 AS SELECT * FROM (VALUES
    ('a1',20,10000,200000,0.8,40),('a2',10,12000,120000,0.7,25),('a3',30,15000,450000,0.5,60)
) v(article_id,stock_qty,price_krw,stock_value_krw,sell_through,n_sold)""")
con.execute("""CREATE TABLE transactions AS SELECT * FROM (VALUES
    (DATE '2025-04-01','a1'),(DATE '2025-04-02','a2'),(DATE '2025-04-03','a3')
) v(t_dat,article_id)""")
con.execute("CREATE TABLE articles AS SELECT article_id FROM article_attrs")
con.close()
print(f"Created synthetic Stock DB at {path}")
