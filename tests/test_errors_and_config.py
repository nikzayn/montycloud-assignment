"""Cross-cutting behaviour: unexpected errors, configuration, service wiring."""
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

from image_service import handlers
from image_service.apigw import json_response
from image_service.config import Settings
from image_service.models import Image
from image_service.service import build_service


def test_unexpected_errors_become_a_generic_500(api, monkeypatch, caplog):
    def explode(**kwargs):
        raise RuntimeError("database password is hunter2")

    monkeypatch.setattr(api.service, "list_images", explode)

    response = api.list()

    assert response.status == 500
    assert response.body == {"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}}
    assert "hunter2" not in str(response.body)      # internals never reach the client
    assert "Unhandled error" in caplog.text          # ...but they are logged


def test_aws_errors_in_the_upload_trigger_propagate_so_lambda_retries(api, monkeypatch):
    def throttled(*args, **kwargs):
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "UpdateItem")

    created = api.create().body
    monkeypatch.setattr(api.service._repo._table, "update_item", throttled)

    with pytest.raises(ClientError):
        api.simulate_s3_upload(created["upload"]["fields"]["key"])


def test_json_response_serialises_dynamodb_decimals():
    body = json_response(200, {"whole": Decimal("3"), "fraction": Decimal("1.5")})["body"]
    assert body == '{"whole": 3, "fraction": 1.5}'


def test_json_response_refuses_unknown_types():
    with pytest.raises(TypeError):
        json_response(200, {"bad": object()})


def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "t")
    monkeypatch.setenv("BUCKET_NAME", "b")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost.localstack.cloud:4566")
    monkeypatch.setenv("PUBLIC_S3_ENDPOINT_URL", "http://localhost:4566")
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "2048")

    settings = Settings.from_env()

    assert (settings.table_name, settings.bucket_name, settings.region) == ("t", "b", "ap-south-1")
    assert settings.endpoint_url == "http://localhost.localstack.cloud:4566"
    assert settings.public_s3_endpoint_url == "http://localhost:4566"
    assert settings.max_upload_bytes == 2048


def test_presigned_urls_use_the_public_endpoint_when_configured(aws, settings):
    local = Settings(
        table_name=settings.table_name,
        bucket_name=settings.bucket_name,
        endpoint_url="http://localhost.localstack.cloud:4566",
        public_s3_endpoint_url="http://localhost:4566",
    )
    storage = build_service(local)._storage

    assert storage.create_download_url("images/x.png").startswith("http://localhost:4566/images-test-bucket/")
    assert storage.create_upload_form("images/x.png", "image/png")["url"] == "http://localhost:4566/images-test-bucket"


def test_get_service_is_built_once_and_cached(aws, monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "images-test")
    handlers.get_service.cache_clear()
    try:
        assert handlers.get_service() is handlers.get_service()
    finally:
        handlers.get_service.cache_clear()


def test_image_item_round_trip():
    image = Image(image_id="i", user_id="u", title="t", content_type="image/png",
                  s3_key="k", status="ACTIVE", created_at="c", tags=["a"], size_bytes=10)
    item = image.to_item()
    assert "description" not in item and "expires_at" not in item   # empty values not stored
    assert Image.from_item(item) == image
