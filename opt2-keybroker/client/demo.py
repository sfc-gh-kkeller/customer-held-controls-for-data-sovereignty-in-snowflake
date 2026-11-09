#!/usr/bin/env python3
"""Option 2 – scripted end-to-end demo.

  uv run demo.py            # full run
Scenarios:
  1. SOV_VIEWER                    -> **ENCRYPTED** (policy never asks for a key)
  2. SOV_ANALYST, no step-up       -> **KEY DENIED** (broker: no_stepup_grant)
  3. Step-up via portal (TOTP + justification, 5 min)
  4. SOV_ANALYST, with step-up     -> plaintext; aggregates work
  5. Replay attack: call the broker with a finished query_id -> denied (query_not_running)
  6. Key-fetch economics: broker invocations per query (from CloudWatch)
"""
import base64
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import boto3
import pyotp

HERE = Path(__file__).parent
OUT = json.load(open(HERE.parent / "infra" / "outputs.json"))["SovereigntyKeyBroker"]
CONN = os.getenv("SNOW_CONNECTION", "default")
_who = json.loads(subprocess.run(
    ["snow", "sql", "-c", CONN, "--format", "json", "-q", "SELECT CURRENT_USER() U, CURRENT_ACCOUNT() L"],
    capture_output=True, text=True, check=True).stdout)[0]
USER = (os.getenv("DEMO_USER") or _who["U"]).upper()     # the enrolled Snowflake user
ACCOUNT_LOCATOR = _who["L"]
REGION = "eu-central-1"
logs = boto3.client("logs", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)

Q = ("SELECT department, COUNT(*) n, ROUND(AVG(salary)) avg_salary, MAX(full_name) sample_name, "
     "MAX(salary_raw) sample_raw FROM SOVEREIGNTY_DEMO.OPT2_BYOK.EMPLOYEES GROUP BY 1 ORDER BY 1")


def banner(t):
    print("\n" + "=" * 78 + f"\n  {t}\n" + "=" * 78)


def sf(role: str, sql: str, tag: str = "sov_opt2_demo"):
    full = (f"USE ROLE {role}; USE SECONDARY ROLES NONE; USE WAREHOUSE SOV_WH; "
            f"ALTER SESSION SET USE_CACHED_RESULT=FALSE; "
            f"ALTER SESSION SET QUERY_TAG='{tag}'; {sql}; SELECT LAST_QUERY_ID() qid;")
    r = subprocess.run(["snow", "sql", "-c", CONN, "--format", "json", "-q", full],
                       capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        print(r.stdout[-800:], r.stderr[-800:])
        raise
    rows, qid = d[-2], d[-1][0]["QID"]
    return rows, qid


def show(rows):
    for r in rows:
        print("   ", r)


def stepup(cls: str, minutes: int, justification: str):
    secret = (HERE.parent / ".secrets" / f"totp_{USER}.txt").read_text().strip()
    body = json.dumps({"user": USER, "classification": cls, "minutes": minutes,
                       "justification": justification, "totp": pyotp.TOTP(secret).now()}).encode()
    req = urllib.request.Request(OUT["StepUpPortalUrl"].rstrip("/") + "/grant", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def revoke_all():
    t = boto3.resource("dynamodb", region_name=REGION).Table(OUT["GrantsTable"])
    for cls in ("salary", "pii"):
        t.delete_item(Key={"user": USER, "classification": cls})


def broker_audit(qid: str, wait_s: int = 20):
    """Return broker audit records for a query_id from CloudWatch Logs."""
    deadline = time.time() + wait_s
    recs = []
    while time.time() < deadline:
        ev = logs.filter_log_events(logGroupName=OUT["BrokerLogGroup"], filterPattern=f'"{qid}"',
                                    startTime=int((time.time() - 900) * 1000)).get("events", [])
        recs = [json.loads(e["message"]) for e in ev if e["message"].startswith("{")]
        if recs:
            time.sleep(3)
            ev = logs.filter_log_events(logGroupName=OUT["BrokerLogGroup"], filterPattern=f'"{qid}"',
                                        startTime=int((time.time() - 900) * 1000)).get("events", [])
            return [json.loads(e["message"]) for e in ev if e["message"].startswith("{")]
        time.sleep(3)
    return recs


def main():
    revoke_all()

    banner("1. SOV_VIEWER – no decryption rights at all")
    rows, _ = sf("SOV_VIEWER", Q)
    show(rows[:3])

    banner("2. SOV_ANALYST – no step-up grant yet")
    rows, qid = sf("SOV_ANALYST", Q)
    show(rows[:3])
    for a in broker_audit(qid):
        if "allow" in a:
            print(f"    broker decision: allow={a['allow']} reason={a['reason']} class={a['classification']}")

    banner("3. Step-up outside Snowflake: TOTP + justification, 5 minutes, salary+pii")
    for cls in ("salary", "pii"):
        print("   ", stepup(cls, 5, "Annual compensation review HR-2026-114, approved by CHRO"))

    banner("4. SOV_ANALYST – same query, now with a valid grant")
    rows, qid = sf("SOV_ANALYST", Q)
    show(rows)
    audit = broker_audit(qid)
    decisions = [a for a in audit if "allow" in a]
    batches = [a for a in audit if a.get("event") == "batch"]
    for a in decisions:
        print(f"    broker decision: allow={a['allow']} reason={a['reason']} class={a['classification']} "
              f"justification='{a.get('justification')}'")

    banner("5. Replay attack – reuse that (now finished) query_id against the broker directly")
    time.sleep(12)  # outlive the broker's 10 s allow-cache
    evt = {"headers": {"sf-external-function-current-query-id": qid,
                       "sf-context-current-account": ACCOUNT_LOCATOR,
                       "sf-context-current-user": USER, "sf-context-current-role": "SOV_ANALYST"},
           "body": json.dumps({"data": [[0, "salary"]]})}
    fn = OUT["BrokerLogGroup"].split("/")[-1]
    resp = json.loads(lam.invoke(FunctionName=fn, Payload=json.dumps(evt).encode())["Payload"].read())
    print("    broker returned:", json.loads(resp["body"]))
    print("    (allow-cache expired -> fresh call-back -> query is no longer RUNNING -> no key)")

    banner("6. Key-fetch economics for query " + qid)
    print(f"    external-function batches received by broker : {len(batches)}")
    print(f"    rows in those batches                         : {sum(b.get('rows', 0) for b in batches)}")
    print(f"    fresh authorization decisions (Snowflake call-back): {len(decisions)}")

    banner("7. Revoke (or wait for TTL) -> access gone immediately")
    revoke_all()
    rows, _ = sf("SOV_ANALYST", Q, tag="sov_opt2_after_revoke")
    show(rows[:2])


if __name__ == "__main__":
    main()
