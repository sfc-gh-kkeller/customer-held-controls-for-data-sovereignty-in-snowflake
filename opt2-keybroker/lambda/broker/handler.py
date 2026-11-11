"""Option 2 – key broker (behind API Gateway, called as a Snowflake External Function).

A data key is released only if ALL of these hold:
  1. The request came through API Gateway (IAM-signed by the Snowflake API integration role).
  2. A non-expired step-up grant exists in DynamoDB for (user, classification).
  3. Call-back to Snowflake: the query_id in the request header is RUNNING, issued by that
     user, under that role, in this account – right now.
Otherwise the row gets NULL and the masking policy shows a denial marker.

Snowflake request headers used (added automatically / via CONTEXT_HEADERS):
  sf-external-function-current-query-id
  sf-context-current-account, sf-context-current-user, sf-context-current-role
"""
import base64
import json
import os
import time

import boto3

from sfsql import SnowflakeSQL

ddb = boto3.resource("dynamodb")
GRANTS = ddb.Table(os.environ["GRANTS_TABLE"])
KEYS = ddb.Table(os.environ["KEYS_TABLE"])
kms = boto3.client("kms")
secrets = boto3.client("secretsmanager")

EXPECTED_ACCOUNT = os.environ["SNOWFLAKE_ACCOUNT_LOCATOR"].upper()   # e.g. AB12345
# A cached ALLOW is only trusted briefly (covers the batches of one query); after that the
# RUNNING check is repeated, so a leaked query_id cannot be replayed once the query ends.
ALLOW_TTL_S = int(os.getenv("ALLOW_TTL_S", "10"))
DENY_TTL_S = int(os.getenv("DENY_TTL_S", "60"))

_sf = None
_decisions: dict[tuple, tuple[float, dict]] = {}   # (query_id, user, role, cls) -> (ts, decision)
_keys: dict[str, bytes] = {}                         # classification -> plaintext data key


def _sf_client() -> SnowflakeSQL:
    global _sf
    if _sf is None:
        cfg = json.loads(secrets.get_secret_value(SecretId=os.environ["SF_SECRET_ARN"])["SecretString"])
        _sf = SnowflakeSQL(cfg["account"], cfg["user"], cfg["private_key"], cfg["role"], cfg["warehouse"])
    return _sf


def _hdr(headers: dict, name: str) -> str:
    v = headers.get(name) or headers.get(name.lower()) or ""
    return v.strip()


def _audit(**kw):
    print(json.dumps({"audit": "sov_keybroker", "ts": time.time(), **kw}))


def _check_grant(user: str, cls: str) -> tuple[bool, str]:
    item = GRANTS.get_item(Key={"user": user, "classification": cls}).get("Item")
    if not item:
        return False, "no_stepup_grant"
    if int(item["expires_at"]) < time.time():
        return False, "stepup_grant_expired"
    return True, item.get("justification", "")


def _check_query_running(query_id: str, user: str, role: str) -> tuple[bool, str]:
    # A time window keeps the lookup fast: RESULT_LIMIT alone scans the user's last 1000 queries (~8-10 s for a
    # busy user), while END_TIME_RANGE_START answers in ~0.3 s. Running queries (no end time yet) are included.
    rows = _sf_client().query(
        "SELECT execution_status, user_name, role_name "
        "FROM TABLE(SOVEREIGNTY_DEMO.INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER("
        "USER_NAME => ?, END_TIME_RANGE_START => DATEADD(minute, -10, CURRENT_TIMESTAMP()), "
        "RESULT_LIMIT => 1000)) WHERE query_id = ?",
        [user, query_id])
    if not rows:
        return False, "query_not_found"
    status, q_user, q_role = rows[0]
    if status != "RUNNING":
        return False, f"query_not_running({status})"
    if q_user.upper() != user.upper():
        return False, "query_user_mismatch"
    if q_role.upper() != role.upper():
        return False, f"query_role_mismatch({q_role})"
    return True, "running"


def _data_key(cls: str) -> bytes:
    if cls not in _keys:
        item = KEYS.get_item(Key={"classification": cls}).get("Item")
        if not item:
            raise KeyError(cls)
        _keys[cls] = kms.decrypt(CiphertextBlob=base64.b64decode(item["wrapped_key"]),
                                 EncryptionContext={"classification": cls})["Plaintext"]
    return _keys[cls]


def decide(query_id: str, account: str, user: str, role: str, cls: str) -> dict:
    cache_key = (query_id, user, role, cls)
    hit = _decisions.get(cache_key)
    if hit and time.time() - hit[0] < (ALLOW_TTL_S if hit[1]["allow"] else DENY_TTL_S):
        return {**hit[1], "cached": True}

    if account.upper() != EXPECTED_ACCOUNT:
        d = {"allow": False, "reason": f"unexpected_account({account})"}
    else:
        ok, why = _check_grant(user, cls)
        if not ok:
            d = {"allow": False, "reason": why}
        else:
            ok2, why2 = _check_query_running(query_id, user, role)
            d = {"allow": ok2, "reason": why2, "justification": why}
    _decisions[cache_key] = (time.time(), d)
    return {**d, "cached": False}


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    query_id = _hdr(headers, "sf-external-function-current-query-id")
    account = _hdr(headers, "sf-context-current-account")
    user = _hdr(headers, "sf-context-current-user")
    role = _hdr(headers, "sf-context-current-role")

    rows = json.loads(event.get("body") or "{}").get("data", [])
    out = []
    for row in rows:
        row_no, cls = row[0], str(row[1]).lower()
        try:
            d = decide(query_id, account, user, role, cls)
            key_b64 = base64.b64encode(_data_key(cls)).decode() if d["allow"] else None
        except Exception as e:  # fail closed
            import traceback
            print(json.dumps({"audit": "sov_keybroker_error", "query_id": query_id,
                              "error": repr(e)[:300], "trace": traceback.format_exc()[-1200:]}))
            d = {"allow": False, "reason": f"broker_error:{type(e).__name__}", "cached": False}
            key_b64 = None
        if not d.get("cached"):
            _audit(query_id=query_id, account=account, user=user, role=role,
                   classification=cls, allow=d["allow"], reason=d["reason"],
                   justification=d.get("justification"))
        out.append([row_no, key_b64])

    _audit(event="batch", query_id=query_id, rows=len(rows))
    return {"statusCode": 200, "body": json.dumps({"data": out})}
