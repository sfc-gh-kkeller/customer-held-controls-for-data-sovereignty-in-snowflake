"""Offline tests for the key broker decision logic (no AWS, no Snowflake).

  cd opt2-keybroker/client && uv run --with pytest pytest ../tests -q
"""
import importlib
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "lambda" / "broker"))
os.environ.update(GRANTS_TABLE="g", KEYS_TABLE="k", SF_SECRET_ARN="s", SNOWFLAKE_ACCOUNT_LOCATOR="AB12345",
                  AWS_DEFAULT_REGION="eu-central-1")


class FakeTable:
    def __init__(self, items):
        self.items = items

    def get_item(self, Key):
        k = tuple(Key.values())
        return {"Item": self.items[k]} if k in self.items else {}


@pytest.fixture
def broker(monkeypatch):
    import boto3
    monkeypatch.setattr(boto3, "resource", lambda *a, **k: types.SimpleNamespace(Table=lambda n: None))
    monkeypatch.setattr(boto3, "client", lambda *a, **k: None)
    h = importlib.reload(importlib.import_module("handler"))
    h.GRANTS = FakeTable({("ANALYST1", "salary"): {"expires_at": int(time.time()) + 300, "justification": "audit"}})
    h._data_key = lambda cls: b"\x01" * 32
    h._decisions.clear()
    state = {"status": "RUNNING", "calls": 0}

    def running(qid, user, role):
        state["calls"] += 1
        return (state["status"] == "RUNNING", "running" if state["status"] == "RUNNING" else "query_not_running")
    h._check_query_running = running
    return h, state


def call(h, qid="q1", user="ANALYST1", role="SOV_ANALYST", account="AB12345", rows=((0, "salary"),)):
    ev = {"headers": {"sf-external-function-current-query-id": qid, "sf-context-current-account": account,
                      "sf-context-current-user": user, "sf-context-current-role": role},
          "body": json.dumps({"data": [list(r) for r in rows]})}
    return json.loads(h.handler(ev, None)["body"])["data"]


def test_allow_with_grant_and_running_query(broker):
    h, st = broker
    assert call(h)[0][1] is not None


def test_one_callback_per_query_across_batches(broker):
    h, st = broker
    for _ in range(5):
        call(h, rows=[(i, "salary") for i in range(100)])
    assert st["calls"] == 1


def test_no_grant_denied(broker):
    h, st = broker
    assert call(h, user="MALLORY")[0][1] is None
    assert st["calls"] == 0          # never even reaches Snowflake


def test_wrong_account_denied(broker):
    h, _ = broker
    assert call(h, account="EVIL123")[0][1] is None


def test_replay_after_query_finished(broker):
    h, st = broker
    assert call(h)[0][1] is not None
    st["status"] = "SUCCESS"
    h._decisions[("q1", "ANALYST1", "SOV_ANALYST", "salary")] = (time.time() - 11, {"allow": True, "reason": "running"})
    assert call(h)[0][1] is None


def test_unknown_classification_fails_closed(broker):
    h, _ = broker
    h.GRANTS.items[("ANALYST1", "secret")] = {"expires_at": int(time.time()) + 60}
    def boom(cls):
        raise KeyError(cls)
    h._data_key = boom
    assert call(h, rows=[(0, "secret")])[0][1] is None
