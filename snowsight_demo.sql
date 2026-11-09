/* =====================================================================================================
   Customer-held controls – Snowsight walkthrough            account: your demo account
   Run section by section (select a block → Cmd/Ctrl+Enter). Every query is read-only.

   BEFORE THE DEMO (laptop, ~10 min, once per day unless noted)
   ---------------------------------------------------------------------------------------------------
   0. AWS session (Options 2 / 2c broker and IdP run in AWS; needed only for redeploys, not for queries)
        aws login
   1. Authenticator app on your phone (ONCE – Google Authenticator, Microsoft Authenticator, 1Password …)
        cd opt2-keybroker
        pixi run totp-qr            # shows the QR of your EXISTING secret again -> scan it   (no rotation)
        pixi run enroll             # only for a NEW / lost phone: rotates the secret, prints a new QR
        pixi run totp-now           # fallback: prints the current 6-digit code in the terminal
      The same TOTP secret is used by the Option 2 step-up portal and by the Option 2c mock IdP.
      Entry in the app shows as "Sovereignty Key Broker: <your user>".
   2. Option 1 tunnel (resumes the compute pool + service if suspended, ~1 min cold start)
        cd opt1-tunnel && pixi run tunnel-up                 # default TTL 15m;  pixi run tunnel-up 30m
      Afterwards:  pixi run tunnel-down                      # asks whether to suspend the pool (saves credits)
   3. Option 2 step-up portal URL (bookmark it on the phone):   cd opt2-keybroker && pixi run portal
   4. Option 2c: nothing to start – the QR in section 2c links your phone straight to the approval page.
      Optional dry run from the terminal:   cd opt2c-device-flow && pixi run e2e
   5. Option 4 on-prem app (localhost):  cd opt4-client-decrypt && pixi run app   (first time: pixi run setup)
   6. Snowsight: open this worksheet, role ACCOUNTADMIN, warehouse SOV_WH (X-Small, auto-suspend 60 s).
      Your public IP must be allowed in OPT1_TUNNEL.ONPREM_EGRESS_IP / OPT4_CLIENT_DECRYPT.APP_EGRESS_IP
      (the setup scripts add the IP they run from; add more with ALTER NETWORK RULE).
   ===================================================================================================== */
USE ROLE ACCOUNTADMIN;
USE WAREHOUSE SOV_WH;
USE DATABASE SOVEREIGNTY_DEMO;
USE SECONDARY ROLES NONE;            -- keep role checks exact (Option 2 policies use the primary role)


/* =====================================================================================================
   OPTION 1 – On-demand tunnel to the on-prem Iceberg lake (no data at rest in Snowflake)
   Pre-req on the laptop (data owner opens the tunnel, with TTL + reason):
     cd opt1-tunnel && pixi run tunnel-up            (see BEFORE THE DEMO, step 2)
   ===================================================================================================== */

-- 1a. What Snowflake stores: only the non-sensitive half.
SELECT * FROM OPT1_TUNNEL.EMPLOYEES_PUBLIC LIMIT 10;

-- 1b. The "sensitive" view holds no data – it is just a call through the tunnel.
SELECT GET_DDL('VIEW', 'SOVEREIGNTY_DEMO.OPT1_TUNNEL.EMPLOYEES_SENSITIVE_LIVE');

-- 1c. Tunnel state. SUSPENDED / agent offline  ->  the next query fails: the data simply is not reachable.
SHOW SERVICES LIKE 'TUNNEL_SERVICE' IN SCHEMA OPT1_TUNNEL;
-- ALTER SERVICE OPT1_TUNNEL.TUNNEL_SERVICE RESUME;   -- only if you didn't use tunnel_ctl --suspend-service

-- 1d. Live join: public data in Snowflake + sensitive data streamed from on-prem, per query.
SELECT p.department,
       COUNT(*)              AS headcount,
       ROUND(AVG(s.salary))  AS avg_salary
