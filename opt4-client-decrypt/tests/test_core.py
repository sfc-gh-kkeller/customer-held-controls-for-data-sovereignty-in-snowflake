import base64
import os
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from secure_analytics import auth, smart_query
from secure_analytics.cache import LocalCache, cache_name
from secure_analytics.config import EncryptedTable, load_config, redact
from secure_analytics.crypto import DECRYPT_FAILED, Decryptor, KeyError_, looks_encrypted
from secure_analytics.decrypt_pipeline import load_table
from secure_analytics.sql_guard import UnsafeSQL, check_read_only

KEY = base64.b64encode(os.urandom(32)).decode()


# ---------------- crypto
def test_roundtrip_and_numeric():
    d = Decryptor.from_b64(KEY)
    for v in ["Alice Müller", "DE89370400440532013000", "123456.78", "x" * 100]:
        assert d.decrypt(d.encrypt(v)) == v
    assert d.failures == 0


def test_wrong_key_and_garbage_are_uniform():
    a, b = Decryptor.from_b64(KEY), Decryptor.from_b64(base64.b64encode(os.urandom(32)).decode())
    ct = a.encrypt("secret")
    assert b.decrypt(ct) == DECRYPT_FAILED
    assert b.decrypt("not-base64!!") == DECRYPT_FAILED
    assert b.decrypt(None) is None
    assert b.failures == 2


def test_key_validation():
    with pytest.raises(KeyError_):
        Decryptor.from_b64("")
    with pytest.raises(KeyError_):
        Decryptor.from_b64(base64.b64encode(b"short").decode())


def test_looks_encrypted():
    d = Decryptor.from_b64(KEY)
    assert looks_encrypted(d.encrypt("hello"))
    assert not looks_encrypted("Engineering")
    assert not looks_encrypted("a" * 60 + "!")


# ---------------- sql_guard
@pytest.mark.parametrize("sql", [
    "SELECT 1", "select * from a.b.c where x = 1;", "WITH t AS (SELECT 1 x) SELECT * FROM t",
    "SELECT a FROM x UNION ALL SELECT a FROM y",
])
def test_guard_allows(sql):
    check_read_only(sql)


@pytest.mark.parametrize("sql", [
    "DROP TABLE x", "DELETE FROM x", "INSERT INTO x VALUES (1)", "SELECT 1; DROP TABLE x",
    "COPY x TO '/tmp/out.csv'", "ATTACH '/tmp/x.db' AS y", "PRAGMA database_list",
    "SET enable_external_access = true", "UPDATE x SET a = 1", "CREATE TABLE y AS SELECT 1",
    "SELECT * INTO newt FROM x", "", "SELECT * FROM read_csv('/etc/hosts')",
    "SELECT * FROM read_parquet('s3://b/x.parquet')", "SELECT * FROM glob('/home/*')",
    "SELECT a FROM t WHERE a IN (SELECT * FROM read_text('/etc/passwd'))",
])
def test_guard_blocks(sql):
    with pytest.raises(UnsafeSQL):
        check_read_only(sql)


def test_guard_table_names_skip_ctes():
    c = check_read_only("WITH t AS (SELECT * FROM db.s.real) SELECT * FROM t JOIN other o ON 1=1")
    assert sorted(c.tables) == ["db.s.real", "other"]


# ---------------- cache
def test_cache_ttl_enforced_on_read():
    c = LocalCache(ttl_seconds=3600)
    c.put("DB.S.EMP", pd.DataFrame({"a": [1, 2]}), ["a"], ttl_seconds=1)
    assert c.has("db_s_emp")
    c.entries["db_s_emp"].loaded_at = time.time() - 5
    assert not c.has("db_s_emp")
    with pytest.raises(Exception):
        c.query('SELECT * FROM "db_s_emp"')


def test_cache_blocks_file_access():
    c = LocalCache()
    with pytest.raises(Exception):
        c.query("SELECT * FROM read_csv('/etc/hosts')")
    with pytest.raises(Exception):
        c.query("SET enable_external_access = true")


def test_cache_name():
    assert cache_name('SOV."X".EMP-1') == "sov__x__emp_1"


# ---------------- smart query
class FakeSource:
    name, kind = "fake", "snowflake"

    def __init__(self, tables):
        self.tables, self.calls = tables, []

    def query(self, sql, max_rows=None):
        self.calls.append(sql)
        for name, df in self.tables.items():
            if name.lower() in sql.lower():
                return df
        raise RuntimeError("no such table")


def test_smart_query_joins_cache_and_live():
    dec = Decryptor.from_b64(KEY)
    enc = pd.DataFrame({"ID": [1, 2], "DEPT": ["A", "B"], "SALARY": [dec.encrypt("100"), dec.encrypt("300")]})
    src = FakeSource({"DB.S.EMP_ENC": enc, "DB.S.DEPTS": pd.DataFrame({"DEPT": ["A", "B"], "NAME": ["Eng", "Ops"]})})
    cache = LocalCache()
    rep = load_table(src, EncryptedTable("DB.S.EMP_ENC", ["SALARY"]), dec, cache, 1000)
    assert rep.ok and rep.rows == 2 and rep.decrypted_columns == ["SALARY"]

    r = smart_query.run("SELECT AVG(salary) a FROM DB.S.EMP_ENC", "cache", cache, None, 100)
    assert r.df.iloc[0, 0] == 200

    with pytest.raises(UnsafeSQL):
        smart_query.run("SELECT * FROM DB.S.DEPTS", "cache", cache, src, 100)

    r = smart_query.run("SELECT d.NAME, SUM(e.SALARY) s FROM DB.S.EMP_ENC e JOIN DB.S.DEPTS d ON e.DEPT = d.DEPT "
                        "GROUP BY 1 ORDER BY 1", "smart", cache, src, 100)
    assert r.df["s"].tolist() == [100, 300] and r.live_tables == ["DB.S.DEPTS"]


def test_wildcard_detection():
    dec = Decryptor.from_b64(KEY)
    df = pd.DataFrame({"N": [dec.encrypt(f"n{i}") for i in range(5)], "P": ["plain"] * 5})
    cache = LocalCache()
    rep = load_table(FakeSource({"T": df}), EncryptedTable("T", ["*"]), dec, cache, 10)
    assert rep.decrypted_columns == ["N"]


# ---------------- config / auth
def test_config_and_redact(tmp_path):
    secrets = {"snowflake": {"account": "A", "user": "U", "pat": "SECRET"},
               "encryption": {"aes_key": KEY},
               "encrypted_tables": [{"table_name": "T", "encrypted_columns": ["X"]}],
               "roles": {"admin": ["Boss@Corp.com"], "analyst": ["@corp.com"]},
               "app": {"dev_login": True}}
    cfg = load_config(secrets, tmp_path / "settings.yaml")
    assert cfg.snowflake.pat == "SECRET" and cfg.dev_login and not cfg.oidc_configured
    cfg.settings["cache_ttl_minutes"] = 5
    cfg.settings["pat"] = "leak"
    cfg.save_settings()
    text = (tmp_path / "settings.yaml").read_text()
    assert "cache_ttl_minutes: 5" in text and "leak" not in text
    assert redact(secrets)["snowflake"]["pat"] == "•••"
    assert auth.resolve_role("boss@corp.com", cfg.roles) == "admin"
    assert auth.resolve_role("joe@corp.com", cfg.roles) == "analyst"
    assert auth.resolve_role("x@other.com", cfg.roles) == "viewer"
