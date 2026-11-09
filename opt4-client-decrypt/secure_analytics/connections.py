"""Source connections: Snowflake via ADBC (PAT or password), optional PostgreSQL via SQLAlchemy."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import pandas as pd


@dataclass
class Source:
    name: str
    kind: str                     # "snowflake" | "postgres"
    handle: Any = field(repr=False)
    info: dict[str, str] = field(default_factory=dict)   # non-secret display info only
    connected_at: float = field(default_factory=time.time)

    def query(self, sql: str, max_rows: int | None = None) -> pd.DataFrame:
        if self.kind == "snowflake":
            cur = self.handle.cursor()
            try:
                cur.execute(sql)
                table = cur.fetch_arrow_table()
            finally:
                cur.close()
            df = table.to_pandas()
        elif self.kind == "postgres":
            from sqlalchemy import text
            with self.handle.connect() as c:
                df = pd.read_sql(text(sql), c)
        else:
            raise ValueError(f"Unknown source kind {self.kind}")
        return df.head(max_rows) if max_rows else df

    def close(self) -> None:
        try:
            if self.kind == "snowflake":
                self.handle.close()
            else:
                self.handle.dispose()
        except Exception:
            pass


def snowflake_uri(account: str, user: str, secret: str, database: str = "", schema: str = "",
                  warehouse: str = "", role: str = "") -> str:
    """ADBC URI with credentials percent-encoded (PATs/passwords may contain '@', ':' or '/')."""
    uri = f"{quote(user, safe='')}:{quote(secret, safe='')}@{quote(account, safe='-_.')}"
    if database:
        uri += f"/{quote(database, safe='')}" + (f"/{quote(schema, safe='')}" if schema else "")
    params = [f"{k}={quote(v, safe='')}" for k, v in (("warehouse", warehouse), ("role", role)) if v]
    return uri + ("?" + "&".join(params) if params else "")


def _friendly(err: Exception) -> str:
    msg = str(err)
    low = msg.lower()
    if "network policy" in low or "not allowed to access" in low:
        hint = "Your IP is not allowed by the user's network policy."
    elif "token" in low or "authentication" in low or "login" in low or "incorrect username" in low:
        hint = "Authentication failed – check the user and that the PAT is valid and not expired."
    elif "warehouse" in low:
        hint = "Warehouse not found or not authorised for this role."
    elif "account" in low and ("invalid" in low or "not found" in low):
        hint = "Account identifier not recognised – use ORGNAME-ACCOUNTNAME."
    else:
        hint = "Connection failed."
    return f"{hint} ({msg.splitlines()[0][:200]})"


class ConnectError(RuntimeError):
    pass


def connect_snowflake(name: str, account: str, user: str, secret: str, database: str = "",
                      schema: str = "", warehouse: str = "", role: str = "") -> Source:
    import adbc_driver_snowflake.dbapi as sf
    if not (account and user and secret):
        raise ConnectError("Account, user and PAT/password are required.")
    try:
        conn = sf.connect(snowflake_uri(account, user, secret, database, schema, warehouse, role))
        cur = conn.cursor()
        try:
            cur.execute("SELECT CURRENT_VERSION(), CURRENT_ROLE(), CURRENT_WAREHOUSE()")
            version, cur_role, wh = cur.fetchone()
        finally:
            cur.close()
    except Exception as e:
        raise ConnectError(_friendly(e)) from None
    return Source(name, "snowflake", conn, {"account": account, "user": user, "role": str(cur_role),
                                            "warehouse": str(wh), "database": database, "schema": schema,
                                            "version": str(version)})


def connect_postgres(name: str, host: str, port: int, database: str, user: str, password: str) -> Source:
    from sqlalchemy import URL, create_engine, text
    try:
        engine = create_engine(URL.create("postgresql+psycopg2", username=user, password=password,
                                          host=host, port=int(port), database=database))
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:
        raise ConnectError(_friendly(e)) from None
    return Source(name, "postgres", engine, {"host": host, "database": database, "user": user})
