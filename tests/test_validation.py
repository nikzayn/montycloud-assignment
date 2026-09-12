"""Pure input-validation rules (no AWS involved)."""
import pytest

from image_service.errors import ValidationError
from image_service.validation import (
    DEFAULT_PAGE_SIZE,
    detect_image_type,
    is_valid_image_id,
    normalize_tag,
    parse_limit,
    parse_new_image_request,
    validate_user_id,
)
from tests.helpers import JPEG_BYTES, NOT_AN_IMAGE, PNG_BYTES

VALID = {"title": "ok", "content_type": "image/png"}


class TestNewImageRequest:
    def test_valid_request_is_normalised(self):
        request = parse_new_image_request({
            "title": "  Sunset  ",
            "content_type": "image/jpeg",
            "description": "  Goa, 2026 ",
            "tags": ["#Beach", "beach", "Travel_2026"],
        })
        assert request.title == "Sunset"
        assert request.content_type == "image/jpeg"
        assert request.description == "Goa, 2026"
        assert request.tags == ["beach", "travel_2026"]  # lowercased, '#' stripped, de-duplicated

    def test_optional_fields_default_to_empty(self):
        request = parse_new_image_request(VALID)
        assert request.description == ""
        assert request.tags == []

    @pytest.mark.parametrize("body, message", [
        ([], "JSON object"),
        ("hello", "JSON object"),
        ({"content_type": "image/png"}, "'title' is required"),
        ({"title": "   ", "content_type": "image/png"}, "'title' is required"),
        ({"title": 5, "content_type": "image/png"}, "'title' is required"),
        ({"title": "x" * 101, "content_type": "image/png"}, "at most 100"),
        ({"title": "ok"}, "content_type"),
        ({"title": "ok", "content_type": "application/pdf"}, "content_type"),
        ({"title": "ok", "content_type": ["image/png"]}, "content_type"),
        ({**VALID, "description": 42}, "description"),
        ({**VALID, "description": "x" * 1001}, "description"),
        ({**VALID, "tags": "beach"}, "list"),
        ({**VALID, "tags": [1]}, "strings"),
        ({**VALID, "tags": ["has space"]}, "Invalid tag"),
        ({**VALID, "tags": ["#"]}, "Invalid tag"),
        ({**VALID, "tags": [f"t{i}" for i in range(11)]}, "At most 10"),
        ({**VALID, "user_id": "bob"}, "Unknown field"),
    ])
    def test_invalid_requests_are_rejected(self, body, message):
        with pytest.raises(ValidationError, match=message):
            parse_new_image_request(body)


def test_normalize_tag_accepts_hashtag_style():
    assert normalize_tag(" #Sunset ") == "sunset"


class TestParseLimit:
    def test_defaults_when_missing(self):
        assert parse_limit(None) == DEFAULT_PAGE_SIZE

    @pytest.mark.parametrize("raw, expected", [("1", 1), ("50", 50), ("100", 100)])
    def test_accepts_valid_values(self, raw, expected):
        assert parse_limit(raw) == expected

    @pytest.mark.parametrize("raw", ["0", "101", "-5", "ten", "2.5"])
    def test_rejects_invalid_values(self, raw):
        with pytest.raises(ValidationError):
            parse_limit(raw)


class TestIdentifiers:
    @pytest.mark.parametrize("user_id", ["alice", "user_42", "3f1c2b9a-0c1d-4e5f-8a9b-0c1d2e3f4a5b"])
    def test_valid_user_ids(self, user_id):
        assert validate_user_id(user_id) == user_id

    @pytest.mark.parametrize("user_id", ["", "a b", "x" * 65, "alice\n", "../etc"])
    def test_invalid_user_ids(self, user_id):
        with pytest.raises(ValidationError):
            validate_user_id(user_id)

    def test_image_id_must_be_a_lowercase_uuid(self):
        assert is_valid_image_id("3f1c2b9a-0c1d-4e5f-8a9b-0c1d2e3f4a5b")
        assert not is_valid_image_id("not-a-uuid")
        assert not is_valid_image_id("")
        assert not is_valid_image_id(None)


class TestDetectImageType:
    @pytest.mark.parametrize("header, expected", [
        (JPEG_BYTES, "image/jpeg"),
        (PNG_BYTES, "image/png"),
        (b"GIF87a" + b"\x00" * 10, "image/gif"),
        (b"GIF89a" + b"\x00" * 10, "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", None),   # RIFF, but audio
        (NOT_AN_IMAGE, None),
        (b"", None),
    ])
    def test_detects_formats_from_magic_bytes(self, header, expected):
        assert detect_image_type(header) == expected
