-- ============================================================================
-- Option 2 – part A: objects that do not need AWS yet (run as ACCOUNTADMIN)
-- ============================================================================
USE ROLE ACCOUNTADMIN;
CREATE SCHEMA IF NOT EXISTS SOVEREIGNTY_DEMO.OPT2_BYOK;

CREATE ROLE IF NOT EXISTS SOV_BYOK_ADMIN;   -- owns objects, loads ciphertext
CREATE ROLE IF NOT EXISTS SOV_ANALYST;      -- may decrypt IF step-up grant + running query
CREATE ROLE IF NOT EXISTS SOV_VIEWER;       -- never decrypts
CREATE ROLE IF NOT EXISTS SOV_KEYBROKER;    -- used by the AWS broker for the query_id call-back
GRANT ROLE SOV_BYOK_ADMIN TO ROLE SYSADMIN;
SET me = CURRENT_USER();
GRANT ROLE SOV_BYOK_ADMIN TO USER IDENTIFIER($me);
GRANT ROLE SOV_ANALYST    TO USER IDENTIFIER($me);
GRANT ROLE SOV_VIEWER     TO USER IDENTIFIER($me);

GRANT USAGE ON DATABASE SOVEREIGNTY_DEMO TO ROLE SOV_BYOK_ADMIN;
GRANT USAGE ON DATABASE SOVEREIGNTY_DEMO TO ROLE SOV_ANALYST;
GRANT USAGE ON DATABASE SOVEREIGNTY_DEMO TO ROLE SOV_VIEWER;
GRANT USAGE ON DATABASE SOVEREIGNTY_DEMO TO ROLE SOV_KEYBROKER;
GRANT OWNERSHIP ON SCHEMA SOVEREIGNTY_DEMO.OPT2_BYOK TO ROLE SOV_BYOK_ADMIN COPY CURRENT GRANTS;
GRANT USAGE ON WAREHOUSE SOV_WH TO ROLE SOV_BYOK_ADMIN;
GRANT USAGE ON WAREHOUSE SOV_WH TO ROLE SOV_ANALYST;
GRANT USAGE ON WAREHOUSE SOV_WH TO ROLE SOV_VIEWER;
GRANT USAGE ON WAREHOUSE SOV_WH TO ROLE SOV_KEYBROKER;
-- The broker must see queries of OTHER users on the analytics warehouse
GRANT MONITOR ON WAREHOUSE SOV_WH TO ROLE SOV_KEYBROKER;

-- Broker service user: key-pair auth only, least privilege (MONITOR only).
-- Lambda egress IPs are dynamic, so the user gets its own policy that admits any IP. This overrides
-- a stricter account-level policy for this user only. Production: run the broker in a VPC with a
-- NAT gateway and list that fixed egress IP here instead of 0.0.0.0/0.
CREATE USER IF NOT EXISTS SOV_KEYBROKER_SVC TYPE = SERVICE
  DEFAULT_ROLE = SOV_KEYBROKER DEFAULT_WAREHOUSE = SOV_WH
  COMMENT = 'AWS key broker call-back: verifies query_id is RUNNING before key release';
GRANT ROLE SOV_KEYBROKER TO USER SOV_KEYBROKER_SVC;
CREATE NETWORK RULE IF NOT EXISTS SOVEREIGNTY_DEMO.OPT2_BYOK.BROKER_EGRESS
  MODE = INGRESS TYPE = IPV4 VALUE_LIST = ('0.0.0.0/0');
CREATE NETWORK POLICY IF NOT EXISTS SOV_KEYBROKER_NP
  ALLOWED_NETWORK_RULE_LIST = ('SOVEREIGNTY_DEMO.OPT2_BYOK.BROKER_EGRESS');
ALTER USER SOV_KEYBROKER_SVC SET NETWORK_POLICY = SOV_KEYBROKER_NP;

USE ROLE SOV_BYOK_ADMIN;
USE SCHEMA SOVEREIGNTY_DEMO.OPT2_BYOK;

-- Sensitive columns hold base64(iv || ciphertext || tag), AES-256-GCM, encrypted client-side
CREATE TABLE IF NOT EXISTS EMPLOYEES_ENC (
  employee_id INT, department STRING, country STRING, hire_year INT, level INT,
  full_name STRING, national_id STRING, iban STRING, salary STRING, bonus STRING);

-- Pure SQL decryption with Snowflake's FIPS DECRYPT_RAW. No key is stored anywhere in Snowflake:
-- it is passed in per query from the external key broker.
CREATE OR REPLACE SECURE FUNCTION SOV_DECRYPT(env STRING, key_b64 STRING, aad STRING)
  RETURNS STRING
  AS $$
    IFF(key_b64 IS NULL, NULL,
      HEX_DECODE_STRING(TO_VARCHAR(TRY_DECRYPT_RAW(
        TO_BINARY(SUBSTR(HEX_ENCODE(BASE64_DECODE_BINARY(env)), 25,
                         LENGTH(HEX_ENCODE(BASE64_DECODE_BINARY(env))) - 56), 'HEX'),   -- ciphertext
        BASE64_DECODE_BINARY(key_b64),                                                   -- key
        TO_BINARY(LEFT(HEX_ENCODE(BASE64_DECODE_BINARY(env)), 24), 'HEX'),              -- iv
        TO_BINARY(HEX_ENCODE(aad), 'HEX'),                                               -- aad
        'AES-GCM',
        TO_BINARY(RIGHT(HEX_ENCODE(BASE64_DECODE_BINARY(env)), 32), 'HEX')              -- tag
      ), 'HEX')))
  $$;

GRANT USAGE ON SCHEMA SOVEREIGNTY_DEMO.OPT2_BYOK TO ROLE SOV_ANALYST;
GRANT USAGE ON SCHEMA SOVEREIGNTY_DEMO.OPT2_BYOK TO ROLE SOV_VIEWER;
GRANT SELECT ON TABLE EMPLOYEES_ENC TO ROLE SOV_ANALYST;
GRANT SELECT ON TABLE EMPLOYEES_ENC TO ROLE SOV_VIEWER;
