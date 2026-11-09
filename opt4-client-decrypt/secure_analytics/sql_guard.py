"""Read-only SQL validation with sqlglot (replaces regex keyword blacklists)."""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

# Statement roots we accept. Everything else (INSERT, COPY, ATTACH, PRAGMA, SET, CALL, ...) is rejected.
_ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Describe)
# Nodes that must never appear anywhere in the tree, even nested inside a SELECT.
_FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
              exp.Command, exp.Copy, exp.Set, exp.Pragma, exp.Into, exp.TruncateTable)
_FILE_FUNCS = {"read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
               "read_blob", "glob", "parquet_scan", "sniff_csv", "query_table", "getenv"}


class UnsafeSQL(ValueError):
    pass


@dataclass(frozen=True)
class CheckedSQL:
    sql: str
    tables: list[str]   # fully written table names as they appear (e.g. "DB.SCHEMA.T")


def table_names(tree: exp.Expression) -> list[str]:
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    out: list[str] = []
    for t in tree.find_all(exp.Table):
        parts = [p for p in (t.catalog, t.db, t.name) if p]
        full = ".".join(parts)
        if t.name and t.name.lower() not in ctes and full not in out:
            out.append(full)
    return out


def check_read_only(sql: str, dialect: str = "snowflake") -> CheckedSQL:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise UnsafeSQL("Empty query")
    try:
        trees = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQL(f"Could not parse SQL: {str(e).splitlines()[0]}") from e
    trees = [t for t in trees if t is not None]
    if len(trees) != 1:
        raise UnsafeSQL("Only a single statement is allowed")
    tree = trees[0]
    if not isinstance(tree, _ALLOWED_ROOTS):
        raise UnsafeSQL(f"Only SELECT queries are allowed (got {tree.key.upper()})")
    bad = next((n for n in tree.walk() if isinstance(n, _FORBIDDEN)), None)
    if bad is not None:
        raise UnsafeSQL(f"Statement type not allowed: {bad.key.upper()}")
    # Table functions (read_csv, read_parquet, glob, read_text, ...) read files or URLs – never allowed.
    for t in tree.find_all(exp.Table):
        if isinstance(t.this, exp.Func):
            raise UnsafeSQL(f"Table functions are not allowed ({t.this.sql()[:40]})")
    for f in tree.find_all(exp.Anonymous):
        if f.name.lower() in _FILE_FUNCS:
            raise UnsafeSQL(f"Function not allowed: {f.name}")
    return CheckedSQL(sql, table_names(tree))
