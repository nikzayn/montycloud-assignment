"""API 3: view/download an image.  API 4: delete an image."""
import uuid
from urllib.parse import urlparse


class TestGetImage:
    def test_returns_metadata_and_a_download_url(self, api):
        image_id = api.upload(title="Sunset", tags=["beach"], description="Goa")

        response = api.get(image_id)

        assert response.status == 200
        image = response.body["image"]
        assert image["image_id"] == image_id
        assert image["title"] == "Sunset"
        assert image["description"] == "Goa"
        assert image["tags"] == ["beach"]
        assert image["content_type"] == "image/png"
        assert image["status"] == "ACTIVE"
        assert "s3_key" not in image and "expires_at" not in image  # internals stay private
        url = urlparse(image["download_url"])
        assert url.path.endswith(f"{image_id}.png")
        assert "X-Amz-Signature" in url.query  # pre-signed, short-lived

    def test_pending_image_has_status_but_no_download_url(self, api):
        image_id = api.create().body["image"]["image_id"]
        image = api.get(image_id).body["image"]
        assert image["status"] == "PENDING"
        assert "download_url" not in image

    def test_unknown_image_returns_404(self, api):
        response = api.get(str(uuid.uuid4()))
        assert response.status == 404
        assert response.body["error"]["code"] == "NOT_FOUND"

    def test_malformed_id_returns_404(self, api):
        assert api.get("not-a-uuid").status == 404


class TestDownloadImage:
    def test_redirects_to_presigned_s3_url(self, api):
        image_id = api.upload()

        response = api.download(image_id)

        assert response.status == 302
        assert f"{image_id}.png" in response.headers["Location"]
        assert "X-Amz-Signature" in response.headers["Location"]

    def test_image_not_yet_uploaded_returns_409(self, api):
        image_id = api.create().body["image"]["image_id"]
        response = api.download(image_id)
        assert response.status == 409
        assert response.body["error"]["code"] == "CONFLICT"

    def test_unknown_image_returns_404(self, api):
        assert api.download(str(uuid.uuid4())).status == 404


class TestDeleteImage:
    def test_owner_can_delete_image_and_file(self, api, table):
        created = api.create().body
        image_id, key = created["image"]["image_id"], created["upload"]["fields"]["key"]
        api.simulate_s3_upload(key)
        assert api.object_exists(key)

        response = api.delete(image_id, user="alice")

        assert response.status == 204
        assert response.body is None
        assert not api.object_exists(key)
        assert "Item" not in table.get_item(Key={"image_id": image_id})
        assert api.get(image_id).status == 404
        assert api.list().body["items"] == []

    def test_can_delete_image_whose_upload_never_happened(self, api):
        image_id = api.create().body["image"]["image_id"]
        assert api.delete(image_id).status == 204

    def test_other_users_cannot_delete(self, api):
        image_id = api.upload(user="alice")

        response = api.delete(image_id, user="mallory")

        assert response.status == 403
        assert response.body["error"]["code"] == "FORBIDDEN"
        assert api.get(image_id).body["image"]["status"] == "ACTIVE"

    def test_requires_caller_identity(self, api):
        image_id = api.upload()
        assert api.delete(image_id, user=None).status == 401

    def test_unknown_image_returns_404(self, api):
        assert api.delete(str(uuid.uuid4())).status == 404

    def test_deleting_twice_returns_404_the_second_time(self, api):
        image_id = api.upload()
        assert api.delete(image_id).status == 204
        assert api.delete(image_id).status == 404
