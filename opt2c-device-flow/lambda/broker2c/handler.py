"""Option 2c – key broker for External Access UDFs (Lambda Function URL).

Routes (JSON in / JSON out):
  POST /apikey  {id_token, ctx}            -> {api_key, expires_at, bound_query_id}
  POST /key     {api_key, classification, ctx} -> {key_b64 | null, reason, bound_query_id}
  POST /decrypt {api_key, classification, ctx, items[]} -> {plain[] | null, reason, failed}
                Option 2d: same checks and binding as /key, but the broker decrypts the batch itself and
                the data key never leaves AWS (Snowflake sends ciphertext, gets plaintext back).
  POST /bind    {ctx, marker} + X-Bench-Token -> bake-off probe: binding only, no secrets

ctx = {account, user, role, session, query_id, mode}

Binding a request to "the query that is asking" – two modes, compared in the bake-off:
  A  self-id : the query passes its own query_id (QUERY_HISTORY_BY_SESSION self-join). The broker
               checks THAT id: RUNNING, same user / role / session, text calls the expected UDF.
  B  session : the broker resolves the single RUNNING query of that user in that session whose
               text calls the expected UDF.

Call-back economy: decisions are single-flighted per query in DynamoDB, so the 1..N UDF
processes of one query cause one Snowflake call-back, not N.
"""
import base64
import hashlib
import json
import os
import secrets as pysecrets
import time

import boto3
import jwt
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sfsql import SnowflakeSQL

ddb = boto3.resource("dynamodb")
STATE = ddb.Table(os.environ["STATE_TABLE"])
KEYS = ddb.Table(os.environ["KEYS_TABLE"])
kms = boto3.client("kms")
sm = boto3.client("secretsmanager")

EXPECTED_ACCOUNT = os.environ["SNOWFLAKE_ACCOUNT_LOCATOR"].upper()
AUDIENCE = "sovereignty-key-broker"
DECISION_TTL_S = int(os.getenv("DECISION_TTL_S", "300"))   # mode A: one decision per query_id
SESSION_TTL_S = int(os.getenv("SESSION_TTL_S", "3"))       # mode B: keyed by session, must be short
WAIT_S = 6.0
MAX_ITEMS = int(os.getenv("MAX_DECRYPT_ITEMS", "5000"))   # one vectorized UDF batch

_sf = None
_hs256 = None
_bench_token = None
_keys: dict[str, tuple[float, bytes]] = {}   # classification -> (fetched_at, plaintext data key)
KEY_CACHE_TTL_S = int(os.getenv("KEY_CACHE_TTL_S", "1800"))


# --------------------------------------------------------------------------- helpers
def _sf_client() -> SnowflakeSQL:
    global _sf
    if _sf is None:
        cfg = json.loads(sm.get_secret_value(SecretId=os.environ["SF_SECRET_ARN"])["SecretString"])
        _sf = SnowflakeSQL(cfg["account"], cfg["user"], cfg["private_key"], cfg["role"], cfg["warehouse"])
    return _sf


def _signing():
    global _hs256, _bench_token
    if _hs256 is None:
        s = json.loads(sm.get_secret_value(SecretId=os.environ["SIGNING_SECRET_ARN"])["SecretString"])
        _hs256, _bench_token = s["hs256"], s.get("bench_token", "")
    return _hs256, _bench_token


def _audit(**kw):
    print(json.dumps({"audit": "sov_keybroker_2c", "ts": time.time(), **kw}, default=str))


def _resp(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, default=str)}


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _data_key(cls: str) -> bytes:
    """Unwrapped data key, cached in this Lambda instance so KMS is not called per request. The cache entry is
    renewed after KEY_CACHE_TTL_S (default 30 min): a revoked KMS grant or a disabled key takes effect within that
    window, and the plaintext key does not live in memory indefinitely."""
    hit = _keys.get(cls)
    if hit and time.time() - hit[0] < KEY_CACHE_TTL_S:
        return hit[1]
    item = KEYS.get_item(Key={"classification": cls}).get("Item")
    if not item:
        raise KeyError(cls)
    key = kms.decrypt(CiphertextBlob=base64.b64decode(item["wrapped_key"]),
                      EncryptionContext={"classification": cls})["Plaintext"]
    _keys[cls] = (time.time(), key)
    return key


