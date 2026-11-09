"""Bake-off part 1 (Snowflake only): does the self-id join return the query's OWN query_id?

Scenarios: sync connector, async connector, two async queries in one session, inside a stored
procedure, inside a Snowflake Scripting block, as a UDF argument, behind a view.
Compares the self-id seen inside the query with the real query id (cursor.sfqid / LAST_QUERY_ID).

  pixi run python bench/selfid_contexts.py [runs]
"""
import json
import os
import sys
import time

import snowflake.connector

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
SELF = ("(SELECT query_id FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION()) "
        "ORDER BY start_time DESC LIMIT 1)")


def setup(c):
    cur = c.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW")
    cur.execute("USE SCHEMA SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW")
    cur.execute(f"CREATE OR REPLACE SECURE VIEW BENCH_SELF_ID_SECURE AS SELECT query_id FROM "
                f"TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION()) ORDER BY start_time DESC LIMIT 1")
    cur.execute(f"CREATE OR REPLACE VIEW BENCH_SELF_ID AS SELECT query_id FROM "
                f"TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION()) ORDER BY start_time DESC LIMIT 1")
    cur.execute("""CREATE OR REPLACE FUNCTION BENCH_ECHO(x STRING) RETURNS STRING
                   LANGUAGE PYTHON RUNTIME_VERSION='3.12' HANDLER='f' AS 'def f(x): return x'""")
    cur.execute(f"""CREATE OR REPLACE PROCEDURE BENCH_SP() RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS $$
        DECLARE self_id STRING; child_id STRING;
        BEGIN
          SELECT q.query_id INTO :self_id FROM {SELF} q;
          child_id := LAST_QUERY_ID();
          RETURN self_id || '|' || child_id;
        END $$""")


def sync_case(c, sql):
    cur = c.cursor()
    cur.execute(sql)
    seen = cur.fetchone()[0]
    return seen, cur.sfqid


def run():
    c = snowflake.connector.connect(connection_name=os.getenv("SNOW_CONNECTION", "default"))
    c.cursor().execute("USE ROLE ACCOUNTADMIN")
    c.cursor().execute("USE WAREHOUSE SOV_WH")
    setup(c)
    cases = {
        "sync_plain": f"SELECT q.query_id FROM {SELF} q",
        "sync_udf_arg": f"SELECT ANY_VALUE(BENCH_ECHO(q.query_id)) FROM TABLE(GENERATOR(ROWCOUNT=>5000)), {SELF} q",
        "sync_view": "SELECT query_id FROM BENCH_SELF_ID",
        "sync_secure_view": "SELECT query_id FROM BENCH_SELF_ID_SECURE",
        "sync_udf_arg_view": "SELECT ANY_VALUE(BENCH_ECHO(v.query_id)) FROM TABLE(GENERATOR(ROWCOUNT=>5000)), BENCH_SELF_ID v",
    }
    res = {}
    for name, sql in cases.items():
        ok, ms = 0, []
        try:
            for _ in range(RUNS):
                t = time.time()
                seen, real = sync_case(c, sql)
                ms.append((time.time() - t) * 1000)
                ok += seen == real
            res[name] = {"correct": ok, "runs": RUNS, "p50_ms": round(sorted(ms)[len(ms) // 2])}
        except Exception as e:
            res[name] = {"error": str(e)[:160]}

    # inside a stored procedure: self-id = the child SELECT, not the CALL
    ok_child = ok_call = 0
    for _ in range(RUNS):
        cur = c.cursor()
        cur.execute("CALL BENCH_SP()")
        self_id, child = cur.fetchone()[0].split("|")
        ok_child += self_id == child
        ok_call += self_id == cur.sfqid
    res["stored_proc"] = {"equals_child_select": ok_child, "equals_call": ok_call, "runs": RUNS}

    # async: single query, then two concurrent queries in the same session
    ok1 = 0
    for _ in range(RUNS):
        cur = c.cursor()
        cur.execute_async(f"SELECT q.query_id FROM {SELF} q")
        qid = cur.sfqid
        while c.is_still_running(c.get_query_status(qid)):
            time.sleep(0.05)
        cur.get_results_from_sfqid(qid)
        ok1 += cur.fetchone()[0] == qid
    res["async_single"] = {"correct": ok1, "runs": RUNS}

    ok2 = 0
    slow = (f"SELECT ANY_VALUE(q.query_id) FROM (SELECT SHA2(SEQ8()::STRING) h FROM TABLE(GENERATOR(ROWCOUNT=>3000000))) g, "
            f"{SELF} q")
    n_pairs = max(5, RUNS // 4)
    for _ in range(n_pairs):
        a, b = c.cursor(), c.cursor()
        a.execute_async(slow)
        b.execute_async(slow)
        got = 0
        for cur in (a, b):
            qid = cur.sfqid
            while c.is_still_running(c.get_query_status(qid)):
                time.sleep(0.05)
            cur.get_results_from_sfqid(qid)
            got += cur.fetchone()[0] == qid
        ok2 += got
    res["async_two_concurrent_same_session"] = {"correct": ok2, "runs": 2 * n_pairs}

    # Snowflake Scripting anonymous block (Snowsight "Run all" style)
    cur = c.cursor()
    cur.execute(f"""EXECUTE IMMEDIATE $$
        DECLARE s STRING; l STRING;
        BEGIN SELECT q.query_id INTO :s FROM {SELF} q; l := LAST_QUERY_ID(); RETURN s || '|' || l; END $$""")
    s, l = cur.fetchone()[0].split("|")
    res["scripting_block"] = {"self_equals_child": s == l, "self_equals_block": s == cur.sfqid}

    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    run()