FROM OPT1_TUNNEL.EMPLOYEES_PUBLIC p
JOIN OPT1_TUNNEL.EMPLOYEES_SENSITIVE_LIVE s USING (employee_id)
GROUP BY 1 ORDER BY 1;

-- 1e. Run 1d again: it goes through the tunnel again (VOLATILE function -> the result cache is not REUSED).
--     Now close the tunnel on the laptop (`pixi run tunnel-down`) and run 1d once more -> it fails.
--
-- 1f. Caveat: the result of an earlier run is still PERSISTED for 24 h. The same user can read it back
--     with RESULT_SCAN even while the tunnel is closed. Closing the tunnel stops NEW reads, not past results.
SELECT * FROM TABLE(RESULT_SCAN(
  (SELECT query_id FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION())
   WHERE query_text ILIKE '%EMPLOYEES_SENSITIVE_LIVE%' AND execution_status = 'SUCCESS'
     AND query_text NOT ILIKE '%RESULT_SCAN%' AND query_text NOT ILIKE '%GET_DDL%'
   ORDER BY start_time DESC LIMIT 1)));


/* =====================================================================================================
   OPTION 2 – Column encryption + external key broker with step-up auth (BYOK, AWS Lambda + KMS)
   ===================================================================================================== */

-- 2a. Even ACCOUNTADMIN on the base table: the masking policy never asks for a key -> '**ENCRYPTED**'.
--     (The stored value is AES-256-GCM ciphertext; no key exists in Snowflake.)
SELECT employee_id, department, full_name, iban, salary
FROM OPT2_BYOK.EMPLOYEES_ENC LIMIT 5;

-- 2b. VIEWER role: the masking policy never asks for a key -> ciphertext.
USE ROLE SOV_VIEWER;
SELECT department, full_name, salary_raw FROM SOVEREIGNTY_DEMO.OPT2_BYOK.EMPLOYEES LIMIT 5;

-- 2c. ANALYST role WITHOUT step-up: the policy calls the broker, the broker refuses -> '**KEY DENIED**'.
USE ROLE SOV_ANALYST;
SELECT department, full_name, salary_raw FROM SOVEREIGNTY_DEMO.OPT2_BYOK.EMPLOYEES LIMIT 5;

-- 2d. Step-up OUTSIDE Snowflake: open the step-up portal (StepUpPortalUrl in opt2-keybroker/infra/outputs.json),
--     your user, classification salary + pii, 5 minutes, justification, TOTP from the authenticator app.
--     The broker checks via call-back that this exact query_id is RUNNING for your user / SOV_ANALYST.

-- 2e. Same role, same query, after step-up -> plaintext; aggregates work in Snowflake.
SELECT department,
       COUNT(*)             AS n,
       ROUND(AVG(salary))   AS avg_salary,
       MAX(full_name)       AS sample_name
FROM SOVEREIGNTY_DEMO.OPT2_BYOK.EMPLOYEES
GROUP BY 1 ORDER BY 1;

-- 2f. The key never persists: external functions bypass the result cache; wait 5 min (or revoke) and rerun 2e.
USE ROLE ACCOUNTADMIN;
SHOW MASKING POLICIES IN SCHEMA SOVEREIGNTY_DEMO.OPT2_BYOK;


/* =====================================================================================================
   OPTION 2b – Key fetched by an External Access UDF, memoized per query (no Lambda / API Gateway)
   The probe UDF stands in for the key fetch: it reports its Python process and whether its memo was warm.
   ===================================================================================================== */
USE ROLE ACCOUNTADMIN;
ALTER SESSION SET USE_CACHED_RESULT = FALSE;

-- 2b-1. Run twice: a NEW process each time, memo empty -> the key would be fetched again per query.
SELECT SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROCESS_PROBE_EAI('salary') AS probe;
SELECT SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROCESS_PROBE_EAI('salary') AS probe;

