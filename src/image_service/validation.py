import re
from dataclasses import dataclass
from typing import Any, List, Optional

from .errors import ValidationError
from .models import CONTENT_TYPE_EXTENSIONS

TITLE_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 1000
MAX_TAGS = 10
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

_TAG_RE = re.compile(r"[a-z0-9_]{1,30}")
_USER_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_IMAGE_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CREATE_FIELDS = {"title", "description", "tags", "content_type"}


@dataclass(frozen=True)
class NewImageRequest:
    title: str
    content_type: str
    description: str
    tags: List[str]


def parse_new_image_request(body: Any) -> NewImageRequest:
    """Validate the POST /images body and return a clean, normalised request."""
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object")

    unknown = sorted(set(body) - _CREATE_FIELDS)
    if unknown:
        raise ValidationError("Unknown field(s): " + ", ".join(unknown))

    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValidationError("'title' is required")
    title = title.strip()
    if len(title) > TITLE_MAX_LENGTH:
        raise ValidationError(f"'title' must be at most {TITLE_MAX_LENGTH} characters")

    description = body.get("description", "")
    if not isinstance(description, str) or len(description) > DESCRIPTION_MAX_LENGTH:
        raise ValidationError(
            f"'description' must be a string of at most {DESCRIPTION_MAX_LENGTH} characters"
        )

    content_type = body.get("content_type")
    if not isinstance(content_type, str) or content_type not in CONTENT_TYPE_EXTENSIONS:
        raise ValidationError("'content_type' must be one of: " + ", ".join(CONTENT_TYPE_EXTENSIONS))

    return NewImageRequest(
        title=title,
        content_type=content_type,
        description=description.strip(),
        tags=normalize_tags(body.get("tags", [])),
    )


def normalize_tag(raw: Any) -> str:
    """'#Sunset ' -> 'sunset'. Tags are case-insensitive, so they're stored lowercase."""
    if not isinstance(raw, str):
        raise ValidationError("Tags must be strings")
    tag = raw.strip().lstrip("#").lower()
    if not _TAG_RE.fullmatch(tag):
        raise ValidationError(f"Invalid tag '{raw}': use 1-30 letters, digits or underscores")
    return tag


def normalize_tags(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        raise ValidationError("'tags' must be a list of strings")
    tags = []  # type: List[str]
    for item in raw:
        tag = normalize_tag(item)
        if tag not in tags:          # de-duplicate, keep the client's order
            tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise ValidationError(f"At most {MAX_TAGS} tags are allowed")
    return tags


def parse_limit(raw: Optional[str]) -> int:
    if raw is None:
        return DEFAULT_PAGE_SIZE
    try:
        limit = int(raw)
    except ValueError:
        raise ValidationError("'limit' must be an integer")
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValidationError(f"'limit' must be between 1 and {MAX_PAGE_SIZE}")
    return limit


def validate_user_id(raw: str) -> str:
    if not isinstance(raw, str) or not _USER_ID_RE.fullmatch(raw):
        raise ValidationError("Invalid user id: use 1-64 letters, digits, '-' or '_'")
    return raw


def is_valid_image_id(raw: Optional[str]) -> bool:
    return bool(raw) and bool(_IMAGE_ID_RE.fullmatch(raw))


def detect_image_type(header: bytes) -> Optional[str]:
    """Identify an image from its first bytes ("magic numbers").

    The Content-Type a client declares can't be trusted on its own, so after the
    upload we peek at the real file content before marking the image ACTIVE.
    """
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    return None
