import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from app.scripts.cleanup_orphan_chunks import cleanup, find_corpus


@patch("app.scripts.cleanup_orphan_chunks.print")
class CleanupTests(unittest.TestCase):
    def client(self):
        client = Mock()
        client.query.return_value = {"Items": [{
            "corpus_id": {"S": "c"}, "chunk_id": {"S": "chunk"},
            "document_id": {"S": "derived"}, "external_id": {"S": "upload"},
        }]}
        client.get_item.return_value = {}
        return client

    def run_cleanup(self, client, apply=False):
        return cleanup(client, corpus_id="c", chunks_table="chunks", status_table="status", apply=apply)

    def test_preview_never_deletes(self, output):
        client = self.client()
        self.assertEqual(1, self.run_cleanup(client)["orphan_chunks"])
        client.transact_write_items.assert_not_called()

    def test_existing_upload_is_protected_regardless_of_status(self, output):
        client = self.client()
        client.get_item.return_value = {"Item": {"status": {"S": "failed"}}}
        self.assertEqual(0, self.run_cleanup(client, True)["deleted_chunks"])
        client.transact_write_items.assert_not_called()

    def test_apply_checks_status_and_chunk_identity_atomically(self, output):
        client = self.client()
        self.assertEqual(1, self.run_cleanup(client, True)["deleted_chunks"])
        actions = client.transact_write_items.call_args.kwargs["TransactItems"]
        self.assertEqual({"S": "upload"}, actions[0]["ConditionCheck"]["Key"]["document_id"])
        self.assertIn("#source = :upload", actions[1]["Delete"]["ConditionExpression"])

    def test_race_is_protected(self, output):
        client = self.client()
        client.transact_write_items.side_effect = ClientError({
            "Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [{"Code": "ConditionalCheckFailed"}, {"Code": "None"}],
        }, "TransactWriteItems")
        result = self.run_cleanup(client, True)
        self.assertEqual(0, result["deleted_chunks"])
        self.assertEqual(1, result["protected_chunks"])

    def test_empty_filtered_page_still_paginates(self, output):
        client = self.client()
        page = client.query.return_value
        client.query.side_effect = [{"Items": [], "LastEvaluatedKey": {"chunk_id": {"S": "previous"}}}, page]
        self.assertEqual(1, self.run_cleanup(client)["orphan_chunks"])
        self.assertEqual(2, client.query.call_count)
        self.assertEqual("#source = :upload", client.query.call_args.kwargs["FilterExpression"])

    def test_name_lookup_requires_unique_match_across_pages(self, output):
        client = Mock()
        client.scan.side_effect = [
            {"Items": [], "LastEvaluatedKey": {"corpus_id": {"S": "previous"}}},
            {"Items": [{"corpus_id": {"S": "c"}}]},
        ]
        self.assertEqual("c", find_corpus(client, "corpora", "cfuf"))
        client.scan.side_effect = None
        client.scan.return_value = {"Items": [{"corpus_id": {"S": "a"}}, {"corpus_id": {"S": "b"}}]}
        with self.assertRaises(ValueError):
            find_corpus(client, "corpora", "cfuf")
