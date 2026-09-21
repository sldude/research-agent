"""Offline worker tests; no S3, DynamoDB, or Bedrock requests are sent."""

import os
import unittest
from io import BytesIO
from unittest.mock import Mock, patch

from app.workers.document_ingestion_worker import handler


class DocumentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.repository = Mock()
        self.repository.get_document_status.return_value = {
            "owner_id": "user", "s3_bucket": "uploads-test",
            "s3_key": "uploads/user/corpus/doc.txt",
            "filename": "notes.txt", "status": "uploaded",
        }
        self.repository.claim_document_processing.return_value = "claimed"
        self.s3 = Mock()
        self.body = BytesIO(b"Example document")
        self.s3.get_object.return_value = {"Body": self.body}
        self.ingest = Mock(return_value={"chunks_saved": 2})
        self.context = Mock()
        self.context.get_remaining_time_in_millis.return_value = 300000
        self.event = {"Records": [{
            "eventSource": "aws:s3", "eventName": "ObjectCreated:Put",
            "s3": {"bucket": {"name": "uploads-test"},
                   "object": {"key": "uploads%2Fuser%2Fcorpus%2Fdoc.txt"}},
        }]}
        for patcher in (
            patch.dict(os.environ, {"DOCUMENT_UPLOAD_BUCKET": "uploads-test"}),
            patch("app.workers.document_ingestion_worker.DynamoRepository", return_value=self.repository),
            patch("app.workers.document_ingestion_worker.boto3.client", return_value=self.s3),
            patch("app.workers.document_ingestion_worker.ingest_document", self.ingest),
            patch("app.workers.document_ingestion_worker.logger"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_success_downloads_and_marks_ready_with_same_lease(self):
        handler(self.event, self.context)
        self.s3.get_object.assert_called_once_with(
            Bucket="uploads-test", Key="uploads/user/corpus/doc.txt"
        )
        self.assertTrue(self.body.closed)
        self.assertEqual(b"Example document", self.ingest.call_args.kwargs["contents"])
        self.assertEqual("notes.txt", self.ingest.call_args.kwargs["filename"])
        owner = self.repository.claim_document_processing.call_args.kwargs["lease_owner"]
        self.repository.mark_document_ready.assert_called_once_with(
            corpus_id="corpus", document_id="doc", lease_owner=owner, chunks_saved=2
        )

    def test_completed_duplicate_does_not_download_or_embed(self):
        self.repository.get_document_status.return_value["status"] = "ready"
        handler(self.event, self.context)
        self.repository.claim_document_processing.assert_not_called()
        self.s3.get_object.assert_not_called()
        self.ingest.assert_not_called()

    def test_active_lease_raises_without_processing(self):
        self.repository.claim_document_processing.return_value = "busy"
        with self.assertRaisesRegex(RuntimeError, "another worker"):
            handler(self.event, self.context)
        self.ingest.assert_not_called()
        self.repository.mark_document_failed.assert_not_called()

    def test_completed_during_claim_is_skipped(self):
        self.repository.claim_document_processing.return_value = "ready"
        handler(self.event, self.context)
        self.s3.get_object.assert_not_called()

    def test_ingestion_error_marks_failure_and_propagates(self):
        self.ingest.side_effect = RuntimeError("Bedrock unavailable")
        with self.assertRaisesRegex(RuntimeError, "Bedrock unavailable"):
            handler(self.event, self.context)
        self.repository.mark_document_failed.assert_called_once()
        self.repository.mark_document_ready.assert_not_called()

    def test_mismatched_record_is_rejected_before_claim(self):
        self.repository.get_document_status.return_value["owner_id"] = "someone-else"
        with self.assertRaises(ValueError):
            handler(self.event, self.context)
        self.repository.claim_document_processing.assert_not_called()

    def test_s3_test_event_does_not_process(self):
        handler({"Event": "s3:TestEvent"}, self.context)
        self.repository.get_document_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
