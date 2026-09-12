"""API 1: uploading an image with metadata (POST /images + the S3 upload trigger)."""
import base64
import json
import uuid

from image_service import handlers
from tests.helpers import JPEG_BYTES, NOT_AN_IMAGE, PNG_BYTES


class TestCreateImage:
    def test_returns_pending_image_and_presigned_upload_form(self, api):
        response = api.create(title="Sunset", tags=["#Beach"], description="Goa")

        assert response.status == 201
        image = response.body["image"]
        assert image["status"] == "PENDING"
        assert image["user_id"] == "alice"
        assert image["title"] == "Sunset"
        assert image["tags"] == ["beach"]
        assert "download_url" not in image  # nothing to download yet

        upload = response.body["upload"]
        assert upload["method"] == "POST"
        assert upload["file_field"] == "file"
        assert upload["fields"]["key"] == f"images/{image['image_id']}.png"
        assert upload["fields"]["Content-Type"] == "image/png"

    def test_upload_policy_enforces_content_type_and_size(self, api, settings):
        fields = api.create(content_type="image/jpeg").body["upload"]["fields"]
        policy = json.loads(base64.b64decode(fields["policy"]))

        assert {"Content-Type": "image/jpeg"} in policy["conditions"]
        assert ["content-length-range", 1, settings.max_upload_bytes] in policy["conditions"]

    def test_metadata_is_persisted_with_a_cleanup_ttl(self, api, table):
        image_id = api.create().body["image"]["image_id"]

        item = table.get_item(Key={"image_id": image_id})["Item"]
        assert item["status"] == "PENDING"
        assert item["s3_key"] == f"images/{image_id}.png"
        assert item["expires_at"] > 0  # abandoned uploads are removed by DynamoDB TTL

    def test_extension_follows_content_type(self, api):
        fields = api.create(content_type="image/webp").body["upload"]["fields"]
        assert fields["key"].endswith(".webp")

    def test_requires_caller_identity(self, api):
        response = api.create(user=None)
        assert response.status == 401
        assert response.body["error"]["code"] == "UNAUTHORIZED"

    def test_header_identity_is_ignored_unless_explicitly_allowed(self, api, monkeypatch):
        monkeypatch.setenv("ALLOW_HEADER_AUTH", "false")
        assert api.create(user="alice").status == 401

    def test_identity_from_cognito_authorizer_claims(self, api, monkeypatch):
        monkeypatch.setenv("ALLOW_HEADER_AUTH", "false")
        response = api.call(
            handlers.create_image,
            body={"title": "From Cognito", "content_type": "image/png"},
            claims={"sub": "cognito-user-1"},
        )
        assert response.status == 201
        assert response.body["image"]["user_id"] == "cognito-user-1"

    def test_invalid_user_id_is_rejected(self, api):
        assert api.create(user="bad user!").status == 400

    def test_rejects_invalid_metadata(self, api):
        response = api.create(content_type="application/pdf")
        assert response.status == 400
        assert response.body["error"]["code"] == "VALIDATION_ERROR"

    def test_rejects_malformed_json(self, api):
        response = api.call(handlers.create_image, raw_body="{not json", user="alice")
        assert response.status == 400
        assert "valid JSON" in response.body["error"]["message"]

    def test_rejects_empty_body(self, api):
        response = api.call(handlers.create_image, user="alice")
        assert response.status == 400
        assert "required" in response.body["error"]["message"]

    def test_accepts_base64_encoded_body(self, api):
        raw = base64.b64encode(json.dumps({"title": "b64", "content_type": "image/png"}).encode()).decode()
        response = handlers.create_image(
            {"headers": {"X-User-Id": "alice"}, "body": raw, "isBase64Encoded": True}, None
        )
        assert response["statusCode"] == 201

    def test_rejects_invalid_base64_body(self, api):
        response = handlers.create_image(
            {"headers": {"X-User-Id": "alice"}, "body": "not*base64", "isBase64Encoded": True}, None
        )
        assert response["statusCode"] == 400

    def test_responses_include_cors_headers(self, api):
        assert api.create().headers["Access-Control-Allow-Origin"] == "*"


class TestUploadComplete:
    """What happens after the client uploads the file to S3 (the S3 -> Lambda trigger)."""

    def test_valid_upload_activates_image(self, api, table):
        created = api.create().body
        image_id, key = created["image"]["image_id"], created["upload"]["fields"]["key"]

        result = api.simulate_s3_upload(key, PNG_BYTES)

        assert result["processed"][0]["outcome"] == "activated"
        image = api.get(image_id).body["image"]
        assert image["status"] == "ACTIVE"
        assert image["size_bytes"] == len(PNG_BYTES)
        assert image["uploaded_at"] is not None
        assert "download_url" in image
        assert "expires_at" not in table.get_item(Key={"image_id": image_id})["Item"]  # TTL removed

    def test_file_that_is_not_an_image_is_rejected_and_removed(self, api):
        created = api.create(content_type="image/png").body
        image_id, key = created["image"]["image_id"], created["upload"]["fields"]["key"]

        result = api.simulate_s3_upload(key, NOT_AN_IMAGE)

        assert result["processed"][0]["outcome"] == "rejected"
        image = api.get(image_id).body["image"]
        assert image["status"] == "REJECTED"
        assert "not a valid image/png" in image["rejection_reason"]
        assert not api.object_exists(key)

    def test_file_of_a_different_type_than_declared_is_rejected(self, api):
        created = api.create(content_type="image/png").body
        result = api.simulate_s3_upload(created["upload"]["fields"]["key"], JPEG_BYTES)
        assert result["processed"][0]["outcome"] == "rejected"

    def test_duplicate_s3_events_are_harmless(self, api):
        created = api.create().body
        key = created["upload"]["fields"]["key"]

        api.simulate_s3_upload(key)
        second = api.simulate_s3_upload(key)

        assert second["processed"][0]["outcome"] == "activated"
        assert api.get(created["image"]["image_id"]).body["image"]["status"] == "ACTIVE"

    def test_object_without_metadata_is_deleted(self, api):
        key = f"images/{uuid.uuid4()}.png"
        result = api.simulate_s3_upload(key)
        assert result["processed"][0]["outcome"] == "orphan_deleted"
        assert not api.object_exists(key)

    def test_upload_after_image_was_deleted_is_cleaned_up(self, api):
        created = api.create().body
        key = created["upload"]["fields"]["key"]
        assert api.delete(created["image"]["image_id"]).status == 204

        result = api.simulate_s3_upload(key)  # the upload form was still valid

        assert result["processed"][0]["outcome"] == "orphan_deleted"
        assert not api.object_exists(key)

    def test_objects_outside_the_images_prefix_are_ignored(self, api):
        result = api.simulate_s3_upload("other/readme.txt", b"hello")
        assert result["processed"][0]["outcome"] == "ignored"
        assert api.object_exists("other/readme.txt")

    def test_size_is_looked_up_when_event_has_none(self, api):
        created = api.create().body
        key = created["upload"]["fields"]["key"]
        api.s3.put_object(Bucket=api.bucket, Key=key, Body=PNG_BYTES)

        handlers.on_upload_complete({"Records": [{"s3": {"object": {"key": key}}}]}, None)

        assert api.get(created["image"]["image_id"]).body["image"]["size_bytes"] == len(PNG_BYTES)
