-- ============================================================================
-- Prerequisites — run once as ACCOUNTADMIN before deploying the service
-- ============================================================================

USE ROLE ACCOUNTADMIN;
USE DATABASE <your-database>;   -- create this first if it doesn't exist
USE SCHEMA   <your-schema>;

-- ── 1. Role ───────────────────────────────────────────────────────────────────
CREATE ROLE IF NOT EXISTS dockertest;

GRANT OWNERSHIP  ON DATABASE <your-database> TO ROLE dockertest;
GRANT OWNERSHIP  ON SCHEMA   <your-schema>   TO ROLE dockertest;
GRANT USAGE      ON DATABASE <your-database> TO ROLE accountadmin;
GRANT USAGE      ON SCHEMA   <your-schema>   TO ROLE accountadmin;
GRANT CREATE TABLE ON SCHEMA <your-schema>   TO ROLE dockertest;

-- ── 2. Compute pool ──────────────────────────────────────────────────────────
-- CPU_X64_S is sufficient for the tunnel + iceberg agent workload.
-- Increase MAX_NODES if you need to serve many concurrent sessions.
CREATE COMPUTE POOL IF NOT EXISTS websocket_tunnel_pool
  MIN_NODES = 1
  MAX_NODES = 4
  INSTANCE_FAMILY = CPU_X64_S
  AUTO_RESUME = TRUE
  AUTO_SUSPEND_SECS = 3600;

-- Wait until IDLE or ACTIVE before continuing
DESCRIBE COMPUTE POOL websocket_tunnel_pool;

GRANT USAGE     ON COMPUTE POOL websocket_tunnel_pool TO ROLE dockertest;
GRANT OPERATE   ON COMPUTE POOL websocket_tunnel_pool TO ROLE dockertest;
GRANT MONITOR   ON COMPUTE POOL websocket_tunnel_pool TO ROLE dockertest;
GRANT MODIFY    ON COMPUTE POOL websocket_tunnel_pool TO ROLE dockertest;
GRANT OWNERSHIP ON COMPUTE POOL websocket_tunnel_pool TO ROLE dockertest;

-- ── 3. Image repository ───────────────────────────────────────────────────────
USE ROLE dockertest;

CREATE IMAGE REPOSITORY IF NOT EXISTS websocket_images;

-- !! Copy the repository_url from this output and paste into build-and-push.sh !!
-- It looks like: <orgname>-<account>.registry.snowflakecomputing.com/<db>/<schema>/websocket_images
SHOW IMAGE REPOSITORIES;

-- ── 4. Network rule for DuckDB extension downloads ───────────────────────────
-- The iceberg-agent downloads DuckDB extensions (iceberg, httpfs, avro) at
-- build time. If you need runtime downloads too, this rule is required.
-- TYPE = HOST_PORT requires "host:port" entries (not plain hostnames).
CREATE OR REPLACE NETWORK RULE duckdb_extensions_network_rule
  MODE       = EGRESS
  TYPE       = HOST_PORT
  VALUE_LIST = (
    'extensions.duckdb.org:443',
    'extensions.duckdb.org:80',
    '0.0.0.0:443',
    '0.0.0.0:80'
  );

-- ── 5. External Access Integration ───────────────────────────────────────────
USE ROLE ACCOUNTADMIN;

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION duckdb_extensions_access_integration
  ALLOWED_NETWORK_RULES = (duckdb_extensions_network_rule)
  ENABLED = true;

GRANT USAGE ON INTEGRATION duckdb_extensions_access_integration TO ROLE dockertest;
