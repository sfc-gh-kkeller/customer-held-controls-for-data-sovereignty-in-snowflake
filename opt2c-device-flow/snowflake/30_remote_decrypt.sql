-- ============================================================================
-- Option 2d – remote decrypt: the data key never enters Snowflake.
-- Same device-code approval, API key and query binding as Option 2c, but instead of fetching the key the
-- vectorized UDF sends batches of ciphertext to the broker, which decrypts in AWS and returns plaintext.
-- Run after 10_objects.sql (reuses its EAI, SELF_QUERY view and API keys):
--   snow sql -c $SNOW_CONNECTION -f snowflake/30_remote_decrypt.sql -D "broker_host=<host>"
-- ============================================================================
USE ROLE ACCOUNTADMIN;
USE WAREHOUSE SOV_WH;
USE SCHEMA SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW;

-- One HTTPS call per batch (up to 2000 rows). strict = TRUE raises instead of returning NULL, so a denial or
-- a broken value can never be stored as NULL by CREATE TABLE AS or an incremental Dynamic Table.
-- VOLATILE: a Dynamic Table using it is not re-initialised when the function is replaced.
CREATE OR REPLACE FUNCTION SOV_DECRYPT_2D_PY(env STRING, classification STRING, api_key STRING, query_id STRING,
    account STRING, usr STRING, rol STRING, sess STRING, mode STRING, strict BOOLEAN)
  RETURNS STRING LANGUAGE PYTHON VOLATILE RUNTIME_VERSION = '3.12' HANDLER = 'decrypt_batch'
  EXTERNAL_ACCESS_INTEGRATIONS = (SOV_OPT2C_EAI) PACKAGES = ('pandas', 'requests')
AS $$
import pandas, requests
from _snowflake import vectorized
BROKER = "https://<% broker_host %>"
_http = requests.Session()

@vectorized(input=pandas.DataFrame, max_batch_size=2000)
def decrypt_batch(df):
    first = df.iloc[0]                     # all columns but the envelope are constant per query
    strict_mode = bool(first[9])
    items = [None if pandas.isna(v) else v for v in df[0]]
    body = {"api_key": first[2], "classification": first[1], "items": items,
            "ctx": {"account": first[4], "user": first[5], "role": first[6], "session": first[7],
                    "query_id": first[3], "mode": first[8]}}
    try:
        r = _http.post(f"{BROKER}/decrypt", json=body, timeout=30)
        out = r.json()
    except Exception as e:
        if strict_mode:
            raise RuntimeError(f"remote decrypt unavailable: {type(e).__name__}")
        return pandas.Series([None] * len(df))
    plain = out.get("plain")
    if plain is None:                      # denied: no grant, wrong session, query not bound, ...
        if strict_mode:
            raise PermissionError(f"remote decrypt denied: {out.get('reason')}")
        return pandas.Series([None] * len(df))
    bad = sum(1 for i, p in zip(items, plain) if i is not None and p is None)
    if bad and strict_mode:                # fail closed instead of committing NULLs
        raise ValueError(f"remote decrypt failed for {bad} of {len(items)} values")
    return pandas.Series(plain)
$$;

-- Analyst-facing wrappers: context comes from Snowflake (own query id via SELF_QUERY), not from the analyst.
CREATE OR REPLACE SECURE FUNCTION SOV_DECRYPT_2D(env STRING, classification STRING, api_key STRING)
  RETURNS STRING
AS $$
  SOV_DECRYPT_2D_PY(env, classification, api_key, (SELECT query_id FROM SELF_QUERY), CURRENT_ACCOUNT(),
                    CURRENT_USER(), CURRENT_ROLE(), CURRENT_SESSION(), 'A', FALSE)
$$;

CREATE OR REPLACE SECURE FUNCTION SOV_DECRYPT_2D_STRICT(env STRING, classification STRING, api_key STRING)
  RETURNS STRING
AS $$
  SOV_DECRYPT_2D_PY(env, classification, api_key, (SELECT query_id FROM SELF_QUERY), CURRENT_ACCOUNT(),
                    CURRENT_USER(), CURRENT_ROLE(), CURRENT_SESSION(), 'A', TRUE)
$$;

GRANT USAGE ON FUNCTION SOV_DECRYPT_2D(STRING, STRING, STRING) TO ROLE SOV_ANALYST;
GRANT USAGE ON FUNCTION SOV_DECRYPT_2D_STRICT(STRING, STRING, STRING) TO ROLE SOV_ANALYST;
