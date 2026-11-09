-- ============================================================================
-- Create the Iceberg Tunnel Service
-- ============================================================================
-- Run prerequisites.sql first, then build-and-push.sh, then this file.
-- ============================================================================

USE ROLE dockertest;          -- or your chosen role
USE DATABASE <your-database>;
USE SCHEMA   <your-schema>;

CREATE OR REPLACE SERVICE websocket_multi_db_service
  IN COMPUTE POOL websocket_tunnel_pool  -- created in prerequisites.sql
  FROM SPECIFICATION $$
  spec:
    containers:

    # ── Container 1: Tunnel Sidecar ──────────────────────────────────────────
    # Generic TCP port forwarder — no Iceberg knowledge.
    # The on-premise agent connects here over WebSocket and registers which
    # local ports (8181, 9000) to forward. The sidecar then opens TCP listeners
    # so that the iceberg-agent can reach on-premise services via localhost.
    # Image path format: /<database>/<schema>/<repository>/<image>:<tag>
    # Get your exact path from: SHOW IMAGE REPOSITORIES;
    - name: tunnel-sidecar
      image: /<database>/<schema>/websocket_images/tunnel-sidecar:latest
      env:
        WS_PORT: "8081"
        SNOWFLAKE_ACCOUNT: "<your-account-identifier>"  # e.g. MYORG-MYACCOUNT
      resources:
        requests:
          memory: 256Mi
          cpu: 250m
        limits:
          memory: 512Mi
          cpu: 500m

    # ── Container 2: Iceberg Agent ───────────────────────────────────────────
    # - Proxies Iceberg REST catalog requests (rewrites minio:9000 hostnames)
    # - Proxies MinIO S3 object reads
    # - Executes DuckDB queries against on-premise Iceberg tables
    # - Writes query results into Snowflake tables using the SPCS OAuth token
    - name: iceberg-agent
      image: /<database>/<schema>/websocket_images/iceberg-agent:latest
      env:
        API_PORT: "8090"
        ICEBERG_PORT: "8181"   # forwarded by tunnel-sidecar → on-prem Lakekeeper
        S3_PORT: "9000"        # forwarded by tunnel-sidecar → on-prem MinIO
        # Snowflake connection for writing query results to tables
        SNOWFLAKE_WAREHOUSE: "<your-warehouse>"    # e.g. COMPUTE_WH
        SNOWFLAKE_DATABASE:  "<your-database>"     # where result tables are written
        SNOWFLAKE_SCHEMA:    "<your-schema>"
      resources:
        requests:
          memory: 512Mi
          cpu: 500m
        limits:
          memory: 1Gi
          cpu: 1000m

    endpoints:
    # Public WebSocket ingress — on-premise agent connects here
    - name: websocket
      port: 8081
      public: true

    # Internal Iceberg API — accessible from UDFs and notebooks within Snowflake
    # (public: false means reachable inside Snowflake, but not from the open internet)
    - name: iceberg-api
      port: 8090
      public: false

    # Internal tunnel ports — used by notebooks for direct DuckDB access via proxy
    - name: tunnel-port-8181
      port: 8181
      public: false
    - name: tunnel-port-9000
      port: 9000
      public: false
  $$
  EXTERNAL_ACCESS_INTEGRATIONS = (duckdb_extensions_access_integration)
  MIN_INSTANCES = 1
  MAX_INSTANCES = 1
  AUTO_RESUME = TRUE;

-- ── Check status ──────────────────────────────────────────────────────────────
-- Wait for status = RUNNING before proceeding to setup.sql
SHOW SERVICES LIKE 'websocket_multi_db_service';

-- Get endpoints
-- Copy the "websocket" ingress URL → SNOWFLAKE_URL in agent/.env
SHOW ENDPOINTS IN SERVICE websocket_multi_db_service;

-- ── Stream logs ───────────────────────────────────────────────────────────────
SELECT * FROM TABLE(SYSTEM$GET_SERVICE_LOGS('websocket_multi_db_service', 0, 'tunnel-sidecar', 100))
ORDER BY TIMESTAMP DESC;

SELECT * FROM TABLE(SYSTEM$GET_SERVICE_LOGS('websocket_multi_db_service', 0, 'iceberg-agent', 100))
ORDER BY TIMESTAMP DESC;
