"""Query routing.

* cache  – run against decrypted local tables only. Source names (DB.SCHEMA.T) are rewritten to cache names.
* live   – run on the source as-is (read-only checked). Returns ciphertext for encrypted columns.
* smart  – tables in the cache are used locally; any other table is fetched live (row-capped),
           exposed as a temporary view for this one query, then discarded.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import sqlglot
from sqlglot import exp

from .cache import LocalCache, cache_name
from .connections import Source
from .sql_guard import UnsafeSQL, check_read_only

MODES = ("cache", "smart", "live")


@dataclass
class QueryResult:
    df: pd.DataFrame
    mode: str
    executed_sql: str
    local_tables: list[str] = field(default_factory=list)
    live_tables: list[str] = field(default_factory=list)


def _resolve(name: str, cache: LocalCache) -> str | None:
    """Map a written table name to a cache table: exact cache name, or the cache name of a source FQN.
    A bare name also matches a cached table whose source ends with that name."""
    live = cache.live_entries()
    n = cache_name(name)
    if n in live:
        return n
    for e in live.values():
        if e.source.split(".")[-1].lower() == name.lower():
            return e.name
    return None


def _rewrite(sql: str, mapping: dict[str, str]) -> str:
    tree = sqlglot.parse_one(sql, read="snowflake")

    def swap(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Table):
            full = ".".join(p for p in (node.catalog, node.db, node.name) if p)
            if full in mapping:
                new = exp.to_table(mapping[full], quoted=True)
                if node.alias:
                    new.set("alias", node.args.get("alias"))
                return new
        return node

    return tree.transform(swap).sql(dialect="duckdb")


def run(sql: str, mode: str, cache: LocalCache, source: Source | None, max_live_rows: int) -> QueryResult:
    if mode not in MODES:
        raise ValueError(mode)
    checked = check_read_only(sql)
    if mode == "live":
        if source is None:
            raise UnsafeSQL("No live connection – connect first or use cache mode.")
        return QueryResult(source.query(checked.sql, max_live_rows), mode, checked.sql,
                           live_tables=checked.tables)

    mapping, live_needed = {}, []
    for t in checked.tables:
        local = _resolve(t, cache)
        if local:
            mapping[t] = local
        else:
            live_needed.append(t)

    if live_needed and mode == "cache":
        raise UnsafeSQL(f"Not in local cache: {', '.join(live_needed)}. Use smart mode to fetch live tables.")
    if live_needed and source is None:
        raise UnsafeSQL("Query needs live tables but there is no connection.")

    temps: dict[str, pd.DataFrame] = {}
    for t in live_needed:
        tmp = "_live_" + cache_name(t)
        q = check_read_only(f"SELECT * FROM {t} LIMIT {int(max_live_rows)}").sql
        temps[tmp] = source.query(q)
        mapping[t] = tmp

    local_sql = _rewrite(checked.sql, mapping)
    df = cache.query_with_temp(local_sql, temps) if temps else cache.query(local_sql)
    return QueryResult(df, mode, local_sql, [mapping[t] for t in checked.tables if t not in live_needed],
                       live_needed)
