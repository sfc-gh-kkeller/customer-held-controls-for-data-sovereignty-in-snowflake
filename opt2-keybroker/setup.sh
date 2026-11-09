#!/usr/bin/env bash
# Option 2 – end-to-end setup (needs: aws login, snow connection, docker for CDK bundling)
set -euo pipefail
cd "$(dirname "$0")"
CONN="${SNOW_CONNECTION:-default}"
CDK="npx -y aws-cdk@latest"
export AWS_REGION=eu-central-1 CDK_DEFAULT_REGION=eu-central-1
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

echo "== 1. Snowflake objects (roles, table, decrypt UDF, broker service user)"
# Account identifiers come from the connection, nothing is hard-coded
read -r SF_ACCOUNT SF_LOCATOR < <(snow sql -c "$CONN" --format json \
  -q "SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() AS A, CURRENT_ACCOUNT() AS L" \
  | python3 -c "import json,sys;r=json.load(sys.stdin)[0];print(r['A'],r['L'])")
export SF_ACCOUNT
echo "   account $SF_ACCOUNT (locator $SF_LOCATOR)"
snow sql -c "$CONN" -f snowflake/10_objects.sql >/dev/null
mkdir -p .secrets
if [[ ! -f .secrets/broker_rsa.p8 ]]; then
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out .secrets/broker_rsa.p8 2>/dev/null
  openssl rsa -in .secrets/broker_rsa.p8 -pubout -out .secrets/broker_rsa.pub 2>/dev/null
fi
# Always (re)register the public key, so a re-created service user gets it too
PUB=$(grep -v "PUBLIC KEY" .secrets/broker_rsa.pub | tr -d '\n')
snow sql -c "$CONN" -q "USE ROLE ACCOUNTADMIN; ALTER USER SOV_KEYBROKER_SVC SET RSA_PUBLIC_KEY='$PUB';" >/dev/null

echo "== 2. AWS stack (pass 1)"
cd infra
[[ -d .venv ]] || { uv venv -q --python 3.12 && uv pip install -q aws-cdk-lib constructs; }
$CDK bootstrap "aws://$CDK_DEFAULT_ACCOUNT/$AWS_REGION" -c sf_account_locator="$SF_LOCATOR" >/dev/null
$CDK deploy --require-approval never --outputs-file outputs.json -c sf_account_locator="$SF_LOCATOR" ${CDK_EXTRA:-}
cd ..
out() { python3 -c "import json;print(json.load(open('infra/outputs.json'))['SovereigntyKeyBroker']['$1'])"; }
ROLE_ARN=$(out SnowflakeRoleArn); KEY_URL=$(out KeyEndpoint); SECRET_ARN=$(out BrokerSecretArn)
KEY_PREFIX="${KEY_URL%key}"

echo "== 3. Broker Snowflake credentials -> Secrets Manager"
python3 - "$SECRET_ARN" <<'EOF'
import json, os, subprocess, sys
val = json.dumps({"account": os.environ["SF_ACCOUNT"], "user": "SOV_KEYBROKER_SVC",
                  "role": "SOV_KEYBROKER", "warehouse": "SOV_WH",
                  "private_key": open(".secrets/broker_rsa.p8").read()})
subprocess.run(["aws", "secretsmanager", "put-secret-value", "--secret-id", sys.argv[1],
                "--secret-string", val], check=True, stdout=subprocess.DEVNULL)
EOF

echo "== 4. Snowflake API integration, external function, masking policies"
snow sql -c "$CONN" -f snowflake/20_extfn_policies.sql -D "role_arn=$ROLE_ARN" -D "key_url=$KEY_URL" -D "key_prefix=$KEY_PREFIX" >/dev/null
DESC=$(snow sql -c "$CONN" --format json -q "USE ROLE ACCOUNTADMIN; DESC INTEGRATION SOV_KEYBROKER_API")
IAM_USER=$(echo "$DESC" | python3 -c "import json,sys;d=json.load(sys.stdin)[-1];print([r['property_value'] for r in d if r['property']=='API_AWS_IAM_USER_ARN'][0])")
EXT_ID=$(echo "$DESC" | python3 -c "import json,sys;d=json.load(sys.stdin)[-1];print([r['property_value'] for r in d if r['property']=='API_AWS_EXTERNAL_ID'][0])")

echo "== 5. AWS stack (pass 2: trust Snowflake's IAM user + external id)"
cd infra && $CDK deploy --require-approval never --outputs-file outputs.json \
  -c sf_account_locator="$SF_LOCATOR" -c sf_iam_user_arn="$IAM_USER" -c sf_external_id="$EXT_ID" && cd ..

echo "== 6. Encrypt locally with KMS data keys and load ciphertext"
cd client && [[ -d .venv ]] || uv venv -q --python 3.12
uv pip install -q boto3 "botocore[crt]" cryptography pyotp qrcode
uv run encrypt_and_load.py
echo; echo "Done. Step-up portal: $(cd .. && out StepUpPortalUrl)"
echo "Enroll a user: pixi run enroll <SNOWFLAKE_USER>"
