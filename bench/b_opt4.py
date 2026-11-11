"""Option 4: ciphertext in Snowflake, decrypted only in the on-prem app.

Phases: fetch ciphertext (ADBC, PAT from .streamlit/secrets.toml) -> AES decrypt locally -> DuckDB aggregate.

  cd opt4-client-decrypt && pixi run python ../bench/b_opt4.py
"""
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
APP = Path(__file__).resolve().parents[1] / "opt4-client-decrypt"
sys.path.insert(0, str(APP))
import duckdb  # noqa: E402
from common import RUNS, save, stats, timer  # noqa: E402
from secure_analytics.connections import connect_snowflake  # noqa: E402
from secure_analytics.crypto import Decryptor  # noqa: E402

COLS = ["FULL_NAME", "NATIONAL_ID", "IBAN", "SALARY", "BONUS"]


def main():
    cfg = tomllib.loads((APP / ".streamlit" / "secrets.toml").read_text())
    s = cfg["snowflake"]
    table = cfg["encrypted_tables"][0]["table_name"]
    t_conn: list = []
    with timer(t_conn):
        src = connect_snowflake("bench", s["account"], s["user"], s["pat"], s["database"], s["schema"],
                                s["warehouse"])
    dec = Decryptor.from_b64(cfg["encryption"]["aes_key"])
    fetch, decrypt, agg, total = [], [], [], []
    for _ in range(RUNS + 1):
        with timer(total):
            with timer(fetch):
                df = src.query(f"SELECT * FROM {table}")
            with timer(decrypt):
                for col in COLS:
                    df[col] = df[col].map(dec.decrypt)
                df["SALARY"] = df["SALARY"].astype(float)
            with timer(agg):
                out = duckdb.query_df(df, "e", "SELECT department, COUNT(*) n, ROUND(AVG(salary)) avg_salary "
                                               "FROM e GROUP BY 1 ORDER BY 1").fetchall()
    drop = lambda xs: xs[1:]  # noqa: E731  first run reported in first_ms
    save("opt4", {
        "connect_ms": stats(t_conn),
        "first_ms": round(total[0]),
        "end_to_end": stats(drop(total)),
        "fetch_ciphertext": stats(drop(fetch)),
        "decrypt_1000_cells": stats(drop(decrypt)),
        "duckdb_aggregate": stats(drop(agg)),
        "rows": len(df), "decrypt_failures": dec.failures, "sample": [list(map(str, r)) for r in out[:2]],
    })
    src.close()


if __name__ == "__main__":
    main()
