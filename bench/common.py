"""Shared helpers for the performance benchmarks (one script per option, each run in that option's pixi env).

Every benchmark runs the same logical workload where it applies:
    average salary per department over the 200 demo employees
and records client wall time per run plus, for Snowflake queries, the server-side elapsed time.
Results are appended as JSON to bench/results/<name>.json and summarised by bench/run_all.py.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "bench" / "results"
CONN = os.getenv("SNOW_CONNECTION", "default")
RUNS = int(os.getenv("BENCH_RUNS", "10"))


def stats(xs: list[float]) -> dict:
    """Summary in milliseconds."""
    if not xs:
        return {}
    s = sorted(xs)
    p95 = s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]
    return {"n": len(s), "p50": round(statistics.median(s)), "p95": round(p95), "min": round(s[0]),
            "max": round(s[-1]), "mean": round(statistics.fmean(s))}


@contextmanager
def timer(out: list):
    t = time.perf_counter()
    yield
    out.append((time.perf_counter() - t) * 1000)


def connect(role: str = "ACCOUNTADMIN", schema: str | None = None):
    import snowflake.connector
    c = snowflake.connector.connect(connection_name=CONN)
    cur = c.cursor()
    for s in (f"USE ROLE {role}", "USE SECONDARY ROLES NONE", "USE WAREHOUSE SOV_WH",
              "ALTER SESSION SET USE_CACHED_RESULT = FALSE", "ALTER SESSION SET QUERY_TAG = 'sov_bench'"):
        cur.execute(s)
    if schema:
        cur.execute(f"USE SCHEMA {schema}")
    return c, cur


def server_ms(cur, qids: list[str]) -> list[float]:
    """Server-side TOTAL_ELAPSED_TIME for the given query ids (this session's history)."""
    if not qids:
        return []
    time.sleep(1)
    cur.execute("SELECT query_id, total_elapsed_time FROM TABLE(SOVEREIGNTY_DEMO.INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION("
                "RESULT_LIMIT => 1000))")
    m = dict(cur.fetchall())
    return [float(m[q]) for q in qids if q in m]


def run_query(cur, sql: str, runs: int = RUNS, params=None) -> dict:
    """First run (cold-ish) reported separately, then `runs` measured runs."""
    first: list = []
    wall: list = []
    qids: list[str] = []
    with timer(first):
        cur.execute(sql, params)
        rows = cur.fetchall()
    first_qid = cur.sfqid
    for _ in range(runs):
        with timer(wall):
            cur.execute(sql, params)
            rows = cur.fetchall()
        qids.append(cur.sfqid)
    srv = server_ms(cur, qids + [first_qid])
    return {"first_ms": round(first[0]), "wall": stats(wall), "server": stats(srv[:-1] if len(srv) > len(qids) else srv),
            "rows": len(rows), "sample": [list(map(str, r)) for r in rows[:2]]}


def save(name: str, result: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    result = {"name": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **result}
    (RESULTS / f"{name}.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, indent=2, default=str))
