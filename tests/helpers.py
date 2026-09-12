"""Test helpers: a fake clock, sample image bytes, and a client that invokes the
Lambda handlers exactly the way API Gateway and S3 would."""
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, NamedTuple, Optional
from urllib.parse import quote

import boto3

from image_service import handlers

# The smallest byte sequences that pass our "is this really an image?" check.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
NOT_AN_IMAGE = b"<html><script>alert('hi')</script></html>"


class FakeClock:
    """Deterministic time: each call returns a moment one second after the previous one."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class Response(NamedTuple):
    status: int
    body: Any
    headers: Dict[str, str]


class ApiClient:
    def __init__(self, bucket: str, service: Any) -> None:
        self.bucket = bucket
        self.service = service
        self.s3 = boto3.client("s3", region_name="us-east-1")

    def call(
        self,
        handler: Any,
        path: Optional[Dict[str, str]] = None,
        query: Optional[Dict[str, str]] = None,
        body: Any = None,
        user: Optional[str] = None,
        raw_body: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        claims: Optional[Dict[str, str]] = None,
    ) -> Response:
        """Build an API Gateway proxy event, invoke the handler, parse the result."""
        event_headers = dict(headers or {})
        if user:
            event_headers["X-User-Id"] = user
        event = {
            "headers": event_headers,
            "pathParameters": path,
            "queryStringParameters": query,
            "body": raw_body if raw_body is not None else (json.dumps(body) if body is not None else None),
            "isBase64Encoded": False,
            "requestContext": {"authorizer": {"claims": claims}} if claims else {},
        }
        result = handler(event, None)
        parsed = json.loads(result["body"]) if result.get("body") else None
        return Response(result["statusCode"], parsed, result.get("headers", {}))

    # ---- one method per API operation ------------------------------------------------
    def create(self, user: Optional[str] = "alice", title: str = "Sunset",
               content_type: str = "image/png", **extra: Any) -> Response:
        return self.call(handlers.create_image, body={"title": title, "content_type": content_type, **extra}, user=user)

    def list(self, **query: Any) -> Response:
        return self.call(handlers.list_images, query={k: str(v) for k, v in query.items()} or None)

    def get(self, image_id: str) -> Response:
        return self.call(handlers.get_image, path={"image_id": image_id})

    def download(self, image_id: str) -> Response:
        return self.call(handlers.download_image, path={"image_id": image_id})

    def delete(self, image_id: str, user: Optional[str] = "alice") -> Response:
        return self.call(handlers.delete_image, path={"image_id": image_id}, user=user)

    # ---- simulating the client -> S3 upload -------------------------------------------
    def simulate_s3_upload(self, key: str, data: bytes = PNG_BYTES) -> Dict[str, Any]:
        """Put the object in S3, then deliver the ObjectCreated event like the bucket notification does."""
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)
        event = {"Records": [{
            "eventSource": "aws:s3",
            "eventName": "ObjectCreated:Post",
            "s3": {"bucket": {"name": self.bucket}, "object": {"key": quote(key), "size": len(data)}},
        }]}
        return handlers.on_upload_complete(event, None)

    def upload(self, user: str = "alice", data: bytes = PNG_BYTES, **create_kwargs: Any) -> str:
        """Full happy path (create + upload + S3 event). Returns the ACTIVE image's id."""
        created = self.create(user=user, **create_kwargs)
        assert created.status == 201, created.body
        self.simulate_s3_upload(created.body["upload"]["fields"]["key"], data)
        return created.body["image"]["image_id"]

    def object_exists(self, key: str) -> bool:
        return self.s3.list_objects_v2(Bucket=self.bucket, Prefix=key)["KeyCount"] > 0
