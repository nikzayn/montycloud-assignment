"""S3"""

from typing import Any, Dict

class ImageStorage:
    def __init__(
        self,
        s3_client: Any,
        bucket: str,
        max_upload_bytes: int,
        upload_url_ttl_seconds: int,
        download_url_ttl_seconds: int,
        presign_client: Any = None,
    ) -> None:
        self._s3 = s3_client
        self._presign = presign_client or s3_client
        self._bucket = bucket
        self._max_upload_bytes = max_upload_bytes
        self._upload_ttl = upload_url_ttl_seconds
        self._download_ttl = download_url_ttl_seconds

    def create_upload_form(self, key: str, content_type: str) -> Dict[str, Any]:
        """Pre-signed POST form: the client uploads the file straight to S3.

        S3 itself enforces the signed policy (exact key, exact Content-Type, max size),
        so oversized or re-targeted uploads are rejected before they cost us anything.
        """
        form = self._presign.generate_presigned_post(
            Bucket=self._bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, self._max_upload_bytes],
            ],
            ExpiresIn=self._upload_ttl,
        )
        return {
            "method": "POST",
            "url": form["url"],
            "fields": form["fields"],
            "file_field": "file",
            "max_size_bytes": self._max_upload_bytes,
            "expires_in_seconds": self._upload_ttl,
        }

    def create_download_url(self, key: str) -> str:
        return self._presign.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=self._download_ttl,
        )

    def read_first_bytes(self, key: str, count: int = 16) -> bytes:
        """Ranged GET: we only need the file header to identify the format."""
        response = self._s3.get_object(Bucket=self._bucket, Key=key, Range=f"bytes=0-{count - 1}")
        return response["Body"].read()

    def object_size(self, key: str) -> int:
        return self._s3.head_object(Bucket=self._bucket, Key=key)["ContentLength"]

    def delete(self, key: str) -> None:
        """Idempotent: deleting a missing object is not an error in S3."""
        self._s3.delete_object(Bucket=self._bucket, Key=key)
