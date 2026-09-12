import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3
from botocore.config import Config

from .config import Settings
from .errors import Conflict, Forbidden, NotFound
from .models import CONTENT_TYPE_EXTENSIONS, Image, ImageStatus
from .repository import ImageRepository
from .storage import ImageStorage
from .validation import NewImageRequest, detect_image_type, is_valid_image_id

Clock = Callable[[], datetime]
_S3_KEY_RE = re.compile(r"images/([0-9a-f-]{36})\.[a-z]+")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ImageService:
    def __init__(
        self,
        repository: ImageRepository,
        storage: ImageStorage,
        settings: Settings,
        clock: Clock = _utc_now,
    ) -> None:
        self._repo = repository
        self._storage = storage
        self._settings = settings
        self._clock = clock

    # ---- 1. upload (step 1 of 2): register metadata, hand out an upload form ------
    def create_image(self, user_id: str, request: NewImageRequest) -> Tuple[Image, Dict[str, Any]]:
        now = self._clock()
        image_id = str(uuid.uuid4())
        image = Image(
            image_id=image_id,
            user_id=user_id,
            title=request.title,
            description=request.description,
            tags=request.tags,
            content_type=request.content_type,
            s3_key=f"images/{image_id}.{CONTENT_TYPE_EXTENSIONS[request.content_type]}",
            status=ImageStatus.PENDING,
            created_at=_iso(now),
            expires_at=self._expiry(now),   # abandoned uploads clean themselves up
        )
        self._repo.put(image)
        return image, self._storage.create_upload_form(image.s3_key, image.content_type)

    # ---- 1. upload (step 2 of 2): S3 tells us the file has arrived -------------------
    def complete_upload(self, s3_key: str, size_bytes: Optional[int]) -> str:
        """Verify the uploaded file and activate the image. Returns the outcome (for logs).

        Safe to run more than once for the same object: S3 events can be delivered twice.
        """
        match = _S3_KEY_RE.fullmatch(s3_key)
        if not match:
            return "ignored"

        image = self._repo.get(match.group(1))
        if image is None or image.s3_key != s3_key:
            # Metadata expired or was deleted (e.g. DELETE while the upload form was
            # still valid). Nothing points at this object, so remove it.
            self._storage.delete(s3_key)
            return "orphan_deleted"

        detected = detect_image_type(self._storage.read_first_bytes(s3_key))
        if detected != image.content_type:
            self._storage.delete(s3_key)
            reason = f"File content is not a valid {image.content_type} image"
            self._repo.mark_rejected(image.image_id, reason, self._expiry(self._clock()))
            return "rejected"

        if size_bytes is None:
            size_bytes = self._storage.object_size(s3_key)
        self._repo.mark_active(image.image_id, size_bytes, _iso(self._clock()))
        return "activated"

    # ---- 2. list -------------------------------------------------------------------
    def list_images(
        self,
        user_id: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 20,
        next_token: Optional[str] = None,
    ) -> Tuple[List[Image], Optional[str]]:
        return self._repo.list_active(user_id=user_id, tag=tag, limit=limit, next_token=next_token)

    # ---- 3. view / download --------------------------------------------------------
    def get_image(self, image_id: str) -> Image:
        image = self._repo.get(image_id) if is_valid_image_id(image_id) else None
        if image is None:
            raise NotFound("Image not found")
        return image

    def download_url(self, image_id: str) -> str:
        image = self.get_image(image_id)
        if image.status != ImageStatus.ACTIVE:
            raise Conflict(f"Image is {image.status}, not available for download")
        return self._storage.create_download_url(image.s3_key)

    # ---- 4. delete -----------------------------------------------------------------
    def delete_image(self, caller_id: str, image_id: str) -> None:
        image = self.get_image(image_id)
        if image.user_id != caller_id:
            raise Forbidden("You can only delete your own images")
        # File first, then metadata. If the second call fails the client simply retries;
        # we never end up with an invisible, orphaned file we keep paying for.
        self._storage.delete(image.s3_key)
        self._repo.delete(image.image_id)

    # ---- presentation ----------------------------------------------------------------
    def present(self, image: Image) -> Dict[str, Any]:
        """API representation, with a short-lived download URL once the image is ACTIVE."""
        data = image.to_public_dict()
        if image.status == ImageStatus.ACTIVE:
            data["download_url"] = self._storage.create_download_url(image.s3_key)
        return data

    def _expiry(self, now: datetime) -> int:
        return int((now + timedelta(seconds=self._settings.unfinished_record_ttl_seconds)).timestamp())


def build_service(settings: Settings, clock: Clock = _utc_now) -> ImageService:
    """Wire real boto3 clients into the service."""
    s3_config = Config(
        signature_version="s3v4",
        # Path-style URLs (http://host:4566/bucket/key) work with custom endpoints like LocalStack.
        s3={"addressing_style": "path" if settings.endpoint_url else "auto"},
    )
    dynamodb = boto3.resource("dynamodb", region_name=settings.region, endpoint_url=settings.endpoint_url)
    s3 = boto3.client("s3", region_name=settings.region, endpoint_url=settings.endpoint_url, config=s3_config)
    presign_s3 = s3
    if settings.public_s3_endpoint_url:
        presign_s3 = boto3.client(
            "s3",
            region_name=settings.region,
            endpoint_url=settings.public_s3_endpoint_url,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    storage = ImageStorage(
        s3_client=s3,
        presign_client=presign_s3,
        bucket=settings.bucket_name,
        max_upload_bytes=settings.max_upload_bytes,
        upload_url_ttl_seconds=settings.upload_url_ttl_seconds,
        download_url_ttl_seconds=settings.download_url_ttl_seconds,
    )
    repository = ImageRepository(dynamodb.Table(settings.table_name))
    return ImageService(repository, storage, settings, clock)
 