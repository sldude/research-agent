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
    def test_count_uploaded_documents_paginates_status_records(self):
        client = Mock()
        last_key = {"corpus_id": {"S": "c"}, "document_id": {"S": "a"}}
        client.query.side_effect = [
            {"Count": 2, "LastEvaluatedKey": last_key}, {"Count": 1},
        ]
        self.assertEqual(3, DynamoRepository(client).count_documents("c", "user_upload"))
        first, second = client.query.call_args_list
        self.assertEqual("COUNT", first.kwargs["Select"])
        self.assertNotIn("FilterExpression", first.kwargs)
        self.assertEqual(last_key, second.kwargs["ExclusiveStartKey"])

    def test_count_papers_deduplicates_chunks_across_pages(self):
        client = Mock()
        last_key = {"corpus_id": {"S": "c"}, "chunk_id": {"S": "a#chunk:1"}}
        client.query.side_effect = [
            {"Items": [{"document_id": {"S": "a"}}, {"document_id": {"S": "a"}}],
             "LastEvaluatedKey": last_key},
            {"Items": [{"document_id": {"S": "a"}}, {"document_id": {"S": "b"}}]},
        ]
        self.assertEqual(2, DynamoRepository(client).count_documents("c", "research_abstract"))
        first, second = client.query.call_args_list
        self.assertEqual("document_id", first.kwargs["ProjectionExpression"])
        self.assertEqual({":corpus_id": {"S": "c"}}, first.kwargs["ExpressionAttributeValues"])
        self.assertEqual(last_key, second.kwargs["ExclusiveStartKey"])

    def test_empty_corpus_count(self):
        for corpus_type in ("user_upload", "research_abstract"):
            client = Mock()
            client.query.return_value = {"Items": [], "Count": 0}
            self.assertEqual(0, DynamoRepository(client).count_documents("c", corpus_type))

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

    def test_delete_document_removes_all_matching_chunks_and_status(self) -> None:
        client = Mock()
        client.batch_write_item.return_value = {}
        client.query.side_effect = [
            {
                "Items": [{"corpus_id": {"S": "corpus-1"}, "chunk_id": {"S": "a"}}],
                "LastEvaluatedKey": {"corpus_id": {"S": "corpus-1"}, "chunk_id": {"S": "a"}},
            },
            {"Items": [{"corpus_id": {"S": "corpus-1"}, "chunk_id": {"S": "b"}}]},
        ]

        DynamoRepository(client=client).delete_document(
            corpus_id="corpus-1", document_id="doc-1"
        )

        self.assertEqual(2, client.query.call_count)
        query = client.query.call_args_list[0].kwargs
        expected_id = DynamoRepository.document_id("upload", "doc-1")
        self.assertEqual({"S": expected_id + "#chunk:"}, query["ExpressionAttributeValues"][":prefix"])
        self.assertTrue(query["ConsistentRead"])
        requests = client.batch_write_item.call_args.kwargs["RequestItems"]
        self.assertEqual(2, len(next(iter(requests.values()))))
        client.delete_item.assert_called_once()

    @patch("app.database.repository.time.sleep")
    def test_delete_retries_unprocessed_chunks_before_removing_status(self, sleep):
        client = Mock()
        client.query.return_value = {"Items": [
            {"corpus_id": {"S": "c"}, "chunk_id": {"S": "chunk"}}
        ]}
        pending = {"chunks": [{"DeleteRequest": {"Key": {"chunk_id": {"S": "chunk"}}}}]}
        client.batch_write_item.side_effect = [{"UnprocessedItems": pending}, {}]
        DynamoRepository(client).delete_document(corpus_id="c", document_id="upload")
        self.assertEqual(pending, client.batch_write_item.call_args.kwargs["RequestItems"])
        client.delete_item.assert_called_once()

    @patch("app.database.repository.time.sleep")
    def test_failed_chunk_deletion_preserves_status_for_retry(self, sleep):
        client = Mock()
        client.query.return_value = {"Items": [
            {"corpus_id": {"S": "c"}, "chunk_id": {"S": "chunk"}}
        ]}
        client.batch_write_item.return_value = {"UnprocessedItems": {"chunks": [{}]}}
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            DynamoRepository(client).delete_document(corpus_id="c", document_id="upload")
        client.delete_item.assert_not_called()

    def test_search_excludes_deleted_and_unfinished_uploads(self):
        from types import SimpleNamespace
        repository = DynamoRepository(Mock())
        repository.get_document_status = Mock(side_effect=[None, {"status": "processing"}, {"status": "ready"}])
        records = [SimpleNamespace(source="upload", external_id=value) for value in
                   ("deleted", "deleted", "pending", "ready")]
        arxiv = SimpleNamespace(source="arxiv")
        visible = repository._searchable_documents(records + [arxiv], "corpus")
        self.assertEqual([records[-1], arxiv], visible)
        self.assertEqual(3, repository.get_document_status.call_count)

    def test_orphan_chunk_returned_by_vector_index_is_not_returned_to_rag(self):
        client = FakeDynamoClient()
        repository = DynamoRepository(client)
        repository.put_document(
            corpus_id="c", source="upload", external_id="original-upload-id",
            title="Deleted file", abstract=None, authors=[], publication_date=None,
            source_url=None, license_url=None, content="Old searchable text",
            embedding=[0.1, 0.2], embedding_model="test-model",
        )
        client.get_item = Mock(return_value={})
        results = repository.search_documents(
            corpus_id="c", embedding=[0.1, 0.2], embedding_model="test-model", limit=5,
        )
        self.assertEqual([], results)
        self.assertEqual({"S": "original-upload-id"},
                         client.get_item.call_args.kwargs["Key"]["document_id"])
        self.assertTrue(client.get_item.call_args.kwargs["ConsistentRead"])

if __name__ == "__main__":
    unittest.main()
