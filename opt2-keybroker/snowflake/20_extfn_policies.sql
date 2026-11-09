-- ============================================================================
-- Option 2 – part B: API integration + external function + masking policies
-- Run AFTER the CDK stack is deployed. Variables (set by setup.sh):
--   role_arn, key_url, key_prefix  (snow sql -D templating)
-- ============================================================================
USE ROLE ACCOUNTADMIN;

CREATE OR REPLACE API INTEGRATION SOV_KEYBROKER_API
  API_PROVIDER = AWS_API_GATEWAY
  API_AWS_ROLE_ARN = '<% role_arn %>'
  API_ALLOWED_PREFIXES = ('<% key_prefix %>')
  ENABLED = TRUE;
GRANT USAGE ON INTEGRATION SOV_KEYBROKER_API TO ROLE SOV_BYOK_ADMIN;

USE ROLE SOV_BYOK_ADMIN;
USE SCHEMA SOVEREIGNTY_DEMO.OPT2_BYOK;

-- The key fetcher. IMMUTABLE + constant argument => Snowflake may reuse the result within
-- the query; the broker additionally caches its decision per query_id.
-- Snowflake signs/sends: sf-external-function-current-query-id (always) and the context headers.
CREATE OR REPLACE SECURE EXTERNAL FUNCTION SOV_GET_KEY(classification STRING)
  RETURNS STRING
  IMMUTABLE
  API_INTEGRATION = SOV_KEYBROKER_API
  CONTEXT_HEADERS = (CURRENT_ACCOUNT, CURRENT_USER, CURRENT_ROLE)
  MAX_BATCH_ROWS = 4096
  AS '<% key_url %>';

-- Masking policies: only SOV_ANALYST even attempts key retrieval. Everyone else sees ciphertext marker.
CREATE OR REPLACE MASKING POLICY MP_SALARY AS (val STRING) RETURNS STRING ->
  CASE WHEN IS_ROLE_IN_SESSION('SOV_ANALYST')
       THEN COALESCE(SOV_DECRYPT(val, SOV_GET_KEY('salary'), 'salary'), '**KEY DENIED**')
       ELSE '**ENCRYPTED**' END;

CREATE OR REPLACE MASKING POLICY MP_PII AS (val STRING) RETURNS STRING ->
  CASE WHEN IS_ROLE_IN_SESSION('SOV_ANALYST')
       THEN COALESCE(SOV_DECRYPT(val, SOV_GET_KEY('pii'), 'pii'), '**KEY DENIED**')
       ELSE '**ENCRYPTED**' END;

ALTER TABLE EMPLOYEES_ENC MODIFY
  COLUMN salary      SET MASKING POLICY MP_SALARY,
  COLUMN bonus       SET MASKING POLICY MP_SALARY,
  COLUMN full_name   SET MASKING POLICY MP_PII,
  COLUMN national_id SET MASKING POLICY MP_PII,
  COLUMN iban        SET MASKING POLICY MP_PII;

-- Analyst-friendly typed view (decryption still happens through the policies)
CREATE OR REPLACE SECURE VIEW EMPLOYEES AS
SELECT employee_id, department, country, hire_year, level, full_name, national_id, iban,
       TRY_TO_NUMBER(salary) AS salary, TRY_TO_NUMBER(bonus) AS bonus,
       salary AS salary_raw
FROM EMPLOYEES_ENC;
GRANT SELECT ON VIEW EMPLOYEES TO ROLE SOV_ANALYST;
GRANT SELECT ON VIEW EMPLOYEES TO ROLE SOV_VIEWER;

DESC INTEGRATION SOV_KEYBROKER_API;
