"""Option 2 – step-up portal (Lambda Function URL).

GET  /          -> HTML form
POST /grant     -> {user, totp, classification, justification, minutes}
                   verifies TOTP (enrolled out-of-band by an admin), writes a
                   time-boxed grant to DynamoDB (TTL attribute = expires_at).
GET  /grants?user=X -> active grants for X (for the demo UI)
"""
import base64
import html
import json
import os
import time
import urllib.parse

import boto3
import pyotp
from boto3.dynamodb.conditions import Key

ddb = boto3.resource("dynamodb")
GRANTS = ddb.Table(os.environ["GRANTS_TABLE"])
USERS = ddb.Table(os.environ["USERS_TABLE"])
MAX_MINUTES = int(os.getenv("MAX_GRANT_MINUTES", "60"))
CLASSIFICATIONS = os.getenv("CLASSIFICATIONS", "salary,pii").split(",")

FORM = """<!doctype html><html><head><meta charset="utf-8"><title>Key access step-up</title>
<style>body{{font-family:system-ui;max-width:520px;margin:40px auto}}label{{display:block;margin-top:12px}}
input,select,textarea{{width:100%;padding:6px}}button{{margin-top:16px;padding:8px 16px}}
.msg{{padding:10px;margin-top:16px;background:#eef}}</style></head><body>
<h2>Decryption key access &mdash; step-up</h2>
<p>Access to decryption keys is time-boxed and requires MFA plus a justification.
Snowflake never stores the key; it is released per running query only.</p>
<form method="post" action="grant">
<label>Snowflake user<input name="user" required></label>
<label>Data classification<select name="classification">{opts}</select></label>
<label>Justification (audited)<textarea name="justification" required minlength="10"></textarea></label>
<label>Duration (minutes, max {max})<input name="minutes" type="number" value="15" min="1" max="{max}"></label>
<label>Authenticator code (TOTP)<input name="totp" required pattern="[0-9]{{6}}"></label>
<button type="submit">Request access</button></form>{msg}</body></html>"""


def _resp(status, body, ctype="text/html"):
    return {"statusCode": status, "headers": {"Content-Type": ctype}, "body": body}


def _form(msg=""):
    opts = "".join(f"<option>{html.escape(c)}</option>" for c in CLASSIFICATIONS)
    return FORM.format(opts=opts, max=MAX_MINUTES,
                       msg=f'<div class="msg">{msg}</div>' if msg else "")


def _params(event):
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    ctype = (event.get("headers") or {}).get("content-type", "")
    if "json" in ctype:
        return json.loads(body or "{}")
    return {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}


def grant(p, source_ip):
    user = str(p.get("user", "")).strip().upper()
    cls = str(p.get("classification", "")).strip().lower()
    just = str(p.get("justification", "")).strip()
    minutes = max(1, min(int(p.get("minutes", 15)), MAX_MINUTES))
    if cls not in CLASSIFICATIONS:
        return False, "unknown classification"
    if len(just) < 10:
        return False, "justification too short"
    u = USERS.get_item(Key={"user": user}).get("Item")
    if not u or not pyotp.TOTP(u["totp_secret"]).verify(str(p.get("totp", "")), valid_window=1):
        print(json.dumps({"audit": "sov_stepup", "user": user, "classification": cls,
                          "result": "mfa_failed", "ip": source_ip}))
        return False, "MFA verification failed"
    if cls not in u.get("allowed_classifications", []):
        return False, f"user not entitled to '{cls}'"
    exp = int(time.time()) + minutes * 60
    GRANTS.put_item(Item={"user": user, "classification": cls, "justification": just,
                          "granted_at": int(time.time()), "expires_at": exp, "source_ip": source_ip})
    print(json.dumps({"audit": "sov_stepup", "user": user, "classification": cls,
                      "result": "granted", "minutes": minutes, "justification": just, "ip": source_ip}))
    return True, f"Granted '{cls}' to {user} for {minutes} min (until {time.strftime('%H:%M:%S UTC', time.gmtime(exp))})"


def handler(event, context):
    rc = event.get("requestContext", {}).get("http", {})
    method, path = rc.get("method", "GET"), rc.get("path", "/")
    ip = rc.get("sourceIp", "")
    if method == "POST" and path.endswith("/grant"):
        ok, msg = grant(_params(event), ip)
        if "json" in (event.get("headers") or {}).get("content-type", ""):
            return _resp(200 if ok else 403, json.dumps({"ok": ok, "message": msg}), "application/json")
        return _resp(200 if ok else 403, _form(html.escape(msg)))
    if method == "GET" and path.endswith("/grants"):
        user = (event.get("queryStringParameters") or {}).get("user", "").upper()
        items = GRANTS.query(KeyConditionExpression=Key("user").eq(user)).get("Items", [])
        active = [{"classification": i["classification"], "expires_at": int(i["expires_at"])}
                  for i in items if int(i["expires_at"]) > time.time()]
        return _resp(200, json.dumps(active), "application/json")
    return _resp(200, _form())
