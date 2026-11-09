"""Unit tests for the Option 2c broker and mock IdP (no AWS, no Snowflake)."""
import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path

import jwt
import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
os.environ.update({"AWS_DEFAULT_REGION": "eu-central-1", "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_CONFIG_FILE": "/dev/null", "STATE_TABLE": "s", "KEYS_TABLE": "k",
                   "USERS_TABLE": "u", "SF_SECRET_ARN": "x", "SIGNING_SECRET_ARN": "y",
                   "SNOWFLAKE_ACCOUNT_LOCATOR": "AB12345"})
HS = "unit-test-signing-key-0123456789abcdef"


class FakeTable:
    """Just enough DynamoDB: get/put/update with the condition expressions the code uses."""
    def __init__(self):
        self.items = {}

    def get_item(self, Key, **_):
        k = next(iter(Key.values()))
        return {"Item": dict(self.items[k])} if k in self.items else {}

    def put_item(self, Item, ConditionExpression=None, ExpressionAttributeValues=None, **_):
        k = Item.get("pk") or next(iter(Item.values()))
        cur = self.items.get(k)
        if ConditionExpression and cur is not None:
            expired = "#t < :now" in ConditionExpression and cur["ttl"] < ExpressionAttributeValues[":now"]
            if not expired:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[k] = dict(Item)

    def update_item(self, Key, ExpressionAttributeValues, ConditionExpression=None, **kw):
        k = next(iter(Key.values()))
        cur = self.items[k]
        v = ExpressionAttributeValues
        if ConditionExpression and cur.get("status") != v.get(":p", v.get(":a")):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if ":i" in v:
            cur["status"] = v[":i"]
        else:
            cur.update({"status": v[":a"], "user": v[":u"], "classes": v[":c"], "justification": v[":j"],
                        "hours": v[":h"]})


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(mod)
    sys.path.pop(0)
    return mod


@pytest.fixture
def broker(monkeypatch):
    b = load("broker2c", ROOT / "lambda/broker2c/handler.py")
    b.STATE = FakeTable()
    monkeypatch.setattr(b, "_signing", lambda: (HS, "bench"))
    monkeypatch.setattr(b, "_data_key", lambda cls: b"\x01" * 32)
    b.calls = []
    running = {"qid": "01-run", "user": "ANALYST1", "role": "SOV_ANALYST", "session": "42",
               "text": "SELECT SOV_DEVICE_REDEEM(...) SOV_DECRYPT_2C(...)"}

    class FakeSF:
        def query(self, sql, binds):
            b.calls.append(sql)
            r = running
            row = [r["qid"], "RUNNING", r["user"], r["role"], r["text"]]
            if r"query_id = ?" in sql:                # mode A: (session, claimed id)
                if binds[0] != r["session"]:
                    return []
                return [row] + [[s, "RUNNING", r["user"], r["role"], r["text"]] for s in r.get("siblings", [])]
            if binds[0] != r["session"]:              # mode B: (session)
                return []
            return [[r["qid"], r["user"], r["role"], r["text"]]]
    monkeypatch.setattr(b, "_sf_client", lambda: FakeSF())
    b.running = running
    return b


def ctx(**kw):
    c = {"account": "AB12345", "user": "ANALYST1", "role": "SOV_ANALYST", "session": "42",
         "query_id": "01-run", "mode": "A"}
    c.update(kw)
    return c


def id_token(sub="ANALYST1", **kw):
    now = int(time.time())
    claims = {"iss": "sovereignty-mock-idp", "aud": "sovereignty-key-broker", "sub": sub, "iat": now, "exp": now + 300,
              "jti": f"j{time.time_ns()}", "classes": ["salary"], "justification": "quarterly review",
              "api_key_hours": 1, **kw}
    return jwt.encode(claims, HS, algorithm="HS256")


def call(b, path, body):
    r = b.handler({"requestContext": {"http": {"method": "POST", "path": path}}, "body": json.dumps(body),
                   "headers": {"x-bench-token": "bench"}}, None)
    return r["statusCode"], json.loads(r["body"])


def issue(b, **c):
    return call(b, "/apikey", {"id_token": id_token(), "ctx": ctx(**c)})


def test_happy_path_mode_a(broker):
    st, out = issue(broker)
    assert st == 200 and out["api_key"].startswith("nk2c_") and out["bound_query_id"] == "01-run"
    st, k = call(broker, "/key", {"api_key": out["api_key"], "classification": "salary", "ctx": ctx()})
    assert st == 200 and k["key_b64"] and k["bound_query_id"] == "01-run"


def test_api_key_stored_hashed(broker):
    _, out = issue(broker)
    assert not any(out["api_key"] in json.dumps(v, default=str) for v in broker.STATE.items.values())


def test_id_token_replay_and_forgery(broker):
    tok = id_token()
    assert call(broker, "/apikey", {"id_token": tok, "ctx": ctx()})[0] == 200
    assert call(broker, "/apikey", {"id_token": tok, "ctx": ctx()})[1]["error"] == "id_token_replayed"
    bad = jwt.encode({"sub": "ANALYST1", "aud": "sovereignty-key-broker"}, "wrong", algorithm="HS256")
    assert call(broker, "/apikey", {"id_token": bad, "ctx": ctx()})[0] == 401
    assert call(broker, "/apikey", {"id_token": id_token(sub="ALICE"), "ctx": ctx()})[0] == 403


