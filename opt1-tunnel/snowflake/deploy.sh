#!/usr/bin/env bash
# Deploy Option 1 into your Snowflake account (connection $SNOW_CONNECTION).
set -euo pipefail
cd "$(dirname "$0")"
CONN="${SNOW_CONNECTION:-default}"
REGISTRY="$(snow spcs image-registry url -c "$CONN")"
REPO="sovereignty_demo/opt1_tunnel/tunnel_images"
ONPREM_IP="${ONPREM_IP:-$(curl -s https://checkip.amazonaws.com)/32}"   # agent egress IP(s), comma-separated
IP_LIST=$(python3 -c "import sys;print(','.join(repr(i.strip()) for i in sys.argv[1].split(',') if i.strip()))" "$ONPREM_IP")

SECRET_FILE=../agent/.handshake_secret
[[ -f $SECRET_FILE ]] || python3 -c "import secrets;print(secrets.token_urlsafe(32))" > "$SECRET_FILE"
HS=$(cat "$SECRET_FILE")

echo "== prerequisites (agent egress IP $ONPREM_IP)"
snow sql -c "$CONN" --variable "hs=$HS" --variable "ips=$IP_LIST" \
  -q "SET handshake_secret = '<% hs %>'; $(cat 00_prereqs.sql)"

echo "== agent PAT (role-restricted, 30 days) -> agent/.pat"
if [[ ! -s ../agent/.pat ]]; then
  snow sql -c "$CONN" --format json -q "USE ROLE ACCOUNTADMIN;
    ALTER USER SOV_TUNNEL_AGENT ADD PROGRAMMATIC ACCESS TOKEN agent
      ROLE_RESTRICTION = 'SOV_TUNNEL_ROLE' DAYS_TO_EXPIRY = 30" \
  | python3 -c "import json,sys;d=json.load(sys.stdin);open('../agent/.pat','w').write([r for b in d for r in b if 'token_secret' in r][0]['token_secret'])"
  chmod 600 ../agent/.pat
fi

echo "== build & push images"
snow spcs image-registry login -c "$CONN"
REGISTRY=$REGISTRY REPO=$REPO ./build-and-push.sh latest

echo "== service + functions"
snow sql -c "$CONN" -f 10_service.sql

echo "== public half of the data"
snow sql -c "$CONN" -q "USE ROLE SOV_TUNNEL_ROLE; USE WAREHOUSE SOV_WH; USE SCHEMA SOVEREIGNTY_DEMO.OPT1_TUNNEL;
  PUT file://$(cd ../.. && pwd)/shared/sample_data/employees_public.csv @~/sov1 OVERWRITE = TRUE;
  TRUNCATE TABLE EMPLOYEES_PUBLIC;
  COPY INTO EMPLOYEES_PUBLIC FROM @~/sov1/employees_public.csv.gz FILE_FORMAT = (TYPE = CSV SKIP_HEADER = 1) PURGE = TRUE;"

WS=$(snow sql -c "$CONN" --format json -q "USE ROLE SOV_TUNNEL_ROLE; SHOW ENDPOINTS IN SERVICE SOVEREIGNTY_DEMO.OPT1_TUNNEL.TUNNEL_SERVICE" \
  | python3 -c "import json,sys;print([r['ingress_url'] for r in json.load(sys.stdin)[-1] if r['name']=='websocket'][0])")
echo
echo "Websocket endpoint: wss://$WS"
echo "Set SNOWFLAKE_URL=wss://$WS in opt1-tunnel/agent/.env (copy .env.template first), then: pixi run tunnel-up"