def _ctx(body: dict) -> dict:
    c = body.get("ctx") or {}
    return {"account": str(c.get("account", "")).upper(), "user": str(c.get("user", "")).upper(),
            "role": str(c.get("role", "")).upper(), "session": str(c.get("session", "")),
            "query_id": str(c.get("query_id") or ""), "mode": str(c.get("mode", "A")).upper()}


# --------------------------------------------------------------------------- binding
def _calls(query_text: str, marker: str) -> bool:
    """marker = 'FN1|FN2': the query text must call one of these functions."""
    t = (query_text or "").upper()
    return any(m in t for m in marker.upper().split("|"))


def _bind_a(ctx: dict, marker: str) -> tuple[bool, str, str]:
    """The claimed id must be RUNNING in that session, by that user/role, calling the UDF – and it
    must be the ONLY such query (two concurrent async queries in one session would otherwise let
    one query's self-id point at its sibling; the bake-off showed exactly that)."""
    if not ctx["query_id"]:
        return False, "missing_query_id", ""
    rows = _sf_client().query(
        "SELECT query_id, execution_status, user_name, role_name, query_text "
        "FROM TABLE(SOVEREIGNTY_DEMO.INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION("
        "SESSION_ID => TO_NUMBER(?), RESULT_LIMIT => 100)) "
        "WHERE query_id = ? OR execution_status = 'RUNNING'",
        [ctx["session"], ctx["query_id"]])
    me = [r for r in rows if r[0] == ctx["query_id"]]
    rows = me + [r for r in rows if r[0] != ctx["query_id"] and _calls(r[4], marker)]
    if not me:
        return False, "query_not_found_in_session", ""
    _, status, q_user, q_role, q_text = me[0]
    if status != "RUNNING":
        return False, f"query_not_running({status})", ""
    if q_user.upper() != ctx["user"] or q_role.upper() != ctx["role"]:
        return False, "query_user_or_role_mismatch", ""
    if not _calls(q_text, marker):
        return False, "query_text_mismatch", ""
    siblings = [r for r in rows if r[0] != ctx["query_id"] and r[1] == "RUNNING"]
    if siblings:
        return False, f"concurrent_queries_in_session({len(siblings) + 1})", ""
    return True, "running", ctx["query_id"]


def _bind_b(ctx: dict, marker: str) -> tuple[bool, str, str]:
    rows = _sf_client().query(
        "SELECT query_id, user_name, role_name, query_text "
        "FROM TABLE(SOVEREIGNTY_DEMO.INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION("
        "SESSION_ID => TO_NUMBER(?), RESULT_LIMIT => 100)) "
        "WHERE execution_status = 'RUNNING'",
        [ctx["session"]])
    rows = [r for r in rows if r[1].upper() == ctx["user"] and r[2].upper() == ctx["role"] and _calls(r[3], marker)]
    if not rows:
        return False, "no_running_query_in_session", ""
    if len(rows) > 1:
        return False, f"ambiguous_running_queries({len(rows)})", ""
    return True, "running", rows[0][0]


def _decision_key(ctx: dict, marker: str, scope: str) -> tuple[str, int]:
    if ctx["mode"] == "B":
        return f"dec#B#{scope}#{marker}#{ctx['session']}#{ctx['user']}#{ctx['role']}", SESSION_TTL_S
    return f"dec#A#{scope}#{marker}#{ctx['query_id']}#{ctx['user']}#{ctx['role']}", DECISION_TTL_S