-- 2b-2. Within ONE query the memo works: few processes, almost every row is a memo hit
--       (= a handful of key fetches instead of one per row).
SELECT COUNT(*)                                                                                AS rows_processed,
       COUNT(DISTINCT SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROCESS_PROBE_EAI('salary'):process)     AS python_processes,
       COUNT_IF(SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROCESS_PROBE_EAI('salary'):cache_hit::BOOLEAN) AS memo_hits
FROM TABLE(GENERATOR(ROWCOUNT => 5000));

-- 2b-3. Kill switch: disable the integration -> the next key fetch fails immediately. (Re-enable afterwards.)
-- ALTER EXTERNAL ACCESS INTEGRATION SOV_OPT2B_PROBE_EAI SET ENABLED = FALSE;
-- ALTER EXTERNAL ACCESS INTEGRATION SOV_OPT2B_PROBE_EAI SET ENABLED = TRUE;


/* =====================================================================================================
   OPTION 2c – Device-code sign-in with a justification -> API key scoped to user + account + session
               -> every key fetch is bound to the running query (call-back on its own query_id)
   ===================================================================================================== */
USE ROLE SOV_ANALYST;
USE SECONDARY ROLES NONE;
USE SCHEMA SOVEREIGNTY_DEMO.OPT2C_DEVICE_FLOW;
ALTER SESSION SET USE_CACHED_RESULT = FALSE;

-- 2c-1. Same ciphertext as Option 2, no masking policy on this copy.
SELECT employee_id, department, salary FROM EMPLOYEES_ENC LIMIT 5;

-- 2c-2. Without an API key nothing decrypts (NULL = key refused).
SELECT employee_id, SOV_DECRYPT_2C(salary, 'salary', 'nk2c_nothing') AS salary FROM EMPLOYEES_ENC LIMIT 5;

-- 2c-3. Start the device flow. The result shows the device code, the user code, two links and a QR
--       (rows 'qr'). Scan the QR in the grid with your phone camera – if the grid squeezes it, click the
--       'QR page' link instead and scan from that page. The approval page opens with code and user
--       pre-filled; type the 6-digit code from your authenticator app (iOS/Android offer it via
--       one-time-code autofill if the app supports it), add a justification, tick the classifications.
SELECT item, value FROM TABLE(SOV_DEVICE_LOGIN()) ORDER BY seq;

-- 2c-4. Before approval the IdP answers 'authorization_pending'. After approval, redeem ONCE:
--       the broker checks that THIS query is running in this session for this user, then mints the key.
--       The key value never appears in query text or history – only in the session variable.
SET device_code = '<paste device_code from row 1 of 2c-3>';
SET api_key = (SELECT SOV_DEVICE_REDEEM($device_code):api_key::STRING);
SELECT LEFT($api_key, 8) || '…' AS api_key_prefix;

