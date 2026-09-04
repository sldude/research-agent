"""Create reusable DynamoDB clients from environment configuration."""

import os
from functools import lru_cache
from typing import Any

import boto3
from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
AWS_PROFILE = os.getenv("AWS_PROFILE")
DYNAMODB_CORPORA_TABLE = os.getenv("DYNAMODB_CORPORA_TABLE", "research-agent-corpora")
DYNAMODB_CHUNKS_TABLE = os.getenv("DYNAMODB_CHUNKS_TABLE", "research-agent-chunks")
DYNAMODB_VECTOR_INDEX = os.getenv("DYNAMODB_VECTOR_INDEX", "embedding-index")


@lru_cache(maxsize=1)
def create_dynamodb_client() -> Any:
    """Return one low-level DynamoDB client for the current process."""

    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    return session.client("dynamodb")


def test_connection() -> None:
    """Confirm that both configured DynamoDB tables can be described."""

    client = create_dynamodb_client()
    client.describe_table(TableName=DYNAMODB_CORPORA_TABLE)
    client.describe_table(TableName=DYNAMODB_CHUNKS_TABLE)


if __name__ == "__main__":
    test_connection()
    print("DynamoDB connection successful!")
