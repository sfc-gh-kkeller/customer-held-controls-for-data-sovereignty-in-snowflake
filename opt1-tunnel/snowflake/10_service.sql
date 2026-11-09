-- ============================================================================
-- Option 1 – service + no-cache UDF layer
-- ============================================================================
USE ROLE SOV_TUNNEL_ROLE;
USE SCHEMA SOVEREIGNTY_DEMO.OPT1_TUNNEL;
USE WAREHOUSE SOV_WH;

CREATE SERVICE IF NOT EXISTS TUNNEL_SERVICE
  IN COMPUTE POOL SOV_TUNNEL_POOL
  FROM SPECIFICATION $$
  spec:
    containers:
    - name: tunnel-sidecar
      image: /sovereignty_demo/opt1_tunnel/tunnel_images/tunnel-sidecar:latest
      env:
        WS_PORT: "8081"
        # SNOWFLAKE_ACCOUNT is injected by Snowflake into every service container
      secrets:
      - snowflakeSecret: { objectName: sovereignty_demo.opt1_tunnel.tunnel_handshake_secret }
        secretKeyRef: secret_string
        envVarName: HANDSHAKE_SECRET
      resources: { requests: { memory: 256Mi, cpu: 250m }, limits: { memory: 512Mi, cpu: 500m } }
    - name: iceberg-agent
      image: /sovereignty_demo/opt1_tunnel/tunnel_images/iceberg-agent:latest
      env:
        API_PORT: "8090"
        ICEBERG_PORT: "8181"
        S3_PORT: "9000"
        SNOWFLAKE_WAREHOUSE: "SOV_WH"
        SNOWFLAKE_DATABASE: "SOVEREIGNTY_DEMO"
        SNOWFLAKE_SCHEMA: "OPT1_TUNNEL"
      resources: { requests: { memory: 512Mi, cpu: 300m }, limits: { memory: 1Gi, cpu: 700m } }
    endpoints:
    - { name: websocket,        port: 8081, public: true  }
    - { name: iceberg-api,      port: 8090, public: false }
    - { name: tunnel-port-8181, port: 8181, public: false }
    - { name: tunnel-port-9000, port: 9000, public: false }
  $$
  EXTERNAL_ACCESS_INTEGRATIONS = (SOV_DUCKDB_EAI)
  MIN_INSTANCES = 1 MAX_INSTANCES = 1
  AUTO_RESUME = TRUE;

-- Raw service function (internal; not granted to analysts)
CREATE OR REPLACE FUNCTION _QUERY_ICEBERG_RAW(sql_query STRING, catalog_name STRING, nonce STRING)
  RETURNS VARIANT
  SERVICE = TUNNEL_SERVICE ENDPOINT = 'iceberg-api'
  MAX_BATCH_ROWS = 1
  AS '/query_iceberg';

-- Public wrapper: VOLATILE + per-call nonce -> every invocation re-executes through the tunnel.
-- Combined with USE_CACHED_RESULT=FALSE on the analyst users, the result cache is never used.
CREATE OR REPLACE SECURE FUNCTION QUERY_ONPREM(sql_query STRING)
  RETURNS VARIANT
  VOLATILE
  AS $$ _QUERY_ICEBERG_RAW(sql_query, 'demo', UUID_STRING()) $$;

-- Tabular convenience view over the sensitive on-prem table (never materialized in Snowflake)
-- query_iceberg returns positional arrays: [[c0, c1, ...], ...]
CREATE OR REPLACE SECURE VIEW EMPLOYEES_SENSITIVE_LIVE AS
SELECT r.value[0]::INT    AS employee_id,
       r.value[1]::STRING AS full_name,
       r.value[2]::STRING AS national_id,
       r.value[3]::STRING AS iban,
       r.value[4]::NUMBER AS salary,
       r.value[5]::NUMBER AS bonus
FROM LATERAL FLATTEN(INPUT => QUERY_ONPREM(
  'SELECT employee_id, full_name, national_id, iban, salary, bonus FROM demo.demo.employees_sensitive')) r;

-- Non-sensitive data lives natively in Snowflake (strategic platform)
CREATE TABLE IF NOT EXISTS EMPLOYEES_PUBLIC (
  employee_id INT, department STRING, country STRING, hire_year INT, level INT);

SHOW ENDPOINTS IN SERVICE TUNNEL_SERVICE;