-- 2c-5. Decrypt. One call-back per query (bound to this query's own query_id), key only in memory.
SELECT department, COUNT(*) AS people,
       ROUND(AVG(TRY_TO_NUMBER(SOV_DECRYPT_2C(salary, 'salary', $api_key)))) AS avg_salary
FROM EMPLOYEES_ENC GROUP BY department ORDER BY department;

-- 2c-6. The key only covers what was approved: 'pii' was not ticked -> NULL.
SELECT employee_id, SOV_DECRYPT_2C(full_name, 'pii', $api_key) AS full_name FROM EMPLOYEES_ENC LIMIT 3;

-- 2c-7. Redeeming the same device code again is refused (single use).
SELECT SOV_DEVICE_REDEEM($device_code);

-- 2c-8. Copy the key into ANOTHER worksheet (= other session) and run 2c-5 there: all NULL.
SELECT $api_key;


/* =====================================================================================================
   OPTION 3 – Split storage: only non-sensitive data in Snowflake, the join happens on-prem
   ===================================================================================================== */
USE ROLE SOV_SPLIT_ROLE;         -- owner of OPT3_SPLIT

-- 3a. This is everything Snowflake holds for Option 3 – no names, no IBANs, no salaries.
SELECT * FROM OPT3_SPLIT.EMPLOYEES_PUBLIC LIMIT 10;

-- 3b. Proof by schema: there is no sensitive column anywhere in OPT3_SPLIT.
SELECT table_name, column_name, data_type
FROM SOVEREIGNTY_DEMO.INFORMATION_SCHEMA.COLUMNS
WHERE table_schema = 'OPT3_SPLIT'
ORDER BY table_name, ordinal_position;

-- 3c. Optional write-back: only k-anonymous aggregates computed on-prem come back.
SELECT * FROM OPT3_SPLIT.SALARY_BANDS_AGG ORDER BY department, country;

-- 3d. Snowflake can still do all non-sensitive analytics on its own.
SELECT department, country, COUNT(*) AS headcount, ROUND(AVG(level), 1) AS avg_level
FROM OPT3_SPLIT.EMPLOYEES_PUBLIC GROUP BY 1, 2 ORDER BY 1, 2;


/* =====================================================================================================
   OPTION 4 – Ciphertext in Snowflake, decryption only in the on-prem app (opt4-client-decrypt)
   ===================================================================================================== */
USE ROLE SOV_CLIENTDEC_ROLE;     -- same role the on-prem app's service user gets

-- 4a. The data really is in Snowflake – 200 rows, sensitive columns as AES-256-CBC ciphertext.
SELECT COUNT(*) AS rows_in_snowflake FROM OPT4_CLIENT_DECRYPT.EMPLOYEES_ENCRYPTED;
SELECT * FROM OPT4_CLIENT_DECRYPT.EMPLOYEES_ENCRYPTED LIMIT 10;

-- 4b. Snowflake can filter/group on the plaintext dimensions ...
SELECT department, country, COUNT(*) AS n
FROM OPT4_CLIENT_DECRYPT.EMPLOYEES_ENCRYPTED GROUP BY 1, 2 ORDER BY 1, 2;

-- 4c. ... but cannot compute on the encrypted ones (AVG over ciphertext is NULL – no key in Snowflake).
SELECT department, AVG(TRY_TO_NUMBER(salary)) AS avg_salary_in_snowflake, ANY_VALUE(salary) AS what_snowflake_sees
FROM OPT4_CLIENT_DECRYPT.EMPLOYEES_ENCRYPTED GROUP BY 1 ORDER BY 1;

-- 4d. Who reads it: only the IP-restricted service user of the on-prem app, with a role-restricted PAT.
USE ROLE ACCOUNTADMIN;
SHOW USERS LIKE 'SOV_CLIENTDEC_APP';
SHOW USER PROGRAMMATIC ACCESS TOKENS FOR USER SOV_CLIENTDEC_APP;
-- Then switch to the app (pixi run app → Connect) to show the same rows decrypted locally.


/* =====================================================================================================
   BONUS – Homomorphic encryption: Snowflake computes on ciphertext it can't read
   ===================================================================================================== */

-- H1. Encrypted salary per employee; only the public HE context is stored (no secret key).
USE SCHEMA SOVEREIGNTY_DEMO.HE;
SELECT emp_id, department, region, LEFT(TO_VARCHAR(salary_ct, 'BASE64'), 60) || '…' AS salary_ciphertext
FROM HE.EMPLOYEES_ENC LIMIT 5;
SELECT key_id, scheme, mode, params, created_at FROM HE.HE_KEYS;

-- H2. SUM per department computed by Snowflake on ciphertext; the result is still ciphertext
--     (decrypt client-side in the bonus-homomorphic notebooks).
SELECT e.department,
       COUNT(*)                                        AS n,
       HE_INFO(k.context, HE_SUM(k.context, e.salary_ct)) AS encrypted_sum_info
FROM HE.EMPLOYEES_ENC e
JOIN HE.HE_KEYS k ON k.key_id = e.bfv_key_id
GROUP BY e.department, k.context
ORDER BY 1;