def bind(ctx: dict, marker: str, scope: str = "") -> dict:
    """Single-flight call-back: the first caller for a query runs it, concurrent callers wait."""
    if ctx["account"] != EXPECTED_ACCOUNT:
        return {"ok": False, "reason": f"unexpected_account({ctx['account']})", "query_id": "", "callback": False}
    pk, ttl = _decision_key(ctx, marker, scope)
    now = time.time()
    try:
        STATE.put_item(Item={"pk": pk, "state": "pending", "ttl": int(now) + ttl + 60, "at": str(now)},
                       ConditionExpression="attribute_not_exists(pk) OR #t < :now",
                       ExpressionAttributeNames={"#t": "ttl"}, ExpressionAttributeValues={":now": int(now)})
        leader = True
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        leader = False

    if leader:
        t0 = time.time()
        try:
            ok, why, qid = (_bind_b if ctx["mode"] == "B" else _bind_a)(ctx, marker)
        except Exception as e:  # fail closed
            ok, why, qid = False, f"callback_error:{type(e).__name__}", ""
        ms = round((time.time() - t0) * 1000)
        STATE.put_item(Item={"pk": pk, "state": "done", "ok": ok, "reason": why, "query_id": qid,
                             "callback_ms": ms, "ttl": int(time.time()) + (ttl if ok else 10)})
        return {"ok": ok, "reason": why, "query_id": qid, "callback": True, "callback_ms": ms}

    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        item = STATE.get_item(Key={"pk": pk}, ConsistentRead=True).get("Item") or {}
        if item.get("state") == "done":
            return {"ok": bool(item["ok"]), "reason": item["reason"], "query_id": item.get("query_id", ""),
                    "callback": False, "callback_ms": int(item.get("callback_ms", 0))}
        time.sleep(0.05)
    return {"ok": False, "reason": "decision_timeout", "query_id": "", "callback": False}


# --------------------------------------------------------------------------- routes
def route_apikey(body: dict) -> dict:
    ctx = _ctx(body)
    hs256, _ = _signing()
    try:
        claims = jwt.decode(body.get("id_token", ""), hs256, algorithms=["HS256"], audience=AUDIENCE,
                            issuer="sovereignty-mock-idp")
    except jwt.PyJWTError as e:
        return _resp(401, {"error": f"invalid_id_token:{type(e).__name__}"})
    if claims["sub"].upper() != ctx["user"]:
        _audit(event="apikey", result="deny", reason="token_user_mismatch", ctx=ctx, sub=claims["sub"])
        return _resp(403, {"error": "token_user_mismatch"})
    try:   # single use per id_token
        STATE.put_item(Item={"pk": f"jti#{claims['jti']}", "ttl": int(claims["exp"]) + 60},
                       ConditionExpression="attribute_not_exists(pk)")
    except ClientError:
        return _resp(403, {"error": "id_token_replayed"})

    b = bind(ctx, "SOV_DEVICE_REDEEM", scope=claims["jti"])
    if not b["ok"]:
        _audit(event="apikey", result="deny", reason=b["reason"], ctx=ctx)
        return _resp(403, {"error": b["reason"]})

    api_key = "nk2c_" + pysecrets.token_urlsafe(32)
    exp = int(time.time()) + int(claims.get("api_key_hours", 1)) * 3600
    STATE.put_item(Item={"pk": f"ak#{_h(api_key)}", "user": ctx["user"], "account": ctx["account"],
                         "session": ctx["session"], "classes": claims["classes"],
                         "justification": claims["justification"], "issued_query_id": b["query_id"],
                         "issued_at": int(time.time()), "expires_at": exp, "ttl": exp})
    _audit(event="apikey", result="issued", ctx=ctx, bound_query_id=b["query_id"], classes=claims["classes"],
           justification=claims["justification"], expires_at=exp)
    return _resp(200, {"api_key": api_key, "expires_at": exp, "bound_query_id": b["query_id"]})


def route_key(body: dict) -> dict:
    ctx, cls, ak, b, deny = _authorize(body, "key", "SOV_GET_KEY_2C|SOV_DECRYPT_2C")
    if deny:
        return _resp(403, {"key_b64": None, "reason": deny})
    if b["callback"]:   # one audit line per query, not per process
        _audit(event="key", result="release", ctx=ctx, classification=cls, bound_query_id=b["query_id"],
               justification=ak["justification"], callback_ms=b.get("callback_ms"))
    return _resp(200, {"key_b64": base64.b64encode(_data_key(cls)).decode(), "reason": "released",
                       "bound_query_id": b["query_id"], "callback": b["callback"]})


