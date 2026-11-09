"""Option 2c – mock IdP with an OAuth 2.0 device authorization grant (RFC 8628).

Stands in for Okta / Entra ID device flow. One Lambda Function URL:

POST /device/authorize        -> {device_code, user_code, verification_uri, verification_uri_complete,
                                  expires_in, interval}
GET  /device[?user_code=X]    -> HTML page: user_code, Snowflake user, TOTP, classifications, justification
POST /device                  -> verifies TOTP (existing StepUpUsers table) and approves the device code
POST /token                   -> grant_type=urn:ietf:params:oauth:grant-type:device_code
                                 authorization_pending | expired_token | {id_token, ...}  (single use)

The id_token is a short-lived HS256 JWT (aud = sovereignty-key-broker) that carries the user, the
approved classifications and the justification. The broker exchanges it for a scoped API key.
"""
import base64
import html
import json
import os
import secrets as pysecrets
import time
import urllib.parse
import uuid

import boto3
import jwt
import pyotp

ddb = boto3.resource("dynamodb")
STATE = ddb.Table(os.environ["STATE_TABLE"])
USERS = ddb.Table(os.environ["USERS_TABLE"])
sm = boto3.client("secretsmanager")

ISSUER = "sovereignty-mock-idp"
AUDIENCE = "sovereignty-key-broker"
DEVICE_TTL_S = 600
POLL_INTERVAL_S = 2
ID_TOKEN_TTL_S = 300
MAX_API_KEY_HOURS = int(os.getenv("MAX_API_KEY_HOURS", "8"))
CLASSIFICATIONS = os.getenv("CLASSIFICATIONS", "salary,pii").split(",")
_signing_key = None


def _key() -> str:
    global _signing_key
    if _signing_key is None:
        _signing_key = json.loads(sm.get_secret_value(SecretId=os.environ["SIGNING_SECRET_ARN"])
                                  ["SecretString"])["hs256"]
    return _signing_key


def _audit(**kw):
    print(json.dumps({"audit": "sov_idp", "ts": time.time(), **kw}))


def _resp(status, body, ctype="application/json"):
    return {"statusCode": status, "headers": {"Content-Type": ctype, "Cache-Control": "no-store"},
            "body": body if isinstance(body, str) else json.dumps(body)}


def _params(event) -> dict:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    if "json" in (event.get("headers") or {}).get("content-type", ""):
        return json.loads(body or "{}")
    return {k: (v if len(v) > 1 else v[0]) for k, v in urllib.parse.parse_qs(body).items()}


def _base_url(event) -> str:
    return f"https://{event['requestContext']['domainName']}"


