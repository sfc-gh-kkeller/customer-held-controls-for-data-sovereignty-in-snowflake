"""Option 2c: device-code approval -> session API key -> query-bound key fetch from an EAI UDF.

Approval is automated through the IdP form with the enrolled TOTP secret (demo only).

  cd opt2c-device-flow && DEMO_USER=<user> pixi run python ../bench/b_opt2c.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opt2c-device-flow" / "bench"))
import e2e  # noqa: E402  (reuses session() and approve())
from common import run_query, save, stats, timer, server_ms  # noqa: E402

AGG = ("SELECT department, COUNT(*) n, ROUND(AVG(TRY_TO_NUMBER(SOV_DECRYPT_2C(salary, 'salary', $api_key)))) "
       "avg_salary FROM EMPLOYEES_ENC GROUP BY 1 ORDER BY 1")
ROWS = ("SELECT employee_id, TRY_TO_NUMBER(SOV_DECRYPT_2C(salary, 'salary', $api_key)) s "
        "FROM EMPLOYEES_ENC ORDER BY 1")


def main():
    c, cur = e2e.session()
    res = {}
    t_start, t_approve, t_redeem = [], [], []
    with timer(t_start):
        cur.execute("SELECT SOV_DEVICE_START()")
        d = json.loads(cur.fetchone()[0])
    with timer(t_approve):
        e2e.approve(d["verification_uri"], d["user_code"])
    with timer(t_redeem):
        cur.execute(f"SET api_key = (SELECT SOV_DEVICE_REDEEM('{d['device_code']}'):api_key::STRING)")
    res["login_ms"] = {"device_start": stats(t_start), "approve_form_post": stats(t_approve),
                       "redeem": stats(t_redeem),
                       "note": "approval by a person adds their reaction time; here it is a scripted form post"}
    res["aggregate_200"] = run_query(cur, AGG)
    res["rows_200"] = run_query(cur, ROWS, runs=5)
    save("opt2c", res)
    c.close()


if __name__ == "__main__":
    main()
