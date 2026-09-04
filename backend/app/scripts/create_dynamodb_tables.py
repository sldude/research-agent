"""Provision the two on-demand DynamoDB tables used by the application.

Running this module creates billable AWS resources. It never deletes or
replaces an existing table.
"""

import time
from typing import Any

from botocore.exceptions import ClientError

from app.clients.embeddings import DIMENSIONS
from app.database.database_connect import (
    DYNAMODB_CHUNKS_TABLE,
    DYNAMODB_CORPORA_TABLE,
    DYNAMODB_VECTOR_INDEX,
    create_dynamodb_client,
)


def _create_if_missing(client: Any, *, table_name: str, request: dict) -> None:
    try:
        client.describe_table(TableName=table_name)
        print(f"Table already exists: {table_name}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
        client.create_table(**request)
        print(f"Creating table: {table_name}")


def _wait_for_vector_index(client: Any) -> None:
    while True:
        table = client.describe_table(TableName=DYNAMODB_CHUNKS_TABLE)["Table"]
        index = next(
            (
                value
                for value in table.get("VectorIndexes", [])
                if value["IndexName"] == DYNAMODB_VECTOR_INDEX
            ),
            None,
        )
        if (
            table["TableStatus"] == "ACTIVE"
            and index is not None
            and index["IndexStatus"] == "ACTIVE"
            and not index.get("Backfilling", False)
        ):
            return
        print("Waiting for the chunks table and vector index...")
        time.sleep(5)


def corpora_table_request() -> dict[str, Any]:
    """Return the CreateTable request for corpus metadata."""

    return {
        "TableName": DYNAMODB_CORPORA_TABLE,
        "AttributeDefinitions": [
            {"AttributeName": "corpus_id", "AttributeType": "S"}
        ],
        "KeySchema": [{"AttributeName": "corpus_id", "KeyType": "HASH"}],
        "BillingMode": "PAY_PER_REQUEST",
        "DeletionProtectionEnabled": True,
        "Tags": [{"Key": "Application", "Value": "research-agent"}],
    }


def chunks_table_request() -> dict[str, Any]:
    """Return the CreateTable request containing the cosine vector index."""

    return {
        "TableName": DYNAMODB_CHUNKS_TABLE,
        "AttributeDefinitions": [
            {"AttributeName": "corpus_id", "AttributeType": "S"},
            {"AttributeName": "chunk_id", "AttributeType": "S"},
            {"AttributeName": "embedding_model", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "corpus_id", "KeyType": "HASH"},
            {"AttributeName": "chunk_id", "KeyType": "RANGE"},
        ],
        "VectorIndexes": [
            {
                "IndexName": DYNAMODB_VECTOR_INDEX,
                "VectorAttribute": {"AttributeName": "embedding"},
                "Dimensions": DIMENSIONS,
                "DistanceFunction": "COSINE",
                "Projection": {"ProjectionType": "KEYS_ONLY"},
                "SearchSchema": [
                    {
                        "AttributeName": "corpus_id",
                        "SearchSchemaElementType": "HASH",
                    },
                    {
                        "AttributeName": "embedding_model",
                        "SearchSchemaElementType": "INLINE_FILTER",
                    },
                ],
            }
        ],
        "BillingMode": "PAY_PER_REQUEST",
        "DeletionProtectionEnabled": True,
        "Tags": [{"Key": "Application", "Value": "research-agent"}],
    }


def create_dynamodb_tables() -> None:
    """Create on-demand corpora and chunk tables if they do not exist."""

    client = create_dynamodb_client()
    _create_if_missing(
        client,
        table_name=DYNAMODB_CORPORA_TABLE,
        request=corpora_table_request(),
    )
    _create_if_missing(
        client,
        table_name=DYNAMODB_CHUNKS_TABLE,
        request=chunks_table_request(),
    )
    _wait_for_vector_index(client)
    print("DynamoDB tables and vector index are ready.")


if __name__ == "__main__":
    create_dynamodb_tables()
