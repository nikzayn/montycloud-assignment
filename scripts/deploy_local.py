#!/usr/bin/env python3
"""Deploy the whole stack into LocalStack with plain boto3 (no extra tooling).

Creates, in order:
  1. DynamoDB table `images` (+ 2 GSIs, TTL on expires_at)
  2. S3 bucket `images-bucket` (private; CORS so browsers can upload directly)
  3. One Lambda per API route, plus the S3 upload trigger
  4. API Gateway REST API wired to those Lambdas (stage `local`)
  5. S3 -> Lambda notification for new objects under images/

Safe to re-run: existing resources are reused, Lambda code is updated, and the API is recreated.

    python scripts/deploy_local.py
"""
import io
import json
import os
import pathlib
import sys
import time
import urllib.request
import zipfile

import boto3
from botocore.exceptions import ClientError

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from image_service.schema import TTL_ATTRIBUTE, table_definition  # noqa: E402

LOCALSTACK = os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")
REGION = "us-east-1"
TABLE_NAME = "images"
BUCKET_NAME = "images-bucket"
API_NAME = "image-service"
STAGE = "local"
ROLE_NAME = "image-service-lambda"
RUNTIME = "python3.12"

# Lambda function name -> handler inside the deployment package.
FUNCTIONS = {
    "images-create": "image_service.handlers.create_image",
    "images-list": "image_service.handlers.list_images",
    "images-get": "image_service.handlers.get_image",
    "images-download": "image_service.handlers.download_image",
    "images-delete": "image_service.handlers.delete_image",
    "images-on-upload": "image_service.handlers.on_upload_complete",
}

# HTTP route -> Lambda function.
ROUTES = [
    ("/images", "POST", "images-create"),
    ("/images", "GET", "images-list"),
    ("/images/{image_id}", "GET", "images-get"),
    ("/images/{image_id}", "DELETE", "images-delete"),
    ("/images/{image_id}/download", "GET", "images-download"),
]

LAMBDA_ENV = {
    "TABLE_NAME": TABLE_NAME,
    "BUCKET_NAME": BUCKET_NAME,
    # Inside Lambda containers LocalStack is reachable under this name...
    "AWS_ENDPOINT_URL": "http://localhost.localstack.cloud:4566",
    # ...while pre-signed URLs must work from your laptop.
    "PUBLIC_S3_ENDPOINT_URL": LOCALSTACK,
    # Local stand-in for a Cognito authorizer. NEVER enable in a real deployment.
    "ALLOW_HEADER_AUTH": "true",
}


def aws(service):
    return boto3.client(
        service,
        endpoint_url=LOCALSTACK,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def step(message):
    print(f"==> {message}", flush=True)


def wait_for_localstack(timeout=90):
    step(f"Waiting for LocalStack at {LOCALSTACK}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{LOCALSTACK}/_localstack/health", timeout=3) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(2)
    sys.exit("LocalStack is not reachable. Did you run `docker compose up -d`?")


def create_table(ddb):
    step(f"DynamoDB table '{TABLE_NAME}'")
    definition = table_definition(TABLE_NAME)
    try:
        ddb.create_table(**definition)
        ddb.get_waiter("table_exists").wait(TableName=TABLE_NAME)
        ddb.update_time_to_live(
            TableName=TABLE_NAME,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": TTL_ATTRIBUTE},
        )
        return
    except ClientError as error:
        if error.response["Error"]["Code"] != "ResourceInUseException":
            raise
    existing = ddb.describe_table(TableName=TABLE_NAME)["Table"].get("GlobalSecondaryIndexes", [])
    expected = {index["IndexName"] for index in definition["GlobalSecondaryIndexes"]}
    if {index["IndexName"] for index in existing} != expected:
        sys.exit(
            f"Table '{TABLE_NAME}' exists with a different schema (from another version of this project).\n"
            "Start LocalStack fresh, then deploy again:  make down && make up && make deploy"
        )
    print("    already exists")


def create_bucket(s3):
    step(f"S3 bucket '{BUCKET_NAME}'")
    try:
        s3.create_bucket(Bucket=BUCKET_NAME)
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
        print("    already exists")
    s3.put_bucket_cors(Bucket=BUCKET_NAME, CORSConfiguration={"CORSRules": [{
        "AllowedMethods": ["GET", "POST"],
        "AllowedOrigins": ["*"],
        "AllowedHeaders": ["*"],
        "MaxAgeSeconds": 3000,
    }]})


def create_role(iam, account_id):
    """LocalStack doesn't enforce IAM, but this documents the permissions the Lambdas need."""
    step(f"IAM role '{ROLE_NAME}'")
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole",
    }]}
    try:
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust))
    except ClientError as error:
        if error.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="image-service", PolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow",
             "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                        "dynamodb:DeleteItem", "dynamodb:Query"],
             "Resource": [f"arn:aws:dynamodb:{REGION}:{account_id}:table/{TABLE_NAME}",
                          f"arn:aws:dynamodb:{REGION}:{account_id}:table/{TABLE_NAME}/index/*"]},
            {"Effect": "Allow",
             "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
             "Resource": f"arn:aws:s3:::{BUCKET_NAME}/images/*"},
            {"Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": "*"},
        ],
    }))
    return iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]


