"""Option 3: split storage, local join. Pull the public half from Snowflake, join on-prem in DuckDB with the local
Iceberg lake, write back only k-anonymous aggregates.

  cd opt3-local-join && pixi run python ../bench/b_opt3.py      (needs the on-prem lake: docker compose in opt1-tunnel)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import duckdb  # noqa: E402
from common import RUNS, connect, save, stats, timer  # noqa: E402
from snowflake.connector.pandas_tools import write_pandas  # noqa: E402

K = 5
JOIN = ("SELECT p.department, p.country, COUNT(*) headcount, ROUND(AVG(s.salary)) avg_salary "
        "FROM employees_public p JOIN lake.demo.employees_sensitive s USING (employee_id) "
        f"GROUP BY 1, 2 HAVING COUNT(*) >= {K} ORDER BY 1, 2")


def lake():
    con = duckdb.connect(":memory:")
    con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    con.execute("CREATE SECRET onprem_s3 (TYPE S3, KEY_ID 'admin', SECRET 'password', ENDPOINT 'localhost:9000', "
                "USE_SSL false, URL_STYLE 'path', REGION 'us-east-1')")
    con.execute("CREATE SECRET onprem_rest (TYPE ICEBERG, TOKEN 'dummy')")
    con.execute("ATTACH 'demo' AS lake (TYPE ICEBERG, ENDPOINT 'http://localhost:8181/catalog/', "
                "SECRET onprem_rest, ACCESS_DELEGATION_MODE 'none')")
    return con


def main():
    c, cur = connect("SOV_SPLIT_ROLE", "SOVEREIGNTY_DEMO.OPT3_SPLIT")
    t_lake: list = []
    with timer(t_lake):
        con = lake()
    pull, join, write, total = [], [], [], []
    for _ in range(RUNS + 1):
        with timer(total):
            with timer(pull):
                pub = cur.execute("SELECT * FROM EMPLOYEES_PUBLIC").fetch_pandas_all()
                pub.columns = [x.lower() for x in pub.columns]
            with timer(join):
                con.register("employees_public", pub)
                bands = con.execute(JOIN).fetchdf()
            with timer(write):
                bands.columns = [x.upper() for x in bands.columns]
                write_pandas(c, bands, "SALARY_BANDS_BENCH", auto_create_table=True, overwrite=True)
    cur.execute("DROP TABLE IF EXISTS SALARY_BANDS_BENCH")
    d = lambda xs: xs[1:]  # noqa: E731
    save("opt3", {"attach_lake_ms": stats(t_lake), "first_ms": round(total[0]), "end_to_end": stats(d(total)),
                  "pull_public": stats(d(pull)), "local_join_aggregate": stats(d(join)),
                  "write_back_aggregates": stats(d(write)), "rows_written": len(bands)})
    c.close()


if __name__ == "__main__":
    main()
