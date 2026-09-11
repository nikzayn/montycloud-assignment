from dataclasses import asdict, dataclass, field, fields
from tkinter import ACTIVE
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




