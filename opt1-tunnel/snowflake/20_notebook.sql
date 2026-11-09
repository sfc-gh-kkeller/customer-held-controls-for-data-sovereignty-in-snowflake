-- ============================================================================
-- Option 1b – notebook reads the on-prem lake directly through the tunnel
-- (DuckDB in a Container Runtime notebook -> tunnel-service:8181 / :9000).
-- No Snowflake SQL query touches the sensitive data -> no persisted result, no RESULT_SCAN.
--
-- The notebook is owned by SOV_TUNNEL_ROLE, the same owner role as TUNNEL_SERVICE,
-- which is what allows service-to-service traffic to the tunnel endpoints.
-- Upload the notebook first (from opt1-tunnel/):
--   snow stage copy notebooks/tunnel_direct_read.ipynb @SOVEREIGNTY_DEMO.OPT1_TUNNEL.NOTEBOOK_STAGE --overwrite
-- ============================================================================
USE ROLE ACCOUNTADMIN;

-- Own node: the tunnel pool has a single node and a notebook needs a full node.
CREATE COMPUTE POOL IF NOT EXISTS SOV_NOTEBOOK_POOL
  MIN_NODES = 1 MAX_NODES = 1 INSTANCE_FAMILY = CPU_X64_XS
  AUTO_SUSPEND_SECS = 600 INITIALLY_SUSPENDED = TRUE;
GRANT USAGE, MONITOR ON COMPUTE POOL SOV_NOTEBOOK_POOL TO ROLE SOV_TUNNEL_ROLE;

-- Outbound only for installing duckdb and its iceberg/httpfs extensions.
CREATE OR REPLACE NETWORK RULE SOVEREIGNTY_DEMO.OPT1_TUNNEL.NOTEBOOK_PKG_RULE
  MODE = EGRESS TYPE = HOST_PORT
  VALUE_LIST = ('pypi.org:443', 'files.pythonhosted.org:443',
                'extensions.duckdb.org:443', 'extensions.duckdb.org:80');
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION SOV_OPT1_NOTEBOOK_EAI
  ALLOWED_NETWORK_RULES = (SOVEREIGNTY_DEMO.OPT1_TUNNEL.NOTEBOOK_PKG_RULE) ENABLED = TRUE;
GRANT USAGE ON INTEGRATION SOV_OPT1_NOTEBOOK_EAI TO ROLE SOV_TUNNEL_ROLE;

USE ROLE SOV_TUNNEL_ROLE;
USE SCHEMA SOVEREIGNTY_DEMO.OPT1_TUNNEL;
CREATE STAGE IF NOT EXISTS NOTEBOOK_STAGE;

CREATE OR REPLACE NOTEBOOK TUNNEL_DIRECT_READ
  FROM '@SOVEREIGNTY_DEMO.OPT1_TUNNEL.NOTEBOOK_STAGE'
  MAIN_FILE = 'tunnel_direct_read.ipynb'
  QUERY_WAREHOUSE = SOV_WH
  RUNTIME_NAME = 'SYSTEM$BASIC_RUNTIME'
  COMPUTE_POOL = 'SOV_NOTEBOOK_POOL'
  EXTERNAL_ACCESS_INTEGRATIONS = (SOV_OPT1_NOTEBOOK_EAI)
  IDLE_AUTO_SHUTDOWN_TIME_SECONDS = 1800;
ALTER NOTEBOOK TUNNEL_DIRECT_READ ADD LIVE VERSION FROM LAST;

-- Headless run (same cells as the interactive notebook):
-- EXECUTE NOTEBOOK SOVEREIGNTY_DEMO.OPT1_TUNNEL.TUNNEL_DIRECT_READ();
