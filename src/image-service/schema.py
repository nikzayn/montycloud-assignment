from typing import Any, dict

STATUS_INDEX = "staus-created-at-index"
USER_INDEX = "user_id-created-at-index"
TTL_ATTRIBUTE = "expires_at"

def _index(name: str, partition_key: str) -> dict[str, Any]:
    return {
        "IndexName": name,
        "KeySchema": [
            {"AttributeName": partition_key, "KeyType": "HASH"},
            {"AttributeName": "created_at", "KeyType": "RANGE"},
        ],
        "Projection": {"ProjectionType": "ALL"},
    }


def table_definition(table_name: str) -> dict[str, Any]:
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