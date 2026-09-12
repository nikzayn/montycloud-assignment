from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

#allowed content types
CONTENT_TYPES_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/tiff": "tiff",
    "image/bmp": "bmp",
    "image/x-icon": "ico",
    "image/heic": "heic",
    "image/heif": "heif",
}

class ImageStatus:
    PENDING = "pending" #at this stage, metadata is saved, we will wait for file to arrive in S3
    ACTIVE = "active" #file uploaded and verified, which is also visible in lists
    REJECTED = "rejected" #file type wan't a real image of the declared type


@dataclass
class Image:
    image_id: str # UUID4, also the table's partition key
    user_id: str
    title: str
    content_type: str
    s3_key: str
    status: str
    created_at: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    size_bytes: Optional[int] = None
    uploaded_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    expires_at: Optional[int] = None

    def to_item(self) -> Dict[str, Any]:
        """DynamoDB item. Empty/None attributes are simply left out."""
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [])}

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "Image":
        known = {f.name for f in fields(cls)}
        data = {key: value for key, value in item.items() if key in known}
        for number in ("size_bytes", "expires_at"):
            if data.get(number) is not None:
                data[number] = int(data[number])
        data["tags"] = list(data.get("tags", []))
        return cls(**data)

    def to_public_dict(self) -> Dict[str, Any]:
        """What API clients see. Internal fields (s3_key, expires_at) stay private."""
        public = {
            "image_id": self.image_id,
            "user_id": self.user_id,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "status": self.status,
            "created_at": self.created_at,
            "uploaded_at": self.uploaded_at,
        }
        if self.rejection_reason:
            public["rejection_reason"] = self.rejection_reason
        return public



