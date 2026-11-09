"""End-to-end Option 2c: device flow -> scoped API key -> query-bound key fetch -> decrypt.

Interactive by default (prints the URL + code, you approve on your phone/laptop).
--auto approves via the IdP form using the locally enrolled TOTP secret (demo/test only).

  pixi run e2e            # interactive
  pixi run e2e -- --auto  # unattended
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pyotp
import snowflake.connector

AUTO = "--auto" in sys.argv
USER = os.getenv("DEMO_USER", "ANALYST1").upper()
TOTP_FILE = Path(__file__).resolve().parents[2] / "opt2-keybroker/.secrets" / f"totp_{USER}.txt"


def session():
    c = snowflake.connector.connect(connection_name=os.getenv("SNOW_CONNECTION", "default"))
    cur = c.cursor()
    for s in ("USE ROLE SOV_ANALYST", "USE SECONDARY ROLES NONE", "USE WAREHOUSE SOV_WH",
              "USE SCHEMA SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW", "ALTER SESSION SET USE_CACHED_RESULT = FALSE"):
        cur.execute(s)
    return c, cur


def approve(uri: str, user_code: str, classes=("salary",)):
    form = [("user_code", user_code), ("user", USER), ("justification", "e2e test: quarterly salary review"),
            ("hours", "1"), ("totp", pyotp.TOTP(TOTP_FILE.read_text().strip()).now())]
    form += [("classes", c) for c in classes]
    req = urllib.request.Request(uri, data=urllib.parse.urlencode(form).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        assert r.status == 200, r.status


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    return cond


def main():
    c, cur = session()
    ok = True
    print("1. Start device flow")
    cur.execute("SELECT SOV_DEVICE_START()")
    d = json.loads(cur.fetchone()[0])
    print(f"   open {d['verification_uri_complete']}   code {d['user_code']}")

    cur.execute(f"SELECT SOV_DEVICE_REDEEM('{d['device_code']}')")
    ok &= check("redeem before approval is refused", json.loads(cur.fetchone()[0]).get("error") == "authorization_pending")

    if AUTO:
        approve(d["verification_uri"], d["user_code"])
    else:
        input("   approve in the browser, then press Enter ")

    print("2. Redeem -> API key in a session variable")
    cur.execute(f"SET api_key = (SELECT SOV_DEVICE_REDEEM('{d['device_code']}'):api_key::STRING)")
    redeem_qid = cur.sfqid
    cur.execute("SELECT LEFT($api_key, 5), LENGTH($api_key)")
    prefix, n = cur.fetchone()
    ok &= check("API key issued", prefix == "nk2c_", f"(len {n}, redeem query {redeem_qid})")

    cur.execute(f"SELECT SOV_DEVICE_REDEEM('{d['device_code']}')")
    ok &= check("device code is single use", json.loads(cur.fetchone()[0]).get("error") == "invalid_grant")

    print("3. Decrypt with the API key (query-bound key fetch)")
    t = time.time()
    cur.execute("SELECT employee_id, department, TRY_TO_NUMBER(SOV_DECRYPT_2C(salary, 'salary', $api_key)) s "
                "FROM EMPLOYEES_ENC ORDER BY employee_id")
    rows = cur.fetchall()
    ms = round((time.time() - t) * 1000)
    ok &= check("all 200 salaries decrypted", len(rows) == 200 and all(r[2] for r in rows),
                f"({ms} ms, e.g. {rows[0]})")

    cur.execute("SELECT COUNT(SOV_DECRYPT_2C(full_name, 'pii', $api_key)) FROM EMPLOYEES_ENC")
    ok &= check("classification not approved -> NULL", cur.fetchone()[0] == 0)

    cur.execute("SELECT COUNT(SOV_DECRYPT_2C(salary, 'salary', 'nk2c_forged')) FROM EMPLOYEES_ENC")
    ok &= check("forged API key -> NULL", cur.fetchone()[0] == 0)

    print("4. Same API key from another session of the same user")
    cur.execute("SELECT $api_key")
    key = cur.fetchone()[0]
    c2, cur2 = session()
    cur2.execute("SELECT COUNT(SOV_DECRYPT_2C(salary, 'salary', %s)) FROM EMPLOYEES_ENC", (key,))
    ok &= check("API key replayed in another session -> NULL", cur2.fetchone()[0] == 0)

    print("5. Direct broker call with a forged context (no running query)")
    out = json.load(open(Path(__file__).resolve().parents[2] / "opt2-keybroker/infra/outputs.json"))
    broker = out["SovereigntyKeyBroker"]["Opt2cBrokerUrl"].rstrip("/")
    cur.execute("SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE(), CURRENT_SESSION()::STRING")
    acct, usr, rol, sess = cur.fetchone()
    body = {"api_key": key, "classification": "salary",
            "ctx": {"account": acct, "user": usr, "role": rol, "session": sess, "query_id": redeem_qid, "mode": "A"}}
    req = urllib.request.Request(f"{broker}/key", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20)
        ok &= check("stolen key + finished query_id from outside Snowflake denied", False)
    except urllib.error.HTTPError as e:
        ok &= check("stolen key + finished query_id from outside Snowflake denied", e.code == 403,
                    json.loads(e.read()).get("reason", ""))

    print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
