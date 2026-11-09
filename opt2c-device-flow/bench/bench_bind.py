"""Bake-off part 2: mode A (self-id) vs mode B (session resolution) through the real broker.

Per mode and row count, runs N queries that call SOV_BIND_PROBE and records:
  correct      bound query_id == the real query id (cursor.sfqid)
  denied       broker refused (false deny when it should have allowed)
  callbacks    Snowflake call-backs per query (single-flight should give 1)
  procs        Python processes that called the broker
  wall / broker / callback latency

Extra scenarios: back-to-back queries (stale session cache for B), forged / finished query_id
for A, two concurrent async queries in one session.

  pixi run bench-2c [runs]
"""
import json
import os
import statistics
import sys
import time
import uuid

import snowflake.connector

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 15
ROWS = [10, 5000, 100000]
CTX = "CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE(), CURRENT_SESSION()::STRING"


def q_mode(mode: str, rows: int, scope: str) -> str:
    if mode == "A":
        src, qid = "TABLE(GENERATOR(ROWCOUNT=>{n})) g, SELF_QUERY s".format(n=rows), "s.query_id"
    else:
        src, qid = "TABLE(GENERATOR(ROWCOUNT=>{n})) g".format(n=rows), "NULL"
    return (f"SELECT ARRAY_AGG(DISTINCT p) FROM (SELECT SOV_BIND_PROBE('{mode}', {qid}, '{scope}', {CTX}) p "
            f"FROM {src})")


def parse(raw) -> list[dict]:
    return [json.loads(x) for x in json.loads(raw)]


def pct(v, p):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(len(v) * p))]) if v else None


def run_one(cur, sql):
    t = time.time()
    cur.execute(sql)
    wall = (time.time() - t) * 1000
    outs = parse(cur.fetchone()[0])
    return cur.sfqid, wall, outs


def summarize(name, recs):
    walls = [r["wall"] for r in recs]
    cbms = [o["callback_ms"] for r in recs for o in r["outs"] if o.get("callback")]
    return {"scenario": name, "runs": len(recs),
            "correct": sum(r["correct"] for r in recs),
            "denied": sum(r["denied"] for r in recs),
            "callbacks_per_query_max": max(r["callbacks"] for r in recs),
            "callbacks_per_query_avg": round(statistics.mean(r["callbacks"] for r in recs), 2),
            "procs_avg": round(statistics.mean(r["procs"] for r in recs), 1),
            "wall_p50_ms": pct(walls, .5), "wall_p95_ms": pct(walls, .95),
            "callback_p50_ms": pct(cbms, .5)}


def record(real, wall, outs, mode):
    ok = all(o.get("ok") for o in outs)
    bound = {o.get("query_id") for o in outs}
    return {"wall": wall, "outs": outs, "procs": len({o["proc"] for o in outs}),
            "callbacks": sum(1 for o in outs if o.get("callback")),
            "denied": not ok, "correct": ok and (mode == "NONE" or bound == {real})}


def main():
    c = snowflake.connector.connect(connection_name=os.getenv("SNOW_CONNECTION", "default"))
    cur = c.cursor()
    for s in ("USE ROLE SOV_ANALYST", "USE SECONDARY ROLES NONE", "USE WAREHOUSE SOV_WH",
              "USE SCHEMA SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW", "ALTER SESSION SET USE_CACHED_RESULT = FALSE"):
        cur.execute(s)
    cur.execute("SELECT 1")   # warm warehouse
    results = []

    # 1) latency / correctness / call-backs per mode and size. Spaced > SESSION_TTL_S so B is fair.
    for rows in ROWS:
        for mode in ("NONE", "A", "B"):
            recs = []
            for _ in range(RUNS):
                scope = uuid.uuid4().hex[:8] if mode != "B" else "spaced"
                real, wall, outs = run_one(cur, q_mode(mode, rows, scope))
                recs.append(record(real, wall, outs, mode))
                if mode == "B":
                    time.sleep(3.5)
            results.append(summarize(f"{mode}_rows{rows}", recs))
            print(json.dumps(results[-1]), flush=True)

    # 2) back-to-back queries with a constant scope (= same API key): stale-binding risk for B
    for mode in ("A", "B"):
        recs = []
        for _ in range(RUNS):
            real, wall, outs = run_one(cur, q_mode(mode, 10, "b2b"))
            recs.append(record(real, wall, outs, mode))
        results.append(summarize(f"{mode}_back_to_back", recs))
        print(json.dumps(results[-1]), flush=True)

    # 3) forged and finished query ids for A (must deny)
    cur.execute("SELECT 1")
    finished = cur.sfqid
    for label, fake in (("forged", str(uuid.uuid4())), ("finished", finished)):
        cur.execute(f"SELECT SOV_BIND_PROBE('A', '{fake}', '{uuid.uuid4().hex[:8]}', {CTX})")
        o = json.loads(cur.fetchone()[0])
        results.append({"scenario": f"A_{label}_query_id", "denied": not o["ok"], "reason": o["reason"]})
        print(json.dumps(results[-1]), flush=True)

    # 4) two concurrent async queries in ONE session
    for mode in ("A", "B"):
        good = deny = 0
        n = max(3, RUNS // 3)
        for _ in range(n):
            curs = [c.cursor(), c.cursor()]
            for k in curs:
                k.execute_async(q_mode(mode, 2_000_000, uuid.uuid4().hex[:8] if mode == "A" else "conc"))
            for k in curs:
                qid = k.sfqid
                while c.is_still_running(c.get_query_status(qid)):
                    time.sleep(0.1)
                k.get_results_from_sfqid(qid)
                outs = parse(k.fetchone()[0])
                ok = all(o["ok"] for o in outs)
                deny += not ok
                good += ok and {o["query_id"] for o in outs} == {qid}
            time.sleep(3.5)
        results.append({"scenario": f"{mode}_two_async_same_session", "runs": 2 * n, "correct": good,
                        "denied": deny})
        print(json.dumps(results[-1]), flush=True)

    with open("bench_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
