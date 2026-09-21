"""Offline tests for DynamoDB request shapes and record conversion."""

import unittest
from datetime import date
from unittest.mock import Mock, patch

import boto3

from app.database.repository import DynamoRepository
from app.scripts.create_dynamodb_tables import (
    chunks_table_request,
    corpora_table_request,
    document_status_table_request,
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
            categories=["cs.AI", "cs.IR"],
            updated_date=date(2026, 2, 1),
        )
        ranked = repository.search_documents(
            corpus_id="corpus-1",
            embedding=[0.1, 0.2],
            embedding_model="test-model",
            limit=1,
        )
        self.assertEqual(saved, ranked[0][0])
        self.assertEqual(saved.categories, ["cs.AI", "cs.IR"])
        self.assertEqual(saved.updated_date, date(2026, 2, 1))
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
        client._serializer.serialize_to_request(document_status_table_request(), operation)


    def test_list_document_statuses_reads_all_pages(self) -> None:
        client = Mock()
        client.query.side_effect = [
            {
                "Items": [{
                    "document_id": {"S": "doc-1"},
                    "filename": {"S": "first.txt"},
                    "status": {"S": "ready"},
                    "created_at": {"S": "2026-09-21T10:00:00+00:00"},
                    "chunks_saved": {"N": "2"},
                }],
                "LastEvaluatedKey": {"corpus_id": {"S": "corpus-1"},
                                    "document_id": {"S": "doc-1"}},
            },
            {
                "Items": [{
                    "document_id": {"S": "doc-2"},
                    "filename": {"S": "second.pdf"},
                    "status": {"S": "processing"},
                    "created_at": {"S": "2026-09-21T11:00:00+00:00"},
                }],
            },
        ]

        documents = DynamoRepository(client=client).list_document_statuses("corpus-1")

        self.assertEqual(["doc-1", "doc-2"], [doc["document_id"] for doc in documents])
        self.assertEqual(2, documents[0]["chunks_saved"])
        self.assertIsNone(documents[1]["chunks_saved"])
        self.assertEqual(2, client.query.call_count)
        self.assertEqual(
            {"corpus_id": {"S": "corpus-1"}, "document_id": {"S": "doc-1"}},
            client.query.call_args_list[1].kwargs["ExclusiveStartKey"],
        )

if __name__ == "__main__":
    unittest.main()
