"""The image metadata record"""
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

# Allowed upload types, and the file extension used in the S3 key.
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}

class ImageStatus:
    PENDING = "PENDING"    # metadata saved, waiting for the file to arrive in S3
    ACTIVE = "ACTIVE"      # file uploaded and verified; visible in listings
    REJECTED = "REJECTED"  # uploaded file wasn't a real image of the declared type


@dataclass
class Image:
    image_id: str                          # UUID4, also the table's partition key
    user_id: str                           # owner
    title: str
    content_type: str                      # e.g. image/png
    s3_key: str                            # images/<image_id>.<ext>
    status: str                            # see ImageStatus
    created_at: str                        
    description: str = ""
    tags: List[str] = field(default_factory=list)
    size_bytes: Optional[int] = None       # known once the upload completes
    uploaded_at: Optional[str] = None
    rejection_reason: Optional[str] = None
    expires_at: Optional[int] = None       # DynamoDB TTL (epoch seconds); only set while not ACTIVE

    def to_item(self) -> Dict[str, Any]:
        """DynamoDB item. Empty/None attributes are simply left out."""
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [])}

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "Image":
        known = {f.name for f in fields(cls)}
        data = {key: value for key, value in item.items() if key in known}
        for number in ("size_bytes", "expires_at"):   # DynamoDB returns numbers as Decimal
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
