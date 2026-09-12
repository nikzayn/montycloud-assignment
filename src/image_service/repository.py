"""DynamoDB"""

import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from .errors import BadRequest
from .models import Image, ImageStatus
from .schema import STATUS_INDEX, USER_INDEX

# Capping a single API request for DynamoDB
MAX_READS_PER_PAGE = 10


class ImageRepository:
    def __init__(self, table: Any) -> None:
        self._table = table

    # ---- single-item operations -------------------------------------------------
    def put(self, image: Image) -> None:
        self._table.put_item(
            Item=image.to_item(),
            ConditionExpression="attribute_not_exists(image_id)",  # never overwrite
        )

    def get(self, image_id: str) -> Optional[Image]:
        item = self._table.get_item(Key={"image_id": image_id}, ConsistentRead=True).get("Item")
        return Image.from_item(item) if item else None

    def mark_active(self, image_id: str, size_bytes: int, uploaded_at: str) -> bool:
        """PENDING -> ACTIVE. Returns False if the record no longer exists."""
        return self._update_if_exists(
            image_id,
            "SET #status = :status, size_bytes = :size, uploaded_at = :at "
            "REMOVE expires_at, rejection_reason",
            {":status": ImageStatus.ACTIVE, ":size": size_bytes, ":at": uploaded_at},
        )

    def mark_rejected(self, image_id: str, reason: str, expires_at: int) -> bool:
        return self._update_if_exists(
            image_id,
            "SET #status = :status, rejection_reason = :reason, expires_at = :exp",
            {":status": ImageStatus.REJECTED, ":reason": reason, ":exp": expires_at},
        )

    def delete(self, image_id: str) -> None:
        self._table.delete_item(Key={"image_id": image_id})

    def _update_if_exists(self, image_id: str, expression: str, values: Dict[str, Any]) -> bool:
        try:
            self._table.update_item(
                Key={"image_id": image_id},
                UpdateExpression=expression,
                ConditionExpression="attribute_exists(image_id)",
                ExpressionAttributeNames={"#status": "status"},  # "status" is a reserved word
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    # ---- listing ----------------------------------------------------------------
    def list_active(
        self,
        user_id: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 20,
        next_token: Optional[str] = None,
    ) -> Tuple[List[Image], Optional[str]]:
        """ACTIVE images, newest first, optionally filtered by owner and/or tag.

        Every path is a Query on an index (never a full-table Scan):
          * no user_id -> status index,  partition "ACTIVE"
          * user_id    -> user index,    partition <user_id>, filter status = ACTIVE
          * tag        -> applied as a FilterExpression on top of either query
        """
        if user_id:
            query = {"IndexName": USER_INDEX, "KeyConditionExpression": Key("user_id").eq(user_id)}
            key_attrs = ("image_id", "user_id", "created_at")
            filters = [Attr("status").eq(ImageStatus.ACTIVE)]
        else:
            query = {"IndexName": STATUS_INDEX, "KeyConditionExpression": Key("status").eq(ImageStatus.ACTIVE)}
            key_attrs = ("image_id", "status", "created_at")
            filters = []
        if tag:
            filters.append(Attr("tags").contains(tag))
        if filters:
            condition = filters[0]
            for extra in filters[1:]:
                condition = condition & extra
            query["FilterExpression"] = condition
        query["ScanIndexForward"] = False  # newest first
        if next_token:
            query["ExclusiveStartKey"] = _decode_token(next_token, key_attrs)

        items, last_key = self._read_page(query, limit, key_attrs)
        return [Image.from_item(item) for item in items], _encode_token(last_key) if last_key else None

    def _read_page(self, query: Dict[str, Any], limit: int, key_attrs: Tuple[str, ...]):
        """Collect up to `limit` matching items.

        DynamoDB applies `Limit` *before* the FilterExpression, so one read can return
        fewer matches than asked for. We keep reading until the page is full, and hand
        back the key of the last item we actually returned as the resume point.
        """
        items = []  # type: List[Dict[str, Any]]
        last_key = None
        for _ in range(MAX_READS_PER_PAGE):
            response = self._table.query(Limit=limit, **query)
            batch = response.get("Items", [])
            last_key = response.get("LastEvaluatedKey")
            for position, item in enumerate(batch):
                items.append(item)
                if len(items) == limit:
                    more_after_this = position < len(batch) - 1 or last_key is not None
                    return items, ({k: item[k] for k in key_attrs} if more_after_this else None)
            if last_key is None:
                return items, None
            query["ExclusiveStartKey"] = last_key
        return items, last_key  # read budget used up; client continues from here


def _encode_token(key: Dict[str, Any]) -> str:
    """Opaque, URL-safe pagination token wrapping DynamoDB's LastEvaluatedKey."""
    raw = json.dumps(key, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_token(token: str, key_attrs: Tuple[str, ...]) -> Dict[str, Any]:
    try:
        padded = token + "=" * (-len(token) % 4)
        key = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except ValueError:  # covers bad base64, bad UTF-8 and bad JSON
        raise BadRequest("Invalid 'next_token'")
    valid = (
        isinstance(key, dict)
        and set(key) == set(key_attrs)
        and all(isinstance(value, str) for value in key.values())
    )
    if not valid:  # e.g. a token from a different filter combination
        raise BadRequest("Invalid 'next_token' for this query")
    return key
