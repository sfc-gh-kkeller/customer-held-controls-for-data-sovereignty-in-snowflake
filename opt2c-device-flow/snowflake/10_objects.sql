-- ============================================================================
-- Option 2c – device-code justification, scoped API key, query-bound key fetch
-- Run after the CDK stack is deployed (setup.sh fills the <% %> variables):
--   idp_host, broker_host, bench_token
-- ============================================================================
USE ROLE ACCOUNTADMIN;
USE WAREHOUSE SOV_WH;
CREATE SCHEMA IF NOT EXISTS SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW;
USE SCHEMA SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW;

-- ---------------------------------------------------------------- egress: only IdP + broker
CREATE OR REPLACE NETWORK RULE OPT2C_EGRESS
  MODE = EGRESS TYPE = HOST_PORT
  VALUE_LIST = ('<% idp_host %>:443', '<% broker_host %>:443');

-- Only used by the bake-off probe UDF (not needed for the demo itself)
CREATE OR REPLACE SECRET OPT2C_BENCH_TOKEN TYPE = GENERIC_STRING SECRET_STRING = '<% bench_token %>';

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION SOV_OPT2C_EAI
  ALLOWED_NETWORK_RULES = (SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW.OPT2C_EGRESS)
  ALLOWED_AUTHENTICATION_SECRETS = (SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW.OPT2C_BENCH_TOKEN)
  ENABLED = TRUE;

-- ---------------------------------------------------------------- data: copy without masking policy
-- Same ciphertext and keys as Option 2. The Option 2 masking policies are lifted for the copy only.
ALTER TABLE SOVEREIGNTY_DEMO.OPT2_BYOK.EMPLOYEES_ENC MODIFY
  COLUMN salary UNSET MASKING POLICY, COLUMN bonus UNSET MASKING POLICY,
  COLUMN full_name UNSET MASKING POLICY, COLUMN national_id UNSET MASKING POLICY,
  COLUMN iban UNSET MASKING POLICY;
CREATE OR REPLACE TABLE EMPLOYEES_ENC AS
  SELECT employee_id, department, country, level, full_name, national_id, iban, salary, bonus
  FROM SOVEREIGNTY_DEMO.OPT2_BYOK.EMPLOYEES_ENC;
ALTER TABLE SOVEREIGNTY_DEMO.OPT2_BYOK.EMPLOYEES_ENC MODIFY
  COLUMN salary      SET MASKING POLICY SOVEREIGNTY_DEMO.OPT2_BYOK.MP_SALARY,
  COLUMN bonus       SET MASKING POLICY SOVEREIGNTY_DEMO.OPT2_BYOK.MP_SALARY,
  COLUMN full_name   SET MASKING POLICY SOVEREIGNTY_DEMO.OPT2_BYOK.MP_PII,
  COLUMN national_id SET MASKING POLICY SOVEREIGNTY_DEMO.OPT2_BYOK.MP_PII,
  COLUMN iban        SET MASKING POLICY SOVEREIGNTY_DEMO.OPT2_BYOK.MP_PII;

-- The query's own query_id (community article pattern; verified in the bake-off)
CREATE OR REPLACE SECURE VIEW SELF_QUERY AS
  SELECT query_id FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION())
  ORDER BY start_time DESC LIMIT 1;

-- ---------------------------------------------------------------- Python UDFs (EAI)
CREATE OR REPLACE FUNCTION SOV_DEVICE_START()
  RETURNS VARIANT LANGUAGE PYTHON VOLATILE RUNTIME_VERSION = '3.12' HANDLER = 'start'
  EXTERNAL_ACCESS_INTEGRATIONS = (SOV_OPT2C_EAI) PACKAGES = ('requests')
AS $$
import requests
IDP = "https://<% idp_host %>"
def start():
    return requests.post(f"{IDP}/device/authorize", json={}, timeout=10).json()
$$;

-- Friendlier start: one row per item plus a scannable ASCII QR of the approval link
-- (user code and Snowflake user pre-filled). Use: SELECT * FROM TABLE(SOV_DEVICE_LOGIN());
CREATE OR REPLACE FUNCTION SOV_DEVICE_START_QR(login_hint STRING)
  RETURNS TABLE (seq INT, item STRING, value STRING)
  LANGUAGE PYTHON RUNTIME_VERSION = '3.12' HANDLER = 'Start'
  EXTERNAL_ACCESS_INTEGRATIONS = (SOV_OPT2C_EAI) PACKAGES = ('requests', 'qrcode')
