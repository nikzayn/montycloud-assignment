import base64
import json
import os
from decimal import Decimal
from typing import Any, Dict, Optional

from .errors import ApiError, BadRequest, Unauthorized
from .validation import validate_user_id

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,X-User-Id",
    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
}

# --- responses ----
def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")

def json_response(status_code: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", **CORS_HEADERS},
        "body": json.dumps(body, default=_json_default),
    }

def no_content() -> Dict[str, Any]:
    return {"statusCode": 204, "headers": dict(CORS_HEADERS), "body": ""}


def redirect(location: str) -> Dict[str, Any]:
    return {"statusCode": 302, "headers": {"Location": location, **CORS_HEADERS}, "body": ""}


def error_response(error: ApiError) -> Dict[str, Any]:
    return json_response(error.status_code, {"error": {"code": error.code, "message": error.message}})


# ---- requests ----------------------------------------------------------------------
def json_body(event: Dict[str, Any]) -> Any:
    raw = event.get("body")
    if not raw:
        raise BadRequest("Request body is required")
    try:
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        return json.loads(raw)
    except ValueError:
        raise BadRequest("Request body must be valid JSON")

def query_param(event: Dict[str, Any], name: str) -> Optional[str]:
    return (event.get("queryStringParameters") or {}).get(name)

def path_param(event: Dict[str, Any], name: str) -> str:
    return (event.get("pathParameters") or {}).get(name) or ""

def header(event: Dict[str, Any], name: str) -> Optional[str]:
    for key, value in (event.get("headers") or {}).items():  # headers are case-insensitive
        if key.lower() == name.lower():
            return value
    return None

def caller_id(event: Dict[str, Any]) -> str:
    """Who is making this request?

    Production: a Cognito authorizer on API Gateway validates the JWT and passes the
    user's `sub` claim here. Local development: the `X-User-Id` header stands in for
    it, but only when ALLOW_HEADER_AUTH=true (it's trivially spoofable, so it must
    never be enabled in a real deployment).
    """
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    user_id = claims.get("sub")
    if not user_id and os.environ.get("ALLOW_HEADER_AUTH", "").lower() == "true":
        user_id = header(event, "X-User-Id")
    if not user_id:
        raise Unauthorized("Missing caller identity (locally: send the X-User-Id header)")
    return validate_user_id(user_id)