#!/usr/bin/env bash
# Option 4 – client-side decryption: Snowflake objects, service user + PAT, app config, seed.
set -euo pipefail
cd "$(dirname "$0")"
CONN="${SNOW_CONNECTION:-default}"
APP_IP="${APP_IP:-$(curl -s https://checkip.amazonaws.com)/32}"   # app egress IP(s), comma-separated
IP_LIST=$(python3 -c "import sys;print(','.join(repr(i.strip()) for i in sys.argv[1].split(',') if i.strip()))" "$APP_IP")
export SF_ACCOUNT=$(snow sql -c "$CONN" --format json \
  -q "SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() AS A" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['A'])")

snow sql -c "$CONN" -q "
USE ROLE ACCOUNTADMIN;
CREATE SCHEMA IF NOT EXISTS SOVEREIGNTY_DEMO.OPT4_CLIENT_DECRYPT;
CREATE ROLE IF NOT EXISTS SOV_CLIENTDEC_ROLE;
SET me = CURRENT_USER();
GRANT ROLE SOV_CLIENTDEC_ROLE TO USER IDENTIFIER(\$me);
GRANT USAGE ON DATABASE SOVEREIGNTY_DEMO TO ROLE SOV_CLIENTDEC_ROLE;
GRANT USAGE ON WAREHOUSE SOV_WH TO ROLE SOV_CLIENTDEC_ROLE;
CREATE OR REPLACE NETWORK RULE SOVEREIGNTY_DEMO.OPT4_CLIENT_DECRYPT.APP_EGRESS_IP MODE=INGRESS TYPE=IPV4 VALUE_LIST=($IP_LIST);
GRANT OWNERSHIP ON SCHEMA SOVEREIGNTY_DEMO.OPT4_CLIENT_DECRYPT TO ROLE SOV_CLIENTDEC_ROLE COPY CURRENT GRANTS;
CREATE USER IF NOT EXISTS SOV_CLIENTDEC_APP TYPE=SERVICE DEFAULT_ROLE=SOV_CLIENTDEC_ROLE
  DEFAULT_WAREHOUSE=SOV_WH COMMENT='on-prem decrypting analytics app (Option 4)';
GRANT ROLE SOV_CLIENTDEC_ROLE TO USER SOV_CLIENTDEC_APP;
CREATE NETWORK POLICY IF NOT EXISTS SOV_CLIENTDEC_NP ALLOWED_NETWORK_RULE_LIST=('SOVEREIGNTY_DEMO.OPT4_CLIENT_DECRYPT.APP_EGRESS_IP');
ALTER USER SOV_CLIENTDEC_APP SET NETWORK_POLICY=SOV_CLIENTDEC_NP;
USE ROLE SOV_CLIENTDEC_ROLE; USE SCHEMA SOVEREIGNTY_DEMO.OPT4_CLIENT_DECRYPT;
CREATE TABLE IF NOT EXISTS EMPLOYEES_ENCRYPTED (
  EMPLOYEE_ID INT, DEPARTMENT STRING, COUNTRY STRING, HIRE_YEAR INT, LEVEL INT,
  FULL_NAME STRING, NATIONAL_ID STRING, IBAN STRING, SALARY STRING, BONUS STRING);
" >/dev/null

mkdir -p .secrets
# AES-256 data key: generated once, stays on this host
[[ -f .secrets/aes_key.b64 ]] || python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())" > .secrets/aes_key.b64
chmod 600 .secrets/aes_key.b64
if [[ ! -f .secrets/pat ]]; then
  snow sql -c "$CONN" --format json -q "USE ROLE ACCOUNTADMIN; ALTER USER SOV_CLIENTDEC_APP ADD PROGRAMMATIC ACCESS TOKEN app ROLE_RESTRICTION='SOV_CLIENTDEC_ROLE' DAYS_TO_EXPIRY=30" \
    | python3 -c "import json,sys;d=json.load(sys.stdin);open('.secrets/pat','w').write([r for b in d for r in b if 'token_secret' in r][0]['token_secret'])"
fi

# App config: .streamlit/secrets.toml (gitignored) holds connection, PAT, AES key and encrypted-table list
python3 - <<'PY'
from pathlib import Path
import os
pat = Path(".secrets/pat").read_text().strip(); key = Path(".secrets/aes_key.b64").read_text().strip()
Path(".streamlit/secrets.toml").write_text(f'''[snowflake]
connection_name = "sov_demo"
account   = "{os.environ['SF_ACCOUNT']}"
user      = "SOV_CLIENTDEC_APP"
pat       = "{pat}"
warehouse = "SOV_WH"
database  = "SOVEREIGNTY_DEMO"
schema    = "OPT4_CLIENT_DECRYPT"

[encryption]
aes_key = "{key}"
auto_decrypt_on_connect = true

[[encrypted_tables]]
table_name = "SOVEREIGNTY_DEMO.OPT4_CLIENT_DECRYPT.EMPLOYEES_ENCRYPTED"
encrypted_columns = ["FULL_NAME", "NATIONAL_ID", "IBAN", "SALARY", "BONUS"]

[app]
dev_login = true     # demo only (red banner). Production: configure [auth] OIDC and set false.

[roles]              # email or "@domain"; unmatched users get "viewer"
admin   = ["demo@localhost"]
analyst = ["analyst@localhost"]

# Production SSO (st.login):
# [auth]
# redirect_uri = "http://localhost:8501/oauth2callback"
# cookie_secret = "<long random string>"
# client_id = "..."
# client_secret = "..."
# server_metadata_url = "https://<idp>/.well-known/openid-configuration"
''')
PY
chmod 600 .streamlit/secrets.toml

pixi run python seed_local_encrypt.py
echo
echo "Run:  pixi run app  → sign in (dev) → Connect (pre-filled) → decrypted rows land in the local cache"