AS $$
import qrcode, requests
IDP = "https://<% idp_host %>"

def _qr_lines(text):
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_L)
    q.add_data(text)
    q.make(fit=True)
    m = q.get_matrix()
    if len(m) % 2:
        m.append([False] * len(m[0]))
    # two matrix rows per text line (top, bottom): dark modules = glyph ink, for a light result grid
    chars = {(True, True): "\u2588", (True, False): "\u2580", (False, True): "\u2584", (False, False): "\u2800"}
    return ["".join(chars[(m[r][c], m[r + 1][c])] for c in range(len(m[0]))) for r in range(0, len(m), 2)]

class Start:
    def process(self, login_hint):
        d = requests.post(f"{IDP}/device/authorize", json={"login_hint": login_hint or ""}, timeout=10).json()
        yield (1, "device_code (copy into 2c-4)", d["device_code"])
        yield (2, "user_code", d["user_code"])
        yield (3, "QR page - open on laptop, scan with phone", d["qr_url"])
        yield (4, "or open on phone (code + user pre-filled)", d["verification_uri_complete"])
        yield (5, "expires in (s)", str(d["expires_in"]))
        for i, line in enumerate(_qr_lines(d["verification_uri_complete"])):
            yield (100 + i, "qr", line)
$$;

CREATE OR REPLACE SECURE FUNCTION SOV_DEVICE_LOGIN()
  RETURNS TABLE (seq INT, item STRING, value STRING)
AS $$
  SELECT seq, item, value FROM TABLE(SOV_DEVICE_START_QR(CURRENT_USER())) ORDER BY seq
$$;

CREATE OR REPLACE FUNCTION SOV_DEVICE_REDEEM_PY(device_code STRING, query_id STRING, account STRING,
    usr STRING, rol STRING, sess STRING, mode STRING)
  RETURNS VARIANT LANGUAGE PYTHON VOLATILE RUNTIME_VERSION = '3.12' HANDLER = 'redeem'
  EXTERNAL_ACCESS_INTEGRATIONS = (SOV_OPT2C_EAI) PACKAGES = ('requests')
AS $$
import requests
IDP, BROKER = "https://<% idp_host %>", "https://<% broker_host %>"
def _post(url, body):
    r = requests.post(url, json=body, timeout=20)
    return r.status_code, r.json()
def redeem(device_code, query_id, account, usr, rol, sess, mode):
    st, tok = _post(f"{IDP}/token", {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                     "device_code": device_code})
    if st != 200:
        return {"error": tok.get("error", f"idp_{st}")}
    ctx = {"account": account, "user": usr, "role": rol, "session": sess, "query_id": query_id, "mode": mode}
    st, out = _post(f"{BROKER}/apikey", {"id_token": tok["id_token"], "ctx": ctx})
    return out if st == 200 else {"error": out.get("error", f"broker_{st}")}
$$;

-- Key fetch: one HTTP call per Python process per query (module memo); the broker
-- single-flights its Snowflake call-back per query, so N processes -> 1 call-back.
CREATE OR REPLACE FUNCTION SOV_GET_KEY_2C_PY(classification STRING, api_key STRING, query_id STRING,
    account STRING, usr STRING, rol STRING, sess STRING, mode STRING)
  RETURNS STRING LANGUAGE PYTHON VOLATILE RUNTIME_VERSION = '3.12' HANDLER = 'get_key'
  EXTERNAL_ACCESS_INTEGRATIONS = (SOV_OPT2C_EAI) PACKAGES = ('requests')
AS $$
import requests
BROKER = "https://<% broker_host %>"
_http = requests.Session()
_memo = {}
def get_key(classification, api_key, query_id, account, usr, rol, sess, mode):
    k = (classification, api_key, query_id, sess, mode)
    if k not in _memo:
        body = {"api_key": api_key, "classification": classification,
                "ctx": {"account": account, "user": usr, "role": rol, "session": sess,
                        "query_id": query_id, "mode": mode}}
        try:
            _memo[k] = _http.post(f"{BROKER}/key", json=body, timeout=20).json().get("key_b64")
        except Exception:
            _memo[k] = None      # fail closed
    return _memo[k]
$$;

