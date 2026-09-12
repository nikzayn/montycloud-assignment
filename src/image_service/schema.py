"""DynamoDB table schema"""

from typing import Any, Dict

STATUS_INDEX = "status-created_at-index"
USER_INDEX = "user_id-created_at-index"
TTL_ATTRIBUTE = "expires_at"


def _index(name: str, partition_key: str) -> Dict[str, Any]:
    return {
        "IndexName": name,
        "KeySchema": [
            {"AttributeName": partition_key, "KeyType": "HASH"},
            {"AttributeName": "created_at", "KeyType": "RANGE"},
        ],
        "Projection": {"ProjectionType": "ALL"},
    }


def table_definition(table_name: str) -> Dict[str, Any]:
    """Keyword arguments for DynamoDB `create_table`."""
    return {
        "TableName": table_name,
        "BillingMode": "PAY_PER_REQUEST",
        "KeySchema": [{"AttributeName": "image_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": name, "AttributeType": "S"}
            for name in ("image_id", "status", "user_id", "created_at")
        ],
        "GlobalSecondaryIndexes": [
            _index(STATUS_INDEX, "status"),
            _index(USER_INDEX, "user_id"),
        ],
    }