def test_forged_or_finished_query_id_denied(broker):
    st, out = issue(broker, query_id="01-forged")
    assert st == 403 and out["error"] == "query_not_found_in_session"


def test_concurrent_queries_in_session_fail_closed(broker):
    broker.running["siblings"] = ["01-sibling"]
    st, out = issue(broker)
    assert st == 403 and out["error"].startswith("concurrent_queries_in_session")


def test_scope_mismatch_no_callback(broker):
    _, out = issue(broker)
    n = len(broker.calls)
    for c in (ctx(session="99"), ctx(user="ALICE"), ctx(account="OTHER1")):
        st, k = call(broker, "/key", {"api_key": out["api_key"], "classification": "salary", "ctx": c})
        assert st == 403 and k["key_b64"] is None and k["reason"] == "api_key_scope_mismatch"
    assert len(broker.calls) == n           # cheap checks first: no Snowflake call-back
    st, k = call(broker, "/key", {"api_key": out["api_key"], "classification": "pii", "ctx": ctx()})
    assert k["reason"] == "classification_not_in_api_key"


def test_expired_api_key(broker):
    _, out = issue(broker)
    for v in broker.STATE.items.values():
        if v["pk"].startswith("ak#"):
            v["expires_at"] = int(time.time()) - 1
    st, k = call(broker, "/key", {"api_key": out["api_key"], "classification": "salary", "ctx": ctx()})
    assert k["reason"] == "api_key_expired"


def test_single_flight_one_callback_per_query(broker):
    _, out = issue(broker)
    n = len(broker.calls)
    for _ in range(8):   # 8 UDF processes of one query
        st, k = call(broker, "/key", {"api_key": out["api_key"], "classification": "salary", "ctx": ctx()})
        assert st == 200
    assert len(broker.calls) == n + 1


def test_query_not_running_denies_key(broker):
    _, out = issue(broker)
    broker.running["qid"] = "01-next"   # the redeem query ended; a new query must bind itself
    st, k = call(broker, "/key", {"api_key": out["api_key"], "classification": "salary",
                                  "ctx": ctx(query_id="01-run")})
    assert st == 403 and k["reason"] == "query_not_found_in_session"


def test_mode_b_resolves_session(broker):
    st, out = call(broker, "/bind", {"marker": "SOV_DECRYPT_2C", "ctx": ctx(mode="B", query_id="")})
    assert st == 200 and out["ok"] and out["query_id"] == "01-run"


def test_bind_requires_bench_token(broker):
    r = broker.handler({"requestContext": {"http": {"method": "POST", "path": "/bind"}},
                        "body": json.dumps({"ctx": ctx()}), "headers": {}}, None)
    assert r["statusCode"] == 403


# ------------------------------------------------------------------ mock IdP
@pytest.fixture
def idp(monkeypatch):
    sys.modules.setdefault("pyotp", types.SimpleNamespace())
    m = load("idp", ROOT / "lambda/idp/handler.py")
    m.STATE, m.USERS = FakeTable(), FakeTable()
    m.USERS.items["ANALYST1"] = {"user": "ANALYST1", "totp_secret": "S", "allowed_classifications": ["salary"]}
    monkeypatch.setattr(m, "_key", lambda: HS)
    monkeypatch.setattr(m.pyotp, "TOTP", lambda s: types.SimpleNamespace(verify=lambda c, valid_window=1: c == "123456"),
                        raising=False)
    return m


def ev(path, body=None, method="POST"):
    return {"requestContext": {"domainName": "idp.example", "http": {"method": method, "path": path}},
            "body": json.dumps(body or {}), "headers": {"content-type": "application/json"}}


def test_device_flow(idp):
    a = json.loads(idp.handler(ev("/device/authorize"), None)["body"])
    tok = {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": a["device_code"]}
    assert json.loads(idp.handler(ev("/token", tok), None)["body"])["error"] == "authorization_pending"
    base = {"user_code": a["user_code"], "user": "analyst1", "classes": ["salary"], "justification": "quarterly review"}
    assert idp.handler(ev("/device", {**base, "totp": "000000"}), None)["statusCode"] == 403
    assert idp.handler(ev("/device", {**base, "classes": ["pii"], "totp": "123456"}), None)["statusCode"] == 403
    assert idp.handler(ev("/device", {**base, "totp": "123456"}), None)["statusCode"] == 200
    r = json.loads(idp.handler(ev("/token", tok), None)["body"])
    claims = jwt.decode(r["id_token"], HS, algorithms=["HS256"], audience="sovereignty-key-broker")
    assert claims["sub"] == "ANALYST1" and claims["classes"] == ["salary"]
    assert json.loads(idp.handler(ev("/token", tok), None)["body"])["error"] == "invalid_grant"   # single use
