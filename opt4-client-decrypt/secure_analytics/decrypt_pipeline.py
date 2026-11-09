"""Fetch encrypted tables from a source, decrypt locally, load into the cache. Returns a report, no UI calls."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd

from .cache import CacheEntry, LocalCache
from .config import EncryptedTable
from .connections import Source
from .crypto import Decryptor, looks_encrypted
from .sql_guard import check_read_only


@dataclass
class TableReport:
    table: str
    ok: bool
    rows: int = 0
    decrypted_columns: list[str] = field(default_factory=list)
    failures: int = 0
    seconds: float = 0.0
    error: str = ""
    entry: CacheEntry | None = None


def detect_encrypted_columns(df: pd.DataFrame, sample: int = 5, threshold: float = 0.6) -> list[str]:
    cols = []
    for c in df.columns:
        vals = df[c].dropna().head(sample).tolist()
        if vals and sum(looks_encrypted(v) for v in vals) >= max(1, int(len(vals) * threshold)):
            cols.append(c)
    return cols


def _maybe_numeric(s: pd.Series) -> pd.Series:
    """Decrypted values are strings; restore numbers when every non-null value parses."""
    try:
        return pd.to_numeric(s)
    except (ValueError, TypeError):
        return s


def decrypt_frame(df: pd.DataFrame, columns: list[str], dec: Decryptor) -> tuple[pd.DataFrame, list[str], int]:
    out = df.copy()
    before = dec.failures
    done = []
    for c in columns:
        if c in out.columns:
            out[c] = _maybe_numeric(out[c].map(dec.decrypt))
            done.append(c)
    return out, done, dec.failures - before


def load_table(source: Source, table: EncryptedTable, dec: Decryptor, cache: LocalCache,
               max_rows: int) -> TableReport:
    t0 = time.time()
    try:
        sql = check_read_only(f"SELECT * FROM {table.table_name} LIMIT {int(max_rows)}").sql
        df = source.query(sql)
        cols = detect_encrypted_columns(df) if table.encrypted_columns == ["*"] else \
            [c for c in table.encrypted_columns if c in df.columns]
        plain, done, failures = decrypt_frame(df, cols, dec)
        entry = cache.put(table.table_name, plain, done, failures)
        return TableReport(table.table_name, True, len(plain), done, failures, time.time() - t0, entry=entry)
    except Exception as e:
        return TableReport(table.table_name, False, seconds=time.time() - t0, error=str(e).splitlines()[0][:300])


def load_all(source: Source, tables: list[EncryptedTable], dec: Decryptor, cache: LocalCache,
             max_rows: int) -> list[TableReport]:
    return [load_table(source, t, dec, cache, max_rows) for t in tables]
