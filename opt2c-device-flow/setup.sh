#!/usr/bin/env bash
# Option 2c setup: (re)deploys the shared AWS stack (keeps the Option 2 trust context),
# sets the bench token, and creates the Snowflake objects.
set -euo pipefail
cd "$(dirname "$0")"
CONN=${CONN:-${SNOW_CONNECTION:-default}}
CDK="npx -y aws-cdk@latest"
KB=../opt2-keybroker

echo "== 1. Option 2 trust context (so the redeploy keeps Option 2 working)"
SF_LOCATOR=$(snow sql -c "$CONN" --format json -q "SELECT CURRENT_ACCOUNT() AS L" | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['L'])")
DESC=$(snow sql -c "$CONN" --format json -q "USE ROLE ACCOUNTADMIN; DESC INTEGRATION SOV_KEYBROKER_API")
prop() { echo "$DESC" | python3 -c "import json,sys;d=json.load(sys.stdin)[-1];print([r['property_value'] for r in d if r['property']=='$1'][0])"; }
IAM_USER=$(prop API_AWS_IAM_USER_ARN); EXT_ID=$(prop API_AWS_EXTERNAL_ID)

echo "== 2. AWS stack (adds mock IdP, 2c broker, state table, signing secret)"
(cd $KB/infra && { [[ -d .venv ]] || { uv venv -q --python 3.12 && uv pip install -q aws-cdk-lib constructs; }; } \
  && $CDK deploy --require-approval never --outputs-file outputs.json \
       -c sf_account_locator="$SF_LOCATOR" -c sf_iam_user_arn="$IAM_USER" -c sf_external_id="$EXT_ID")
out() { python3 -c "import json;print(json.load(open('$KB/infra/outputs.json'))['SovereigntyKeyBroker']['$1'])"; }
host() { out "$1" | sed -E 's#https://([^/]+)/?#\1#'; }
IDP_HOST=$(host Opt2cIdpUrl); BROKER_HOST=$(host Opt2cBrokerUrl); SECRET=$(out Opt2cSigningSecretArn)

echo "== 3. Bench token (only for the bake-off probe UDF)"
mkdir -p .secrets
BENCH=$(python3 - "$SECRET" <<'EOF'
import json, secrets, subprocess, sys
cur = json.loads(subprocess.run(["aws", "secretsmanager", "get-secret-value", "--region", "eu-central-1", "--secret-id", sys.argv[1],
                                 "--query", "SecretString", "--output", "text"], capture_output=True,
                                text=True, check=True).stdout)
if not cur.get("bench_token"):
    cur["bench_token"] = secrets.token_urlsafe(24)
    subprocess.run(["aws", "secretsmanager", "put-secret-value", "--region", "eu-central-1", "--secret-id", sys.argv[1],
                    "--secret-string", json.dumps(cur)], check=True, stdout=subprocess.DEVNULL)
print(cur["bench_token"])
EOF
)
umask 077; echo "$BENCH" > .secrets/bench_token.txt

echo "== 4. Snowflake objects (EAI, UDFs, wrappers, data copy)"
snow sql -c "$CONN" -f snowflake/10_objects.sql -D "idp_host=$IDP_HOST" -D "broker_host=$BROKER_HOST" \
  -D "bench_token=$BENCH" >/dev/null
echo "== 5. Option 2d: remote decrypt (key stays in AWS)"
snow sql -c "$CONN" -f snowflake/30_remote_decrypt.sql -D "broker_host=$BROKER_HOST" >/dev/null
echo "Done. IdP: https://$IDP_HOST/device   Broker: https://$BROKER_HOST"
