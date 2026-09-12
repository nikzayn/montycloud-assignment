"""AWS Lambda entry points."""

import logging
from functools import lru_cache, wraps
from typing import Any, Callable, Dict
from urllib.parse import unquote_plus

from . import apigw
from .config import Settings
from .errors import ApiError
from .service import ImageService, build_service
from .validation import normalize_tag, parse_limit, parse_new_image_request, validate_user_id

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

Event = Dict[str, Any]


@lru_cache(maxsize=1)
def get_service() -> ImageService:
    """Built once per Lambda container and reused by every warm invocation."""
    return build_service(Settings.from_env())


def api_handler(func: Callable[[Event, Any], Dict[str, Any]]) -> Callable[[Event, Any], Dict[str, Any]]:
    @wraps(func)
    def wrapper(event: Event, context: Any) -> Dict[str, Any]:
        try:
            return func(event, context)
        except ApiError as error:
            return apigw.error_response(error)
        except Exception:  # last line of defence
            logger.exception("Unhandled error in %s", func.__name__)
            return apigw.error_response(ApiError("Internal server error"))
    return wrapper


@api_handler
def create_image(event: Event, context: Any) -> Dict[str, Any]:
    user_id = apigw.caller_id(event)
    request = parse_new_image_request(apigw.json_body(event))
    service = get_service()
    image, upload_form = service.create_image(user_id, request)
    return apigw.json_response(201, {"image": service.present(image), "upload": upload_form})


@api_handler
def list_images(event: Event, context: Any) -> Dict[str, Any]:
    user_id = apigw.query_param(event, "user_id")
    tag = apigw.query_param(event, "tag")
    service = get_service()
    images, next_token = service.list_images(
        user_id=validate_user_id(user_id) if user_id else None,
        tag=normalize_tag(tag) if tag else None,
        limit=parse_limit(apigw.query_param(event, "limit")),
        next_token=apigw.query_param(event, "next_token"),
    )
    return apigw.json_response(200, {
        "items": [service.present(image) for image in images],
        "count": len(images),
        "next_token": next_token,
    })


@api_handler
def get_image(event: Event, context: Any) -> Dict[str, Any]:
    service = get_service()
    image = service.get_image(apigw.path_param(event, "image_id"))
    return apigw.json_response(200, {"image": service.present(image)})


@api_handler
def download_image(event: Event, context: Any) -> Dict[str, Any]:
    return apigw.redirect(get_service().download_url(apigw.path_param(event, "image_id")))


@api_handler
def delete_image(event: Event, context: Any) -> Dict[str, Any]:
    caller = apigw.caller_id(event)
    get_service().delete_image(caller, apigw.path_param(event, "image_id"))
    return apigw.no_content()


def on_upload_complete(event: Event, context: Any) -> Dict[str, Any]:
    """S3 trigger. Exceptions propagate on purpose so Lambda retries the event."""
    service = get_service()
    results = []
    for record in event.get("Records", []):
        s3_object = record["s3"]["object"]
        key = unquote_plus(s3_object["key"])  # keys arrive URL-encoded in S3 events
        outcome = service.complete_upload(key, s3_object.get("size"))
        logger.info("Processed upload key=%s outcome=%s", key, outcome)
        results.append({"key": key, "outcome": outcome})
    return {"processed": results}
