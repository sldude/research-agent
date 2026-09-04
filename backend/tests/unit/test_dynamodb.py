"""Offline tests for DynamoDB request shapes and record conversion."""

import unittest
from datetime import date
from unittest.mock import patch

import boto3

from app.database.repository import DynamoRepository
from app.scripts.create_dynamodb_tables import (
    chunks_table_request,
    corpora_table_request,
)


class FakeDynamoClient:
    def __init__(self) -> None:
        self.item = None

    def put_item(self, **request):
        self.item = request["Item"]
        return {"ConsumedCapacity": {}}

    def search_vectors(self, **request):
        return {
            "SearchResults": [
                {
                    "Item": {
                        "corpus_id": self.item["corpus_id"],
                        "chunk_id": self.item["chunk_id"],
                    },
                    "Score": 0.125,
                }
            ]
        }

    def batch_get_item(self, **request):
        table_name = next(iter(request["RequestItems"]))
        return {"Responses": {table_name: [self.item]}}


class DynamoRepositoryTests(unittest.TestCase):
    def test_document_round_trip_and_rank(self) -> None:
        client = FakeDynamoClient()
        repository = DynamoRepository(client=client)
        saved = repository.put_document(
            corpus_id="corpus-1",
            source="arxiv",
            external_id="1234.5678",
            title="Example",
            abstract="Example abstract",
            authors=["A. Author"],
            publication_date=date(2026, 1, 2),
            source_url="https://arxiv.org/abs/1234.5678",
            license_url=None,
            content="Example\n\nExample abstract",
            embedding=[0.1, 0.2],
            embedding_model="test-model",
        )
        ranked = repository.search_documents(
            corpus_id="corpus-1",
            embedding=[0.1, 0.2],
            embedding_model="test-model",
            limit=1,
        )
        self.assertEqual(saved, ranked[0][0])
        self.assertEqual(0.125, ranked[0][1])

    def test_create_table_requests_match_current_sdk_model(self) -> None:
        # Ignore the developer's AWS_PROFILE so this remains credential-free.
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("AWS_PROFILE", None)
            client = boto3.Session(
                aws_access_key_id="offline-test",
                aws_secret_access_key="offline-test",
                region_name="us-east-2",
            ).client("dynamodb")
        operation = client.meta.service_model.operation_model("CreateTable")
        client._serializer.serialize_to_request(corpora_table_request(), operation)
        client._serializer.serialize_to_request(chunks_table_request(), operation)


if __name__ == "__main__":
    unittest.main()
