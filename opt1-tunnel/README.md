# Iceberg ↔ Snowflake Tunnel

Query your on-premise Apache Iceberg data from Snowflake — with **absolute data sovereignty**.

The on-premise side always initiates the connection. No inbound firewall rules. No VPN.
No data uploaded to the cloud unless you explicitly choose to. When the agent is stopped,
Snowflake has zero network path to your data — enforced by the network, not just by policy.

A lightweight, outbound-only WebSocket tunnel connects your on-premise
[Lakekeeper](https://lakekeeper.io) catalog and S3-compatible storage
to a pair of containers in
[Snowflake Container Services (SPCS)](https://docs.snowflake.com/en/developer-guide/snowpark-container-services/overview).
From there you can query Iceberg tables ephemerally (DuckDB, PyIceberg) or explicitly
sync results into governed Snowflake tables.

---

## Architecture

```
┌─────────────────────────────────────────────┐     ┌──────────────────────────────────────────┐
│  On-Premise (data centre / private cloud)    │     │       Snowflake (SPCS)                   │
│                                             │     │                                          │
│  ┌─────────────┐    ┌──────────────────┐    │     │  ┌──────────────────────────────────┐   │
│  │  Lakekeeper  │    │  onpremise_agent │────┼─────┼─▶│  tunnel-sidecar (port 8081)      │   │
│  │  REST :8181  │◀───│  (outbound WSS)  │    │     │  │  • WebSocket server              │   │
│  └─────────────┘    └──────────────────┘    │     │  │  • Opens TCP listeners           │   │
│                                             │     │  │    :8181 → on-prem Lakekeeper    │   │
│  ┌─────────────┐                            │     │  │    :9000 → on-prem S3 storage    │   │
│  │  S3 storage  │                            │     │  └──────────────┬───────────────────┘   │
│  │    :9000     │                            │     │                 │ localhost TCP          │
│  └─────────────┘                            │     │  ┌──────────────▼───────────────────┐   │
│                                             │     │  │  iceberg-agent (port 8090)        │   │
│  ← data never leaves this boundary →        │     │  │  • Iceberg REST proxy             │   │
└─────────────────────────────────────────────┘     │  │    (rewrites minio:9000 URLs)     │   │
                 ↑ only when agent is running        │  │  • S3 / MinIO proxy               │   │
                                                    │  │  • DuckDB query engine            │   │
                                                    │  │  • Writes results to SF tables    │   │
                                                    │  └──────────────────────────────────┘   │
                                                    │                 ▲                        │
                                                    │  Snowflake UDFs / Notebooks / SQL        │
                                                    └──────────────────────────────────────────┘
```

**Key design properties:**
- **Sovereignty enforced by the network** — tunnel down = zero Snowflake access, by construction
- **Outbound-only** — the agent initiates the connection; no inbound firewall rules needed
- **Encrypted** — RSA-2048 key exchange + AES-256 for all tunnel traffic
- **Transparent hostname rewriting** — iceberg-agent rewrites `minio:9000` references in
  catalog responses so DuckDB never needs to resolve the internal Docker hostname
- **You choose what lands in Snowflake** — ephemeral notebook queries leave no trace;
  table-sync UDFs explicitly persist data under full Snowflake Horizon governance

---

## Query patterns — sovereignty spectrum

| Option | Method | Data in Snowflake? | Governance? |
|--------|--------|--------------------|-------------|
| A | Direct DuckDB (notebook) | Never | No |
| B | PyIceberg (notebook) | Never | No |
| C | `query_iceberg` UDF | No (VARIANT in query) | No |
| D | `query_iceberg_to_table` UDF | Yes (explicit) | ✅ Full Horizon |
| E | `sync_and_query` stored proc | Yes (explicit) | ✅ Full Horizon |

---

## Prerequisites

| Tool | Purpose | Install |
|------|---------|---------|
| [Pixi](https://pixi.sh) | Python env & task runner | `curl -fsSL https://pixi.sh/install.sh \| bash` |
| [Docker](https://docs.docker.com/get-docker/) | Local Iceberg stack | docker.com |
| Snowflake account | SPCS, UDFs, notebooks | — |
| Snowflake PAT token | Agent authentication | Snowflake UI → Admin → Security → PAT |

---

## Quick Start

### Step 1 — Start the local Iceberg stack

```bash
git clone https://github.com/sfc-gh-kkeller/iceberg-snowflake-tunnel
cd iceberg-snowflake-tunnel

# Start MinIO + Lakekeeper
pixi run start

# Seed demo tables: demo.demo.sales, demo.demo.customers
pixi run seed
```

Verify:
```bash
curl "http://localhost:8181/catalog/v1/config?warehouse=demo"
curl "http://localhost:9000/minio/health/live"
```

MinIO console: http://localhost:9001 (admin / password)

---

### Step 2 — Deploy the Snowflake service

1. **Run prerequisites** — open `snowflake/prerequisites.sql` in Snowsight and run it as `ACCOUNTADMIN`.
   Creates: `dockertest` role, `websocket_tunnel_pool` compute pool, image repository,
   network rule for DuckDB extensions, and external access integration.

2. **Build and push images**:

```bash
# Get your registry URL (run in Snowsight after prerequisites.sql):
#   SHOW IMAGE REPOSITORIES;
# Copy the repository_url:
#   <orgname>-<account>.registry.snowflakecomputing.com/<db>/<schema>/websocket_images

export REGISTRY="<orgname>-<account>.registry.snowflakecomputing.com"
export REPO="<database>/<schema>/websocket_images"

docker login $REGISTRY

cd snowflake
./build-and-push.sh
```

3. **Create the SPCS service** — fill in your database, schema, account, and warehouse
   placeholders in `snowflake/service.sql`, then run it in Snowsight.

4. **Get the endpoint URLs**:

```sql
SHOW ENDPOINTS IN SERVICE websocket_multi_db_service;
```

Copy the **websocket** endpoint — you need it for the agent in Step 3.

---

### Step 3 — Configure and start the on-premise agent

```bash
cd agent
cp .env.template .env
```

Edit `.env`:
```
SNOWFLAKE_URL=wss://<your-websocket-endpoint>.snowflakecomputing.app
SNOWFLAKE_PAT=<your-pat-token>
SNOWFLAKE_ACCOUNT=<your-account-identifier>
SNOWFLAKE_ROLE=dockertest
```

Start the agent:
```bash
pixi run agent
```

You should see:
```
✅ WebSocket connection established
✅ Secure handshake complete via Snowflake Token
📤 Pushed 2 port mappings to tunnel
📡 Listening for forwarding requests...
```

When this agent is stopped, Snowflake has no route to your data.

---

### Step 4 — Create UDFs and stored procedures

Open `snowflake/setup.sql` in Snowsight and run it.
Wait until the service status is `RUNNING` before running this step:

```sql
SHOW SERVICES LIKE 'websocket_multi_db_service';
```

---

### Step 5 — Query (SQL patterns)

**Option C — UDF, result as VARIANT (no persistence):**
```sql
SELECT query_iceberg('SELECT * FROM demo.demo.sales', 'demo');
```

**Option D — UDF, push results into a Snowflake table:**
```sql
SELECT query_iceberg_to_table(
    'SELECT * FROM demo.demo.sales',
    'demo',
    'SALES_FROM_ICEBERG'
);
SELECT * FROM SALES_FROM_ICEBERG ORDER BY SALE_ID;
```

**Option E — Stored procedure, sync + query in one call:**
```sql
CALL sync_and_query(
    'SELECT * FROM demo.demo.sales ORDER BY sale_id',
    'demo',
    'SALES_FROM_ICEBERG'
);
```

**Join Iceberg data with a native Snowflake table:**
```sql
SELECT
    s.sale_id, s.product, s.amount, s.region,
    c.customer_name, c.tier
FROM SALES_FROM_ICEBERG s
LEFT JOIN SF_CUSTOMERS c ON s.sale_id = c.customer_id
ORDER BY s.sale_id;
```

---

### Step 6 — Notebook (ephemeral patterns)

Open `notebooks/iceberg_demo.ipynb` in a Snowflake Notebook.
Set `SF_DATABASE` and `SF_SCHEMA` in the config cell — the `AGENT_URL` is derived automatically.

**Option A — Direct DuckDB (nothing persists in Snowflake):**
```python
import duckdb

# Internal SPCS DNS — set SF_DATABASE and SF_SCHEMA to match your setup
AGENT_URL = "http://websocket-multi-db-service.<sf-schema>.<sf-database>.svc.spcs.internal:8090"

con = duckdb.connect(":memory:")
con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
con.execute(f"""
    CREATE SECRET iceberg_s3 (
        TYPE S3, KEY_ID 'admin', SECRET 'password',
        ENDPOINT '{AGENT_URL.replace("http://", "")}',
        USE_SSL false, URL_STYLE 'path'
    );
""")
con.execute("CREATE SECRET iceberg_rest (TYPE ICEBERG, TOKEN 'dummy');")
con.execute(f"""
    ATTACH 'demo' AS demo (
        TYPE ICEBERG, ENDPOINT '{AGENT_URL}/catalog/', SECRET iceberg_rest
    );
""")
df = con.execute("SELECT * FROM demo.demo.sales").fetchdf()
```

**Option B — PyIceberg (also fully ephemeral):**
```python
from pyiceberg.catalog.rest import RestCatalog

catalog = RestCatalog(name="demo", uri=f"{AGENT_URL}/catalog/", warehouse="demo")
df = catalog.load_table("demo.sales").scan().to_pandas()
```

Both options A and B leave zero trace in Snowflake — no result set cache, no table writes.

---

## Repository structure

```
iceberg-snowflake-tunnel/
├── docker-compose.yml             # MinIO + Lakekeeper (local Iceberg stack)
├── pixi.toml                      # Python dependencies and tasks
├── .gitignore
│
├── agent/
│   ├── onpremise_agent.py         # On-premise tunnel agent
│   ├── port_mappings.json         # Ports to forward: 8181 (catalog), 9000 (S3)
│   └── .env.template              # Configuration template → copy to .env
│
├── snowflake/
│   ├── iceberg_agent.py           # SPCS iceberg agent (proxy + DuckDB engine)
│   ├── tunnel_sidecar.py          # SPCS tunnel sidecar (generic TCP forwarder)
│   ├── Dockerfile.iceberg         # Image for iceberg-agent
│   ├── Dockerfile.tunnel-sidecar  # Image for tunnel-sidecar
│   ├── build-and-push.sh          # Build & push both images to Snowflake registry
│   ├── prerequisites.sql          # ← Run first: role, compute pool, network rule, EAI
│   ├── service.sql                # ← Run second: CREATE SERVICE
│   └── setup.sql                  # ← Run third: UDFs, stored procedures, smoke tests
│
├── demo-data/
│   └── seed.py                    # Seed demo Iceberg tables via DuckDB
│
└── notebooks/
    └── iceberg_demo.ipynb         # All 5 query patterns (A–E), ordered by sovereignty
```

### Execution order

```
prerequisites.sql  →  build-and-push.sh  →  service.sql  →  [start agent]  →  setup.sql
```

---

## How hostname rewriting works

When Iceberg metadata is written, data file URLs are stored using the internal Docker hostname:
```
http://minio:9000/warehouse/abc/metadata/snap-xyz.avro
```

From SPCS, `minio` does not resolve. The `iceberg-agent` handles this in two complementary ways:

**For notebook clients (DuckDB, PyIceberg):** the `/catalog/` proxy rewrites every JSON
response from Lakekeeper, replacing `http://minio:9000` with `http://<agent>:8090/s3`.
DuckDB fetches data files from the agent's `/s3/` proxy, which forwards them to `localhost:9000`.

**For embedded DuckDB (UDFs):** since `minio:9000` also appears in binary Avro manifest files
that can't be rewritten in-flight, the agent patches `/etc/hosts` at startup to add
`127.0.0.1 minio`. The tunnel-sidecar already forwards port 9000 to on-premise MinIO, so `minio`
resolves directly.

In production with standard `s3://` URIs neither workaround is needed.

---

## Security

- **Outbound-only WebSocket** — no inbound ports opened on the on-premise side
- **RSA-2048 key exchange** establishes a per-session **AES-256 session key**
- **Snowflake PAT** is exchanged for a short-lived OAuth token scoped to the specific endpoint and role
- **SPCS OAuth token** (mounted at `/snowflake/session/token`) is used by iceberg-agent
  for writing result tables — no credentials stored in the container image
- For production, replace `change-me-in-production` in `docker-compose.yml` with a
  strong random value, and use a secrets manager for the PAT token

---

## Troubleshooting

**Agent won't connect (401)**
→ PAT token expired or wrong role. Generate a new one in Snowflake UI → Admin → Security → PAT.

**Agent won't connect (404 "could not find service")**
→ The SPCS service was recreated and has a new endpoint. Run
`SHOW ENDPOINTS IN SERVICE websocket_multi_db_service;` and update `SNOWFLAKE_URL` in `.env`.

**`minio:9000` hostname errors in DuckDB**
→ You are connecting directly to the tunnel port (`:8181`) instead of the
iceberg-agent proxy (`:8090/catalog/`). Use `AGENT_URL/catalog/` as the endpoint.

**`No active warehouse selected`**
→ Set `SNOWFLAKE_WAREHOUSE` in the `iceberg-agent` container env in `service.sql`.

**Docker won't start (container name conflict)**
→ Run `docker compose down --remove-orphans` then `docker compose up -d`.

**Service stuck in `PENDING`**
→ The compute pool may still be provisioning. Check with
`DESCRIBE COMPUTE POOL websocket_tunnel_pool;` and wait for `IDLE` or `ACTIVE`.
