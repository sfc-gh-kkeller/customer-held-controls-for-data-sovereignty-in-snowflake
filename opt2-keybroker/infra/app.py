"""CDK stack for Option 2 – external key broker with step-up.

Deploy:
  cdk deploy -c sf_account_locator=<SNOWFLAKE_ACCOUNT_LOCATOR> \
             [-c sf_iam_user_arn=<API_AWS_IAM_USER_ARN> -c sf_external_id=<API_AWS_EXTERNAL_ID>]
The second pair comes from DESC API INTEGRATION after the first deploy.
"""
import os

from aws_cdk import (App, BundlingOptions, CfnOutput, Duration, RemovalPolicy, Stack,
                     aws_apigateway as apigw, aws_dynamodb as ddb, aws_iam as iam,
                     aws_kms as kms, aws_lambda as lambda_, aws_logs as logs,
                     aws_secretsmanager as sm)
from constructs import Construct

HERE = os.path.dirname(os.path.abspath(__file__))
LAMBDA_DIR = os.path.join(HERE, "..", "lambda")
LAMBDA_2C_DIR = os.path.join(HERE, "..", "..", "opt2c-device-flow", "lambda")


def py_code(name: str, base: str = LAMBDA_DIR) -> lambda_.Code:
    return lambda_.Code.from_asset(
        os.path.join(base, name),
        bundling=BundlingOptions(
            image=lambda_.Runtime.PYTHON_3_12.bundling_image,
            platform="linux/arm64",
            command=["bash", "-c",
                     "pip install -q -r requirements.txt -t /asset-output && cp -au . /asset-output"]))


class SovereigntyKeyBrokerStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kw):
        super().__init__(scope, cid, **kw)
        locator = self.node.try_get_context("sf_account_locator")
        if not locator:
            raise ValueError("pass -c sf_account_locator=<locator> (SELECT CURRENT_ACCOUNT()); setup.sh does this")
        sf_iam_user_arn = self.node.try_get_context("sf_iam_user_arn")
        sf_external_id = self.node.try_get_context("sf_external_id")

        cmk = kms.Key(self, "DataKeyWrappingKey", enable_key_rotation=True,
                      alias="alias/sovereignty-datakeys",
                      removal_policy=RemovalPolicy.DESTROY, pending_window=Duration.days(7))

        def table(name, pk, sk=None, ttl=None):
            return ddb.Table(self, name, partition_key=ddb.Attribute(name=pk, type=ddb.AttributeType.STRING),
                             sort_key=ddb.Attribute(name=sk, type=ddb.AttributeType.STRING) if sk else None,
                             billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                             encryption=ddb.TableEncryption.AWS_MANAGED,
                             time_to_live_attribute=ttl, removal_policy=RemovalPolicy.DESTROY)

        keys_t = table("WrappedKeys", "classification")
        grants_t = table("StepUpGrants", "user", "classification", ttl="expires_at")
        users_t = table("StepUpUsers", "user")

        # Snowflake key-pair credentials for the query_id call-back (value set by setup script)
        sf_secret = sm.Secret(self, "BrokerSnowflakeCreds", secret_name="sovereignty-demo/keybroker-snowflake",
                              removal_policy=RemovalPolicy.DESTROY)

        broker = lambda_.Function(
            self, "KeyBroker", runtime=lambda_.Runtime.PYTHON_3_12, architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler", code=py_code("broker"), timeout=Duration.seconds(25),
            memory_size=256, reserved_concurrent_executions=None,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={"GRANTS_TABLE": grants_t.table_name, "KEYS_TABLE": keys_t.table_name,
                         "SF_SECRET_ARN": sf_secret.secret_arn, "SNOWFLAKE_ACCOUNT_LOCATOR": locator})
        grants_t.grant_read_data(broker)
        keys_t.grant_read_data(broker)
        sf_secret.grant_read(broker)
        cmk.grant_decrypt(broker)

        stepup = lambda_.Function(
            self, "StepUpPortal", runtime=lambda_.Runtime.PYTHON_3_12, architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler", code=py_code("stepup"), timeout=Duration.seconds(10),
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={"GRANTS_TABLE": grants_t.table_name, "USERS_TABLE": users_t.table_name,
                         "MAX_GRANT_MINUTES": "60", "CLASSIFICATIONS": "salary,pii"})
        grants_t.grant_read_write_data(stepup)
        users_t.grant_read_data(stepup)
        portal_url = stepup.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)

        # Role Snowflake assumes to call API Gateway
        trust = (iam.ArnPrincipal(sf_iam_user_arn).with_conditions({"StringEquals": {"sts:ExternalId": sf_external_id}})
                 if sf_iam_user_arn and sf_external_id else iam.AccountRootPrincipal())
        sf_role = iam.Role(self, "SnowflakeExtFnRole", role_name="sovereignty-snowflake-extfn", assumed_by=trust)

        api = apigw.RestApi(
            self, "KeyBrokerApi", rest_api_name="sovereignty-keybroker",
            endpoint_types=[apigw.EndpointType.REGIONAL],
            deploy_options=apigw.StageOptions(stage_name="prod", throttling_rate_limit=50, throttling_burst_limit=100),
            policy=iam.PolicyDocument(statements=[iam.PolicyStatement(
                actions=["execute-api:Invoke"], principals=[iam.ArnPrincipal(sf_role.role_arn)],
                resources=["execute-api:/*"])]))
        res = api.root.add_resource("key")
        res.add_method("POST", apigw.LambdaIntegration(broker), authorization_type=apigw.AuthorizationType.IAM)
        sf_role.add_to_policy(iam.PolicyStatement(actions=["execute-api:Invoke"],
                                                  resources=[api.arn_for_execute_api("POST", "/key", "prod")]))

        # ---------------- Option 2c: mock IdP (device flow) + broker for External Access UDFs
        state_t = ddb.Table(self, "Opt2cState", partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
                            billing_mode=ddb.BillingMode.PAY_PER_REQUEST, encryption=ddb.TableEncryption.AWS_MANAGED,
                            time_to_live_attribute="ttl", removal_policy=RemovalPolicy.DESTROY)
        signing = sm.Secret(self, "Opt2cSigning", secret_name="sovereignty-demo/opt2c-signing",
                            removal_policy=RemovalPolicy.DESTROY,
                            generate_secret_string=sm.SecretStringGenerator(
                                secret_string_template='{"bench_token": ""}', generate_string_key="hs256",
                                password_length=48, exclude_punctuation=True))
        idp = lambda_.Function(
            self, "Opt2cMockIdp", runtime=lambda_.Runtime.PYTHON_3_12, architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler", code=py_code("idp", LAMBDA_2C_DIR), timeout=Duration.seconds(10),
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={"STATE_TABLE": state_t.table_name, "USERS_TABLE": users_t.table_name,
                         "SIGNING_SECRET_ARN": signing.secret_arn, "CLASSIFICATIONS": "salary,pii",
                         "MAX_API_KEY_HOURS": "8"})
        state_t.grant_read_write_data(idp)
        users_t.grant_read_data(idp)
        signing.grant_read(idp)
        idp_url = idp.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)

        broker2c = lambda_.Function(
            self, "Opt2cBroker", runtime=lambda_.Runtime.PYTHON_3_12, architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler", code=py_code("broker2c", LAMBDA_2C_DIR), timeout=Duration.seconds(25),
            memory_size=256, log_retention=logs.RetentionDays.ONE_MONTH,
            environment={"STATE_TABLE": state_t.table_name, "KEYS_TABLE": keys_t.table_name,
                         "SF_SECRET_ARN": sf_secret.secret_arn, "SIGNING_SECRET_ARN": signing.secret_arn,
                         "SNOWFLAKE_ACCOUNT_LOCATOR": locator})
        state_t.grant_read_write_data(broker2c)
        keys_t.grant_read_data(broker2c)
        sf_secret.grant_read(broker2c)
        signing.grant_read(broker2c)
        cmk.grant_decrypt(broker2c)
        broker2c_url = broker2c.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)

        CfnOutput(self, "Opt2cIdpUrl", value=idp_url.url)
        CfnOutput(self, "Opt2cBrokerUrl", value=broker2c_url.url)
        CfnOutput(self, "Opt2cSigningSecretArn", value=signing.secret_arn)
        CfnOutput(self, "Opt2cBrokerLogGroup", value=f"/aws/lambda/{broker2c.function_name}")

        CfnOutput(self, "KeyEndpoint", value=f"{api.url}key")
        CfnOutput(self, "SnowflakeRoleArn", value=sf_role.role_arn)
        CfnOutput(self, "StepUpPortalUrl", value=portal_url.url)
        CfnOutput(self, "KmsKeyArn", value=cmk.key_arn)
        CfnOutput(self, "KeysTable", value=keys_t.table_name)
        CfnOutput(self, "UsersTable", value=users_t.table_name)
        CfnOutput(self, "GrantsTable", value=grants_t.table_name)
        CfnOutput(self, "BrokerSecretArn", value=sf_secret.secret_arn)
        CfnOutput(self, "BrokerLogGroup", value=f"/aws/lambda/{broker.function_name}")


app = App()
SovereigntyKeyBrokerStack(app, "SovereigntyKeyBroker",
                      env={"account": os.getenv("CDK_DEFAULT_ACCOUNT"), "region": "eu-central-1"})
app.synth()
