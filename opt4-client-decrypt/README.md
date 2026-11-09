# Secure analytics – client-side decryption for Snowflake

An on-prem Streamlit app that pulls AES-256 ciphertext from Snowflake over ADBC/Arrow, decrypts it **on this host**,
and keeps the plaintext in a per-session, in-memory DuckDB cache with a TTL. Snowflake stores and filters; it never
sees a key or a plaintext value. (Option 4 – see `../docs/05-client-decrypt.md`.)

## Run

```bash
pixi run setup   # Snowflake schema/role/service user + PAT, AES key, .streamlit/secrets.toml, encrypt + load 200 rows
pixi run app     # http://localhost:8501
pixi run test    # unit tests (crypto, SQL guard, cache TTL, smart query, config, roles)
```

Sign in → **Connect** (pre-filled from secrets) → rows are decrypted into the local cache → **Query** / **Explore**.

## Pages

| Page | What it does | Roles |
|---|---|---|
| Connect | Snowflake (PAT/password) or PostgreSQL; edit encrypted-table list; decrypt-and-cache report (rows, columns, failures) | analyst, admin (viewer: read-only) |
| Query | SQL with three modes: **Local cache** (DuckDB on plaintext), **Smart** (cached tables local, others fetched live and row-capped for this query only), **Live source** (shows the ciphertext Snowflake holds). Examples, cached-table browser, executed SQL | viewer: cache only |
| Explore | 9 chart types, categorical/date filters, statistics, correlation, data quality | all |
| Compare | Side-by-side A/B across cache and sources, consolidation with `_source` column, benchmark | analyst, admin |
| Admin | Security posture, non-secret settings, audit log, key round-trip test, versions / cached tables | admin |

## Layout

```
app.py                     entry: auth gate, st.navigation, sidebar status
app_pages/                 connect · query · explore · compare · admin
secure_analytics/          UI-free core
  config.py                secrets.toml (read-only) + settings.yaml (whitelisted, non-secret keys only)
  auth.py                  st.login (OIDC) · guarded dev sign-in · [roles] → viewer/analyst/admin
  crypto.py                AES-256-CBC, wire format base64(ct)||base64(iv16), uniform failure marker
  connections.py           ADBC with percent-encoded credentials, cursors always closed; SQLAlchemy text()
  cache.py                 DuckDB 2.0 in-memory, external access off + config locked, TTL on every read
  sql_guard.py             sqlglot: one read-only statement, no table functions / file readers
  smart_query.py           cache / smart / live routing and table-name rewrite
  decrypt_pipeline.py      fetch → (auto-detect) → decrypt → numeric restore → cache; returns a report
ui/                        session state, charts, empty states
tests/test_core.py
main.py                    shim → app.py (old entry point)
```

## Configuration – `.streamlit/secrets.toml` (gitignored, written by `setup_demo.sh`)

```toml
[snowflake]            # pre-fills Connect; pat is never shown in the UI
account   = "ORG-ACCOUNT"
user      = "SVC_USER"
pat       = "..."
warehouse = "WH"
database  = "DB"
schema    = "SCHEMA"
[encryption]
aes_key = "<base64 32 bytes>"
auto_decrypt_on_connect = true
[[encrypted_tables]]
table_name = "DB.SCHEMA.TABLE"
encrypted_columns = ["FULL_NAME", "SALARY"]   # or ["*"] to auto-detect
[roles]                # email or "@domain"; everyone else is viewer
admin = ["alice@corp.com"]
analyst = ["@corp.com"]
[app]
dev_login = false      # true = demo sign-in with role picker and red banner
[auth]                 # OIDC for st.login – any OIDC provider (Okta, Entra ID, Google, Keycloak …)
redirect_uri = "http://localhost:8501/oauth2callback"
cookie_secret = "<random>"
client_id = "..."
client_secret = "..."
server_metadata_url = "https://<idp>/.well-known/openid-configuration"
```

Runtime settings (TTL, purge interval, live row cap, DuckDB memory, CSV export, auto-decrypt) are edited on the Admin
page and saved to `settings.yaml`; only those keys can ever be written there.

## Security notes

* Plaintext lives only in `st.session_state` (per browser session, in memory) and disappears with the session,
  on sign-out, or at TTL expiry. Nothing is shared between users via `cache_resource`.
* User SQL is checked twice: sqlglot rejects anything but one `SELECT`/`WITH`/set operation and any table function,
  and DuckDB runs with `enable_external_access=false` and `lock_configuration=true`, so `read_csv('/etc/passwd')`,
  `COPY … TO`, `ATTACH` or `SET` fail even if the parser were bypassed.
* `.streamlit/config.toml`: bound to `127.0.0.1`, CORS on, allowed hosts restricted, no telemetry, no error
  details in the browser, grid data export disabled (CSV only through the audited, admin-toggled button), system
  fonts (no Google Fonts calls). Put a TLS reverse proxy in front to share the app on a network.
* Decryption failures are counted per table and surfaced, never echoed as error text.
* Discussion points: AES-CBC has no integrity (AES-GCM would detect tampering); the key sits in a local file
  (an HSM/KMS/Vault fetch at start-up could replace that); dev sign-in is for demos only.

## Upgrading from 0.1 (single `main.py`)

DuckDB 2.0: in-memory only, so the new storage format doesn't matter here. Errors inside a transaction now abort it,
and the app doesn't use explicit transactions. `cache_config.yaml` is replaced by `secrets.toml` + `settings.yaml`,
and the hard-coded default AES key was removed: the app refuses to decrypt without a configured key.