-- Bake-off probe: binding only, returns the broker's decision (no key material)
CREATE OR REPLACE FUNCTION SOV_BIND_PROBE(mode STRING, query_id STRING, scope STRING, account STRING,
    usr STRING, rol STRING, sess STRING)
  RETURNS STRING LANGUAGE PYTHON VOLATILE RUNTIME_VERSION = '3.12' HANDLER = 'probe'
  EXTERNAL_ACCESS_INTEGRATIONS = (SOV_OPT2C_EAI) PACKAGES = ('requests')
  SECRETS = ('bench' = SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW.OPT2C_BENCH_TOKEN)
AS $$
import json, time, uuid, requests, _snowflake
BROKER = "https://<% broker_host %>"
PROC = str(uuid.uuid4())[:8]
_memo = {}
def probe(mode, query_id, scope, account, usr, rol, sess):
    k = (mode, query_id, scope, sess)
    if k not in _memo:
        t = time.time()
        body = {"marker": "SOV_BIND_PROBE", "scope": scope,
                "ctx": {"account": account, "user": usr, "role": rol, "session": sess,
                        "query_id": query_id, "mode": mode}}
        out = requests.post(f"{BROKER}/bind", json=body, timeout=25,
                            headers={"X-Bench-Token": _snowflake.get_generic_secret_string("bench")}).json()
        out["udf_ms"] = round((time.time() - t) * 1000)
        out["proc"] = PROC
        _memo[k] = json.dumps(out)
    return _memo[k]
$$;

-- ---------------------------------------------------------------- analyst-facing wrappers
-- Context comes from Snowflake, not from the analyst. The query_id is the query's own id,
-- read through SELF_QUERY (mode A). Analysts only pass the device code / API key.
CREATE OR REPLACE SECURE FUNCTION SOV_DEVICE_REDEEM(device_code STRING)
  RETURNS VARIANT
AS $$
  SOV_DEVICE_REDEEM_PY(device_code, (SELECT query_id FROM SELF_QUERY), CURRENT_ACCOUNT(), CURRENT_USER(),
                           CURRENT_ROLE(), CURRENT_SESSION(), 'A')
$$;

CREATE OR REPLACE SECURE FUNCTION SOV_GET_KEY_2C(classification STRING, api_key STRING)
  RETURNS STRING
AS $$
  SOV_GET_KEY_2C_PY(classification, api_key, (SELECT query_id FROM SELF_QUERY), CURRENT_ACCOUNT(),
                        CURRENT_USER(), CURRENT_ROLE(), CURRENT_SESSION(), 'A')
$$;

-- Same ciphertext format as Option 2 (base64(iv || ct || tag), AAD = classification)
CREATE OR REPLACE SECURE FUNCTION SOV_DECRYPT_2C(env STRING, classification STRING, api_key STRING)
  RETURNS STRING
AS $$
  SOVEREIGNTY_DEMO.OPT2_BYOK.SOV_DECRYPT(env, SOV_GET_KEY_2C(classification, api_key), classification)
$$;

-- ---------------------------------------------------------------- grants
GRANT USAGE ON SCHEMA SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW TO ROLE SOV_ANALYST;
GRANT SELECT ON TABLE EMPLOYEES_ENC TO ROLE SOV_ANALYST;
GRANT USAGE ON FUNCTION SOV_DEVICE_START() TO ROLE SOV_ANALYST;
GRANT USAGE ON FUNCTION SOV_DEVICE_LOGIN() TO ROLE SOV_ANALYST;
GRANT USAGE ON FUNCTION SOV_DEVICE_REDEEM(STRING) TO ROLE SOV_ANALYST;
GRANT USAGE ON FUNCTION SOV_GET_KEY_2C(STRING, STRING) TO ROLE SOV_ANALYST;
GRANT USAGE ON FUNCTION SOV_DECRYPT_2C(STRING, STRING, STRING) TO ROLE SOV_ANALYST;
-- bake-off only
GRANT SELECT ON VIEW SELF_QUERY TO ROLE SOV_ANALYST;
GRANT USAGE ON FUNCTION SOV_BIND_PROBE(STRING, STRING, STRING, STRING, STRING, STRING, STRING)
  TO ROLE SOV_ANALYST;
DROP FUNCTION IF EXISTS BENCH_WRAP(STRING);
DROP FUNCTION IF EXISTS BENCH_CTX(STRING, STRING, STRING, STRING);
