#!/usr/bin/env bash
# Finish the benchmark after `aws login`: redeploy the AWS stack with the broker call-back fix (code change only;
# keys, grants table, enrolment and Snowflake objects are kept), rerun Option 2, rebuild bench/RESULTS.md.
#   aws login && SNOW_CONNECTION=<conn> DEMO_USER=<user> bash bench/after_aws_login.sh
set -euo pipefail
cd "$(dirname "$0")/.."
CONN="${SNOW_CONNECTION:-default}"
CDK="npx -y aws-cdk@latest"
export AWS_REGION=eu-central-1 CDK_DEFAULT_REGION=eu-central-1 JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

echo "== 1. Redeploy the stack (keeps the Snowflake trust context)"
SF_LOCATOR=$(snow sql -c "$CONN" --format json -q "SELECT CURRENT_ACCOUNT() AS L" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['L'])")
DESC=$(snow sql -c "$CONN" --format json -q "USE ROLE ACCOUNTADMIN; DESC INTEGRATION SOV_KEYBROKER_API")
prop() { echo "$DESC" | python3 -c "import json,sys;d=json.load(sys.stdin)[-1];print([r['property_value'] for r in d if r['property']=='$1'][0])"; }
(cd opt2-keybroker/infra && $CDK deploy --require-approval never --outputs-file outputs.json \
   -c sf_account_locator="$SF_LOCATOR" -c sf_iam_user_arn="$(prop API_AWS_IAM_USER_ARN)" \
   -c sf_external_id="$(prop API_AWS_EXTERNAL_ID)" >/dev/null)

echo "== 2. Rerun the Option 2 benchmark and rebuild the report"
python3 bench/run_all.py opt2
