-- ============================================================================
-- Snowflake setup: UDFs, stored procedures, and governance
-- ============================================================================
-- Run after the service is in RUNNING status:
--   SHOW SERVICES LIKE 'websocket_multi_db_service';
-- ============================================================================

USE ROLE dockertest;          -- or your chosen role
USE DATABASE <your-database>;
USE SCHEMA   <your-schema>;

-- ── 1. UDF: query Iceberg → returns results as VARIANT ───────────────────────
-- The iceberg-agent runs DuckDB internally and returns results as a JSON array.
-- catalog_name must match the Lakekeeper warehouse name (default: 'demo').
--
-- Example:
--   SELECT query_iceberg('SELECT * FROM demo.demo.sales LIMIT 5', 'demo');

CREATE OR REPLACE FUNCTION query_iceberg(sql_query STRING, catalog_name STRING)
  RETURNS VARIANT
  SERVICE = websocket_multi_db_service
  ENDPOINT = 'iceberg-api'
  MAX_BATCH_ROWS = 1
  AS '/query_iceberg';


-- ── 2. UDF: query Iceberg → push results to a Snowflake table ────────────────
-- The iceberg-agent executes the query, then creates (or truncates) the named
-- Snowflake table and inserts all rows. Returns a status object.
--
-- Example:
--   SELECT query_iceberg_to_table(
--       'SELECT * FROM demo.demo.sales',
--       'demo',
--       'SALES_FROM_ICEBERG'
--   );

CREATE OR REPLACE FUNCTION query_iceberg_to_table(
    sql_query    STRING,
    catalog_name STRING,
    target_table STRING
)
  RETURNS VARIANT
  SERVICE = websocket_multi_db_service
  ENDPOINT = 'iceberg-api'
  MAX_BATCH_ROWS = 1
  AS '/query_iceberg_sf_table';


-- ── 3. Stored procedure: sync + query in one call ─────────────────────────────
-- Chains the UDF with an immediate SELECT so the caller gets live rows back.
-- Snowflake supports SELECT / TABLE() against stored procedures.
--
-- Example:
--   CALL sync_and_query(
--       'SELECT * FROM demo.demo.sales ORDER BY sale_id',
--       'demo',
--       'SALES_FROM_ICEBERG'
--   );

CREATE OR REPLACE PROCEDURE sync_and_query(
    iceberg_query STRING,
    catalog_name  STRING,
    result_table  STRING
)
  RETURNS TABLE()
  LANGUAGE SQL
AS
$$
DECLARE
    sync_result VARIANT;
BEGIN
    SELECT query_iceberg_to_table(:iceberg_query, :catalog_name, :result_table)
    INTO :sync_result;

    LET res RESULTSET := (SELECT * FROM IDENTIFIER(:result_table));
    RETURN TABLE(res);
END;
$$;


-- ── 4. Quick smoke test ───────────────────────────────────────────────────────
-- Run these after the on-premise agent is connected.

-- Test 1: simple scalar query
SELECT query_iceberg('SELECT 1 AS test', 'demo');

-- Test 2: full table scan
SELECT query_iceberg('SELECT * FROM demo.demo.sales', 'demo');

-- Test 3: sync to Snowflake table and inspect result
SELECT query_iceberg_to_table(
    'SELECT * FROM demo.demo.sales ORDER BY sale_id',
    'demo',
    'SALES_FROM_ICEBERG'
);
SELECT * FROM SALES_FROM_ICEBERG ORDER BY SALE_ID;

-- Test 4: stored procedure (sync + return in one call)
CALL sync_and_query(
    'SELECT * FROM demo.demo.sales ORDER BY sale_id',
    'demo',
    'SALES_FROM_ICEBERG'
);


-- ── 5. Join Iceberg data with a native Snowflake table ────────────────────────
-- First populate SALES_FROM_ICEBERG (Test 3 or 4 above), then:

CREATE TABLE IF NOT EXISTS SF_CUSTOMERS (
    customer_id   INTEGER,
    customer_name VARCHAR,
    country       VARCHAR,
    tier          VARCHAR
);

INSERT INTO SF_CUSTOMERS VALUES
    (1, 'Acme Corp',    'USA',     'Gold'),
    (2, 'Globex',       'UK',      'Silver'),
    (3, 'Initech',      'Germany', 'Gold'),
    (4, 'Umbrella Inc', 'France',  'Bronze'),
    (5, 'Hooli',        'USA',     'Silver');

SELECT
    s.sale_id,
    s.product,
    s.amount,
    s.region,
    c.customer_name,
    c.tier
FROM SALES_FROM_ICEBERG s
LEFT JOIN SF_CUSTOMERS c ON s.sale_id = c.customer_id
ORDER BY s.sale_id;


-- ── 6. (Optional) Masking policy on the AMOUNT column ────────────────────────
CREATE OR REPLACE MASKING POLICY amount_mask AS (val NUMBER)
  RETURNS NUMBER ->
    CASE
      WHEN CURRENT_ROLE() IN ('ACCOUNTADMIN', 'SYSADMIN', 'DOCKERTEST') THEN val
      ELSE -1
    END;

ALTER TABLE SALES_FROM_ICEBERG
  MODIFY COLUMN AMOUNT
  SET MASKING POLICY amount_mask;
