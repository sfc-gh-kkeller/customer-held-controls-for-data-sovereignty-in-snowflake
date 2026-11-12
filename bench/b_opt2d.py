"""Option 2d: device-code approval -> session API key -> remote decrypt in AWS (the key never enters Snowflake).

Same login as 2c; the vectorized UDF sends ciphertext batches to the broker and gets plaintext back.
Also checks fail-closed behaviour: forged key -> NULL, strict variant -> error instead of NULL.

  cd opt2c-device-flow && DEMO_USER=<user> pixi run python ../bench/b_opt2d.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opt2c-device-flow" / "bench"))
import e2e  # noqa: E402  (reuses session() and approve())
from common import run_query, save, stats, timer  # noqa: E402

AGG = ("SELECT department, COUNT(*) n, ROUND(AVG(TRY_TO_NUMBER(SOV_DECRYPT_2D(salary, 'salary', $api_key)))) "
       "avg_salary FROM EMPLOYEES_ENC GROUP BY 1 ORDER BY 1")
ROWS = ("SELECT employee_id, TRY_TO_NUMBER(SOV_DECRYPT_2D(salary, 'salary', $api_key)) s "
        "FROM EMPLOYEES_ENC ORDER BY 1")


def main():
    c, cur = e2e.session()
    res, checks = {}, {}
    t_login: list = []
    with timer(t_login):
        cur.execute("SELECT SOV_DEVICE_START()")
        d = json.loads(cur.fetchone()[0])
        e2e.approve(d["verification_uri"], d["user_code"])
        cur.execute(f"SET api_key = (SELECT SOV_DEVICE_REDEEM('{d['device_code']}'):api_key::STRING)")
    res["login_ms"] = stats(t_login)
    res["aggregate_200"] = run_query(cur, AGG)
    res["rows_200"] = run_query(cur, ROWS, runs=5)

    # fail-closed checks
    cur.execute("SELECT COUNT(*), COUNT(SOV_DECRYPT_2D(salary, 'salary', $api_key)) FROM EMPLOYEES_ENC")
    n, ok = cur.fetchone()
    checks["all_200_decrypted"] = (n, ok) == (200, 200)
    cur.execute("SELECT COUNT(SOV_DECRYPT_2D(salary, 'salary', 'nk2c_forged')) FROM EMPLOYEES_ENC")
    checks["forged_key_returns_null"] = cur.fetchone()[0] == 0
    try:
        cur.execute("SELECT COUNT(SOV_DECRYPT_2D_STRICT(full_name, 'pii', $api_key)) FROM EMPLOYEES_ENC")
        cur.fetchall()
        checks["strict_denied_raises"] = False
    except Exception as e:  # noqa: BLE001
        checks["strict_denied_raises"] = "denied" in str(e)
    cur.execute("SELECT COUNT(SOV_DECRYPT_2D_STRICT(salary, 'salary', $api_key)) FROM EMPLOYEES_ENC")
    checks["strict_allowed_works"] = cur.fetchone()[0] == 200
    cur.execute("SELECT employee_id, SOV_DECRYPT_2C(salary, 'salary', $api_key), "
                "SOV_DECRYPT_2D(salary, 'salary', $api_key) FROM EMPLOYEES_ENC ORDER BY 1 LIMIT 20")
    checks["same_values_as_2c_first_20"] = all(a == b for _, a, b in cur.fetchall())
    res["checks"] = checks
    save("opt2d", res)
    c.close()
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
