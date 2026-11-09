"""Local DuckDB cache for decrypted data.

* In-memory by default (plaintext never touches disk).
* External file / network access disabled and configuration locked after init, so user SQL cannot
  ``read_csv('/etc/passwd')``, ``COPY ... TO`` a file, or ``ATTACH`` anything.
* TTL enforced on every read, plus a periodic purge.
* One connection guarded by a lock; per-call cursors (DuckDB connections are not thread-safe).
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

import duckdb
import pandas as pd

_IDENT = re.compile(r"[^A-Za-z0-9_]")


def cache_name(source_table: str) -> str:
    """'DB.SCHEMA.TABLE' -> 'db_schema_table' (safe identifier)."""
    return _IDENT.sub("_", source_table).strip("_").lower() or "t"


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass
class CacheEntry:
    name: str
    source: str
    rows: int
    columns: list[str]
    decrypted_columns: list[str]
    loaded_at: float
    ttl_seconds: int
    decrypt_failures: int = 0

    @property
    def expires_at(self) -> float:
        return self.loaded_at + self.ttl_seconds

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at


@dataclass
class LocalCache:
    ttl_seconds: int = 3600
    memory_limit: str = "2GB"
    _conn: duckdb.DuckDBPyConnection = field(init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    entries: dict[str, CacheEntry] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._conn = duckdb.connect(":memory:")
        self._conn.execute(f"SET memory_limit = '{self.memory_limit.replace(chr(39), '')}'")
        self._conn.execute("SET enable_external_access = false")
        self._conn.execute("SET autoinstall_known_extensions = false")
        self._conn.execute("SET autoload_known_extensions = false")
        self._conn.execute("SET lock_configuration = true")

    # -- writes ---------------------------------------------------------------
    def put(self, source: str, df: pd.DataFrame, decrypted_columns: list[str], failures: int = 0,
            ttl_seconds: int | None = None) -> CacheEntry:
        name = cache_name(source)
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.register("_incoming", df)
                cur.execute(f"CREATE OR REPLACE TABLE {qident(name)} AS SELECT * FROM _incoming")
                cur.unregister("_incoming")
            finally:
                cur.close()
            entry = CacheEntry(name, source, len(df), list(df.columns), decrypted_columns, time.time(),
                               ttl_seconds or self.ttl_seconds, failures)
            self.entries[name] = entry
        return entry

    def drop(self, name: str) -> None:
        with self._lock:
            self._conn.execute(f"DROP TABLE IF EXISTS {qident(name)}")
            self.entries.pop(name, None)

    def clear(self) -> None:
        for name in list(self.entries):
            self.drop(name)

    def purge_expired(self) -> list[str]:
        now = time.time()
        gone = [n for n, e in self.entries.items() if e.expired(now)]
        for n in gone:
            self.drop(n)
        return gone

    def query_with_temp(self, sql: str, temps: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Run SQL with extra DataFrames visible as views for the duration of this call only."""
        self.purge_expired()
        with self._lock:
            cur = self._conn.cursor()
            try:
                for name, df in temps.items():
                    cur.register(name, df)
                return cur.execute(sql).fetchdf()
            finally:
                for name in temps:
                    try:
                        cur.unregister(name)
                    except Exception:
                        pass
                cur.close()

    # -- reads ----------------------------------------------------------------
    def live_entries(self) -> dict[str, CacheEntry]:
        self.purge_expired()
        return dict(self.entries)

    def has(self, name: str) -> bool:
        return name in self.live_entries()

    def query(self, sql: str) -> pd.DataFrame:
        """Run read-only SQL. Callers must validate with sql_guard first; expired tables are purged before."""
        self.purge_expired()
        with self._lock:
            cur = self._conn.cursor()
            try:
                return cur.execute(sql).fetchdf()
            finally:
                cur.close()

    def head(self, name: str, limit: int = 1000) -> pd.DataFrame:
        if not self.has(name):
            raise KeyError(name)
        return self.query(f"SELECT * FROM {qident(name)} LIMIT {int(limit)}")

    def stats(self) -> dict:
        e = self.live_entries()
        return {"tables": len(e), "rows": sum(x.rows for x in e.values()),
                "next_expiry": min((x.expires_at for x in e.values()), default=None)}