def _user_code() -> str:
    alphabet = "BCDFGHJKLMNPQRSTVWXZ"          # no vowels, no look-alikes (RFC 8628 §6.1)
    raw = "".join(pysecrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def authorize(event):
    body = json.loads(event.get("body") or "{}") if event.get("body") else {}
    hint = str(body.get("login_hint", "")).strip().upper()[:64]
    device_code = pysecrets.token_urlsafe(32)
    user_code = _user_code()
    exp = int(time.time()) + DEVICE_TTL_S
    STATE.put_item(Item={"pk": f"dc#{device_code}", "user_code": user_code, "status": "pending",
                         "ttl": exp, "expires_at": exp})
    STATE.put_item(Item={"pk": f"uc#{user_code}", "device_code": device_code, "ttl": exp})
    base = _base_url(event)
    q = urllib.parse.urlencode({"user_code": user_code, **({"login_hint": hint} if hint else {})})
    _audit(event="device_authorize", user_code=user_code, login_hint=hint)
    return _resp(200, {"device_code": device_code, "user_code": user_code,
                       "verification_uri": f"{base}/device",
                       "verification_uri_complete": f"{base}/device?{q}",
                       "qr_url": f"{base}/qr?{q}",
                       "expires_in": DEVICE_TTL_S, "interval": POLL_INTERVAL_S})


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Mock IdP – device sign-in</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:system-ui;max-width:520px;margin:32px auto;padding:0 12px;background:#fff;color:#111}}
label{{display:block;margin-top:12px}}input,textarea{{width:100%;padding:6px;box-sizing:border-box}}
button{{margin-top:16px;padding:8px 16px}}.msg{{padding:10px;margin-top:16px;background:#eef}}
.cls label{{display:inline;margin-right:16px}}.cls input{{width:auto}}</style></head><body>
<h2>Mock IdP &mdash; approve Snowflake key access</h2>
<p>A Snowflake session asked for an API key to fetch decryption keys. Approving issues a key
scoped to your user, this account and that one session only.</p>
<form method="post" action="device">
<label>Code shown in Snowflake<input name="user_code" value="{uc}" required pattern="[A-Z]{{4}}-[A-Z]{{4}}"></label>
<label>Snowflake user<input name="user" value="{user}" required autocapitalize="characters"></label>
<div class="cls"><p>Classifications</p>{cls}</div>
<label>Justification (audited)<textarea name="justification" required minlength="10"></textarea></label>
<label>API key lifetime (hours, max {maxh})<input name="hours" type="number" value="1" min="1" max="{maxh}"></label>
<label>Authenticator code (TOTP)<input name="totp" required pattern="[0-9]{{6}}" inputmode="numeric"
  autocomplete="one-time-code" maxlength="6" {focus}></label>
<button type="submit">Approve</button></form>{msg}</body></html>"""


def _page(uc="", msg="", user=""):
    cls = "".join(f'<label><input type="checkbox" name="classes" value="{html.escape(c)}" checked> '
                  f'{html.escape(c)}</label>' for c in CLASSIFICATIONS)
    return PAGE.format(uc=html.escape(uc), user=html.escape(user), cls=cls, maxh=MAX_API_KEY_HOURS,
                       focus="autofocus" if uc and user else "",
                       msg=f'<div class="msg">{msg}</div>' if msg else "")


QR_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Scan to approve</title>
<style>body{{font-family:system-ui;text-align:center;margin:32px;background:#fff;color:#111}}img{{background:#fff;padding:8px}}
.code{{font-size:40px;letter-spacing:6px;font-weight:700;margin:16px}}</style></head><body>
<h2>Scan with your phone to approve</h2>{svg}<div class="code">{uc}</div>
<p>or open <a href="{url}">{url_short}</a> and enter the code</p></body></html>"""


def qr_page(event):
    import io
    import qrcode
    import qrcode.image.svg
    qs = event.get("queryStringParameters") or {}
    uc = str(qs.get("user_code", "")).upper()[:9]
    hint = str(qs.get("login_hint", "")).upper()[:64]
    q = urllib.parse.urlencode({"user_code": uc, **({"login_hint": hint} if hint else {})})
    url = f"{_base_url(event)}/device?{q}"
    buf = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathFillImage, box_size=10, border=2).save(buf)
    svg = (f'<img alt="QR code" width="320" height="320" '
           f'src="data:image/svg+xml;base64,{base64.b64encode(buf.getvalue()).decode()}">')
    return _resp(200, QR_PAGE.format(svg=svg, uc=html.escape(uc), url=html.escape(url),
                                     url_short=html.escape(f"{_base_url(event)}/device")), "text/html")


def approve(p: dict, ip: str) -> tuple[bool, str]:
    uc = str(p.get("user_code", "")).strip().upper()
    user = str(p.get("user", "")).strip().upper()
    just = str(p.get("justification", "")).strip()
    classes = p.get("classes") or []
    classes = [classes] if isinstance(classes, str) else list(classes)
    classes = sorted({c.strip().lower() for c in classes if c.strip().lower() in CLASSIFICATIONS})
    hours = max(1, min(int(p.get("hours", 1) or 1), MAX_API_KEY_HOURS))
    link = STATE.get_item(Key={"pk": f"uc#{uc}"}).get("Item")
    if not link:
        return False, "unknown or expired code"
    dc = STATE.get_item(Key={"pk": f"dc#{link['device_code']}"}).get("Item")
    if not dc or dc["status"] != "pending" or int(dc["expires_at"]) < time.time():
        return False, "code already used or expired"
    if len(just) < 10:
        return False, "justification too short"
    if not classes:
        return False, "select at least one classification"
    u = USERS.get_item(Key={"user": user}).get("Item")
    if not u or not pyotp.TOTP(u["totp_secret"]).verify(str(p.get("totp", "")), valid_window=1):
        _audit(event="device_approve", user=user, user_code=uc, result="mfa_failed", ip=ip)
        return False, "MFA verification failed"
    denied = [c for c in classes if c not in u.get("allowed_classifications", [])]
    if denied:
        return False, f"user not entitled to {', '.join(denied)}"
    STATE.update_item(
        Key={"pk": f"dc#{link['device_code']}"},
        UpdateExpression="SET #s = :a, #u = :u, classes = :c, justification = :j, hours = :h, approved_at = :t",
        ConditionExpression="#s = :p",
        ExpressionAttributeNames={"#s": "status", "#u": "user"},
        ExpressionAttributeValues={":a": "approved", ":p": "pending", ":u": user, ":c": classes,
                                   ":j": just, ":h": hours, ":t": int(time.time())})
    _audit(event="device_approve", user=user, user_code=uc, result="approved", classes=classes,
           hours=hours, justification=just, ip=ip)
    return True, (f"Approved for {user}: {', '.join(classes)} for {hours} h. "
                  "Go back to Snowflake and run the redeem step.")


def token(p: dict):
    if p.get("grant_type") != "urn:ietf:params:oauth:grant-type:device_code":
        return _resp(400, {"error": "unsupported_grant_type"})
    pk = f"dc#{p.get('device_code', '')}"
    dc = STATE.get_item(Key={"pk": pk}).get("Item")
    if not dc or int(dc["expires_at"]) < time.time():
        return _resp(400, {"error": "expired_token"})
    if dc["status"] == "pending":
        return _resp(400, {"error": "authorization_pending"})
    if dc["status"] != "approved":
        return _resp(400, {"error": "invalid_grant"})
    try:   # single use: approved -> issued
        STATE.update_item(Key={"pk": pk}, UpdateExpression="SET #s = :i",
                          ConditionExpression="#s = :a", ExpressionAttributeNames={"#s": "status"},
                          ExpressionAttributeValues={":i": "issued", ":a": "approved"})
    except ddb.meta.client.exceptions.ConditionalCheckFailedException:
        return _resp(400, {"error": "invalid_grant"})
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": dc["user"], "iat": now, "exp": now + ID_TOKEN_TTL_S,
              "jti": str(uuid.uuid4()), "classes": list(dc["classes"]),
              "justification": dc["justification"], "api_key_hours": int(dc["hours"])}
    _audit(event="token_issued", user=dc["user"], jti=claims["jti"])
    return _resp(200, {"id_token": jwt.encode(claims, _key(), algorithm="HS256"),
                       "token_type": "Bearer", "expires_in": ID_TOKEN_TTL_S})


def handler(event, context):
    rc = event.get("requestContext", {}).get("http", {})
    method, path, ip = rc.get("method", "GET"), rc.get("path", "/").rstrip("/"), rc.get("sourceIp", "")
    if method == "POST" and path == "/device/authorize":
        return authorize(event)
    if method == "POST" and path == "/token":
        return token(_params(event))
    if method == "GET" and path == "/qr":
        return qr_page(event)
    if path == "/device":
        if method == "POST":
            p = _params(event)
            ok, msg = approve(p, ip)
            return _resp(200 if ok else 403, _page(p.get("user_code", ""), html.escape(msg), p.get("user", "")),
                         "text/html")
        qs = event.get("queryStringParameters") or {}
        return _resp(200, _page(qs.get("user_code", ""), user=qs.get("login_hint", "")), "text/html")
    return _resp(404, {"error": "not_found"})
