"""Shared fixtures.

Everything runs in-process: `moto` fakes S3 and DynamoDB and the Lambda handlers are
called directly with API-Gateway-shaped events. No Docker or LocalStack needed.
"""
import boto3
import pytest
from moto import mock_aws

from image_service import handlers
from image_service.config import Settings
from image_service.schema import table_definition
from image_service.service import build_service
from tests.helpers import ApiClient, FakeClock


@pytest.fixture(autouse=True)
def aws_environment(monkeypatch):
    """Fake credentials, so a test can never touch a real AWS account."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("ALLOW_HEADER_AUTH", "true")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def settings():
    return Settings(table_name="images-test", bucket_name="images-test-bucket", max_upload_bytes=1024 * 1024)


@pytest.fixture
def aws(settings):
    with mock_aws():
        boto3.resource("dynamodb", region_name="us-east-1").create_table(**table_definition(settings.table_name))
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=settings.bucket_name)
        yield


@pytest.fixture
def table(aws, settings):
    return boto3.resource("dynamodb", region_name="us-east-1").Table(settings.table_name)


@pytest.fixture
def api(aws, settings, monkeypatch):
    service = build_service(settings, clock=FakeClock())
    monkeypatch.setattr(handlers, "get_service", lambda: service)
    return ApiClient(settings.bucket_name, service)