def _authorize(body: dict, event: str, marker: str):
    """Shared checks for /key and /decrypt: API key scope first (no call-back), then the query binding.
    Returns (ctx, classification, api_key_item, binding, deny_reason)."""
    ctx = _ctx(body)
    cls = str(body.get("classification", "")).lower()
    ak_hash = _h(str(body.get("api_key", "")))
    ak = STATE.get_item(Key={"pk": f"ak#{ak_hash}"}).get("Item")
    reason = None
    if not ak:
        reason = "unknown_api_key"
    elif int(ak["expires_at"]) < time.time():
        reason = "api_key_expired"
    elif (ak["user"], ak["account"], ak["session"]) != (ctx["user"], ctx["account"], ctx["session"]):
        reason = "api_key_scope_mismatch"
    elif cls not in ak["classes"]:
        reason = "classification_not_in_api_key"
    if reason:   # cheap checks first: no call-back
        _audit(event=event, result="deny", reason=reason, ctx=ctx, classification=cls)
        return ctx, cls, ak, None, reason
    b = bind(ctx, marker, scope=f"{event}#{ak_hash[:16]}")
    if not b["ok"]:
        if b["callback"]:
            _audit(event=event, result="deny", reason=b["reason"], ctx=ctx, classification=cls)
        return ctx, cls, ak, b, b["reason"]
    return ctx, cls, ak, b, None


def _open(aes: AESGCM, env: str, aad: bytes) -> str:
    raw = base64.b64decode(env)            # iv[12] || ciphertext || tag[16], as in Option 2
    return aes.decrypt(raw[:12], raw[12:], aad).decode()


def route_decrypt(body: dict) -> dict:
    """Option 2d: decrypt a batch in AWS. Same authorisation as /key; the key is never returned."""
    items = body.get("items")
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        return _resp(413 if isinstance(items, list) else 400, {"plain": None, "reason": "bad_items"})
    ctx, cls, ak, b, deny = _authorize(body, "decrypt", "SOV_DECRYPT_2D")
    if deny:
        return _resp(403, {"plain": None, "reason": deny})
    aes, aad = AESGCM(_data_key(cls)), cls.encode()
    plain, failed = [], 0
    for env in items:
        if env is None:
            plain.append(None)
            continue
        try:
            plain.append(_open(aes, env, aad))
        except Exception:
            plain.append(None)
            failed += 1
    # One line per batch (a query sends one batch per UDF process): who, which query, how many values.
    # Never the values themselves.
    _audit(event="decrypt", result="ok", ctx=ctx, classification=cls, bound_query_id=b["query_id"],
           rows=len(items), failed=failed, justification=ak["justification"], callback=b["callback"])
    return _resp(200, {"plain": plain, "reason": "decrypted", "failed": failed,
                       "bound_query_id": b["query_id"]})


def route_bind(body: dict, headers: dict) -> dict:
    _, bench = _signing()
    if not bench or headers.get("x-bench-token") != bench:
        return _resp(403, {"error": "forbidden"})
    ctx = _ctx(body)
    t0 = time.time()
    if ctx["mode"] == "NONE":
        return _resp(200, {"ok": True, "reason": "baseline", "query_id": "", "callback": False, "broker_ms": 0})
    b = bind(ctx, str(body.get("marker", "SOV_BIND_PROBE")), scope=str(body.get("scope", "")))
    b["broker_ms"] = round((time.time() - t0) * 1000)
    return _resp(200, b)


def handler(event, context):
    rc = event.get("requestContext", {}).get("http", {})
    if rc.get("method") != "POST":
        return _resp(405, {"error": "method_not_allowed"})
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    body = json.loads(raw)
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    path = rc.get("path", "/").rstrip("/")
    try:
        if path == "/apikey":
            return route_apikey(body)
        if path == "/key":
            return route_key(body)
        if path == "/decrypt":
            return route_decrypt(body)
        if path == "/bind":
            return route_bind(body, headers)
    except Exception as e:  # fail closed
        import traceback
        print(json.dumps({"audit": "sov_keybroker_2c_error", "error": repr(e)[:300],
                          "trace": traceback.format_exc()[-1200:]}))
        return _resp(500, {"key_b64": None, "error": f"broker_error:{type(e).__name__}"})
    return _resp(404, {"error": "not_found"})