def build_package():
    """Zip src/image_service. boto3 is already in the Lambda runtime, so that's all we need."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "src" / "image_service").glob("*.py")):
            archive.write(path, f"image_service/{path.name}")
    return buffer.getvalue()


def deploy_functions(lam, role_arn, package):
    arns = {}
    for name, handler in FUNCTIONS.items():
        step(f"Lambda '{name}' -> {handler}")
        try:
            lam.create_function(
                FunctionName=name,
                Runtime=RUNTIME,
                Role=role_arn,
                Handler=handler,
                Code={"ZipFile": package},
                Timeout=15,
                MemorySize=256,
                Environment={"Variables": LAMBDA_ENV},
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ResourceConflictException":
                raise
            print("    exists, updating code and configuration")
            lam.update_function_code(FunctionName=name, ZipFile=package)
            lam.get_waiter("function_updated_v2").wait(FunctionName=name)
            lam.update_function_configuration(
                FunctionName=name, Handler=handler, Environment={"Variables": LAMBDA_ENV}
            )
        lam.get_waiter("function_active_v2").wait(FunctionName=name)
        arns[name] = lam.get_function(FunctionName=name)["Configuration"]["FunctionArn"]
    return arns


def allow_invoke(lam, function_name, statement_id, principal, source_arn):
    try:
        lam.add_permission(
            FunctionName=function_name,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal=principal,
            SourceArn=source_arn,
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ResourceConflictException":
            raise


def deploy_api(apigw, lam, function_arns, account_id):
    step(f"API Gateway REST API '{API_NAME}' (recreated from scratch)")
    for api in apigw.get_rest_apis(limit=500)["items"]:
        if api["name"] == API_NAME:
            apigw.delete_rest_api(restApiId=api["id"])

    api_id = apigw.create_rest_api(name=API_NAME, description="Image upload & storage service")["id"]
    resources = {r["path"]: r["id"] for r in apigw.get_resources(restApiId=api_id)["items"]}

    def resource_id(path):
        """Create /images, /images/{image_id}, ... on demand, parents first."""
        if path not in resources:
            parent, _, part = path.rpartition("/")
            resources[path] = apigw.create_resource(
                restApiId=api_id, parentId=resource_id(parent or "/"), pathPart=part
            )["id"]
        return resources[path]

    for path, method, function in ROUTES:
        print(f"    {method:6} {path} -> {function}")
        rid = resource_id(path)
        apigw.put_method(restApiId=api_id, resourceId=rid, httpMethod=method, authorizationType="NONE")
        apigw.put_integration(
            restApiId=api_id,
            resourceId=rid,
            httpMethod=method,
            type="AWS_PROXY",                # the whole HTTP request is handed to the Lambda
            integrationHttpMethod="POST",
            uri=f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/"
                f"{function_arns[function]}/invocations",
        )
        allow_invoke(lam, function, f"apigw-{api_id}-{method}-{rid}", "apigateway.amazonaws.com",
                     f"arn:aws:execute-api:{REGION}:{account_id}:{api_id}/*/{method}{path}")

    apigw.create_deployment(restApiId=api_id, stageName=STAGE)
    return api_id


def connect_upload_trigger(s3, lam, function_arns):
    step("S3 ObjectCreated (images/*) -> images-on-upload")
    allow_invoke(lam, "images-on-upload", "s3-invoke", "s3.amazonaws.com", f"arn:aws:s3:::{BUCKET_NAME}")
    s3.put_bucket_notification_configuration(
        Bucket=BUCKET_NAME,
        NotificationConfiguration={"LambdaFunctionConfigurations": [{
            "LambdaFunctionArn": function_arns["images-on-upload"],
            "Events": ["s3:ObjectCreated:*"],
            "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "images/"}]}},
        }]},
    )


def main():
    wait_for_localstack()
    create_table(aws("dynamodb"))
    create_bucket(aws("s3"))
    account_id = aws("sts").get_caller_identity()["Account"]  # 000000000000 in LocalStack
    role_arn = create_role(aws("iam"), account_id)
    lam = aws("lambda")
    function_arns = deploy_functions(lam, role_arn, build_package())
    api_id = deploy_api(aws("apigateway"), lam, function_arns, account_id)
    connect_upload_trigger(aws("s3"), lam, function_arns)

    api_url = f"{LOCALSTACK}/restapis/{api_id}/{STAGE}/_user_request_"
    (ROOT / ".api_url").write_text(api_url + "\n")
    print()
    print("Deployed! API base URL (saved to .api_url):")
    print(f"    {api_url}")
    print("Alternative host-based URL:")
    print(f"    http://{api_id}.execute-api.localhost.localstack.cloud:4566/{STAGE}")
    print()
    print("Try it:")
    print("    python scripts/client.py upload path/to/photo.jpg --user alice --tags travel")


if __name__ == "__main__":
    main()
