"""Offline API tests; no DynamoDB or Bedrock calls are made."""

import base64
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import boto3
from botocore.config import Config
from fastapi.testclient import TestClient

from app.database.database_tables import CorpusRecord
from app.main import app, get_current_user_id
from app.schemas.api_schemas import RagAnswer


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_current_user_id] = lambda: "test-user"
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ok"}, response.json())

    @patch.dict(os.environ, {"DOCUMENT_UPLOAD_BUCKET": "test-uploads"})
    @patch("app.main.logger")
    @patch("app.main.boto3.Session")
    @patch("app.main.DynamoRepository")
    def test_upload_authorization(self, repository_class, session_class, logger):
        for failure in (None, "sign", "create"):
            with self.subTest(failure=failure):
                repository = Mock()
                repository.get_corpus.return_value = CorpusRecord(
                    id="corpus-1", name="Uploads", corpus_type="user_upload",
                    owner_id="test-user", created_at=datetime.now(timezone.utc),
                )
                repository_class.return_value = repository
                s3 = Mock()
                session_class.return_value.client.return_value = s3
                s3.generate_presigned_post.return_value = {
                    "url": "https://example.test/upload", "fields": {"policy": "test"},
                }
                if failure == "sign":
                    s3.generate_presigned_post.side_effect = RuntimeError("Signing failed")
                if failure == "create":
                    repository.create_document_status.side_effect = RuntimeError("Database unavailable")
                response = TestClient(app, raise_server_exceptions=False).post(
                    "/api/corpora/corpus-1/documents",
                    json={"filename": "notes.PDF", "size_bytes": 5_000_000},
                )
                s3.put_object.assert_not_called()
                repository.finish_document_upload.assert_not_called()
                if failure == "sign":
                    self.assertEqual(502, response.status_code)
                    repository.create_document_status.assert_not_called()
                    continue
                if failure == "create":
                    self.assertEqual(500, response.status_code)
                    self.assertNotIn("fields", response.text)
                    continue
                self.assertEqual(201, response.status_code)
                result = response.json()
                metadata = repository.create_document_status.call_args.kwargs
                self.assertEqual("test-user", metadata["owner_id"])
                self.assertEqual("notes.PDF", metadata["filename"])
                self.assertEqual("test-uploads", metadata["s3_bucket"])
                self.assertEqual(
                    f"uploads/test-user/corpus-1/{result['document_id']}.pdf",
                    metadata["s3_key"],
                )
                s3.generate_presigned_post.assert_called_once_with(
                    Bucket="test-uploads", Key=metadata["s3_key"],
                    Fields={"Content-Type": "application/octet-stream"},
                    Conditions=[
                        ["content-length-range", 1, 5_000_000],
                        {"Content-Type": "application/octet-stream"},
                    ],
                    ExpiresIn=300,
                )
                self.assertEqual("uploading", result["status"])
                self.assertEqual("https://example.test/upload", result["upload_url"])
                self.assertEqual({"policy": "test"}, result["fields"])
                self.assertEqual(300, result["expires_in"])

    @patch.dict(os.environ, {"DOCUMENT_UPLOAD_BUCKET": "test-uploads"})
    @patch("app.main.boto3.Session")
    @patch("app.main.DynamoRepository")
    def test_upload_validation(self, repository_class, session_class):
        repository = repository_class.return_value
        for owner, filename, size, expected in (
            ("test-user", "notes.txt", 5_000_001, 413),
            ("test-user", "notes.exe", 1, 415),
            ("test-user", "notes.txt", 0, 422),
            ("test-user", "notes.txt", -1, 422),
            ("test-user", "notes.txt", "5", 422),
            ("test-user", "notes.txt", True, 422),
            ("test-user", "", 1, 422),
            ("other-user", "notes.txt", 1, 403),
            (None, "notes.txt", 1, 403),
        ):
            with self.subTest(owner=owner, filename=filename, size=size):
                repository.get_corpus.return_value = CorpusRecord(
                    id="corpus-1", name="Uploads", corpus_type="user_upload",
                    owner_id=owner, created_at=datetime.now(timezone.utc),
                )
                response = self.client.post(
                    "/api/corpora/corpus-1/documents",
                    json={"filename": filename, "size_bytes": size},
                )
                self.assertEqual(expected, response.status_code)
        repository.get_corpus.return_value = None
        response = self.client.post(
            "/api/corpora/missing/documents",
            json={"filename": "notes.txt", "size_bytes": 1},
        )
        self.assertEqual(404, response.status_code)
        repository.create_document_status.assert_not_called()
        session_class.assert_not_called()

    @patch.dict(os.environ, {"DOCUMENT_UPLOAD_BUCKET": "test-uploads"}, clear=True)
    @patch("app.main.DynamoRepository")
    def test_upload_signed_policy(self, repository_class):
        """Inspect a real SDK policy using fake credentials and no network."""
        repository_class.return_value.get_corpus.return_value = CorpusRecord(
            id="corpus-1", name="Uploads", corpus_type="user_upload",
            owner_id="test-user", created_at=datetime.now(timezone.utc),
        )
        s3 = boto3.client(
            "s3", region_name="us-east-2",
            aws_access_key_id="test", aws_secret_access_key="test",
            config=Config(signature_version="s3v4"),
        )
        with patch("app.main.boto3.Session") as session_class:
            session_class.return_value.client.return_value = s3
            response = self.client.post(
                "/api/corpora/corpus-1/documents",
                json={"filename": "notes.md", "size_bytes": 1},
            )
        self.assertEqual(201, response.status_code)
        result = response.json()
        policy = json.loads(base64.b64decode(result["fields"]["policy"]))
        self.assertIn(["content-length-range", 1, 5_000_000], policy["conditions"])
        self.assertIn({"bucket": "test-uploads"}, policy["conditions"])
        self.assertIn(
            {"key": f"uploads/test-user/corpus-1/{result['document_id']}.md"},
            policy["conditions"],
        )

    def test_cors_preflight(self) -> None:
        response = self.client.options("/api/corpora")
        self.assertEqual(204, response.status_code)

    @patch("app.main.DynamoRepository")
    def test_document_status_requires_owner(self, repository_class: Mock) -> None:
        repository = repository_class.return_value
        path = "/api/corpora/corpus-1/documents/doc-1/status"
        repository.get_document_status.return_value = {
            "owner_id": "test-user", "status": "ready", "chunks_saved": 2
        }
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {"document_id": "doc-1", "status": "ready", "chunks_saved": 2},
            response.json(),
        )
        repository.get_document_status.return_value["owner_id"] = "other-user"
        self.assertEqual(404, self.client.get(path).status_code)
        repository.get_document_status.return_value = None
        self.assertEqual(404, self.client.get(path).status_code)

    @patch("app.main.DynamoRepository")
    def test_list_corpora(self, repository_class: Mock) -> None:
        repository_class.return_value.count_documents.side_effect = [1234, 0]
        repository_class.return_value.list_corpora.return_value = [
            CorpusRecord(
                id="corpus-1",
                name="Papers",
                corpus_type="research_abstract",
                owner_id=None,
                created_at=datetime.now(timezone.utc),
            ),
            CorpusRecord(
                id="corpus-2",
                name="My papers",
                corpus_type="user_upload",
                owner_id="test-user",
                created_at=datetime.now(timezone.utc),
            ),
            CorpusRecord(
                id="corpus-3",
                name="Someone else's papers",
                corpus_type="user_upload",
                owner_id="another-user",
                created_at=datetime.now(timezone.utc),
            ),
        ]
        response = self.client.get("/api/corpora")
        self.assertEqual(200, response.status_code)
        self.assertEqual(["corpus-1", "corpus-2"], [row["id"] for row in response.json()])
        self.assertEqual([1234, 0], [row["document_count"] for row in response.json()])
        self.assertEqual(["corpus-1", "corpus-2"], [
            call.args[0] for call in repository_class.return_value.count_documents.call_args_list
        ])

    @patch("app.main.DynamoRepository")
    def test_create_existing_corpus_returns_current_count(self, repository_class: Mock) -> None:
        repository = repository_class.return_value
        repository.get_or_create_corpus.return_value = CorpusRecord(
            id="corpus-1", name="Existing", corpus_type="user_upload",
            owner_id="test-user", created_at=datetime.now(timezone.utc),
        )
        repository.count_documents.return_value = 3
        response = self.client.post("/api/corpora", json={"name": "Existing"})
        self.assertEqual(200, response.status_code)
        self.assertEqual(3, response.json()["document_count"])
        repository.count_documents.assert_called_once_with("corpus-1", "user_upload")

    @patch("app.main.answer_question")
    @patch("app.main.DynamoRepository")
    def test_rag_answer(self, repository_class: Mock, answer: Mock) -> None:
        repository_class.return_value.get_corpus.return_value = CorpusRecord(
            id="corpus-1",
            name="Papers",
            corpus_type="research_abstract",
            owner_id=None,
            created_at=datetime.now(timezone.utc),
        )
        answer.return_value = RagAnswer(
            question="What is RAG?",
            answer="A grounded answer.",
            sources=[],
        )
        response = self.client.post(
            "/api/rag/answer",
            json={"corpus_id": "corpus-1", "question": "What is RAG?"},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("A grounded answer.", response.json()["answer"])

    @patch("app.main.DynamoRepository")
    def test_missing_corpus_returns_404(self, repository_class: Mock) -> None:
        repository_class.return_value.get_corpus.return_value = None
        response = self.client.post(
            "/api/rag/answer",
            json={"corpus_id": "missing", "question": "What is RAG?"},
        )
        self.assertEqual(404, response.status_code)

    @patch("app.main.DynamoRepository")
    def test_other_users_private_corpus_returns_403(
        self, repository_class: Mock
    ) -> None:
        repository_class.return_value.get_corpus.return_value = CorpusRecord(
            id="private-corpus",
            name="Private papers",
            corpus_type="user_upload",
            owner_id="another-user",
            created_at=datetime.now(timezone.utc),
        )
        response = self.client.post(
            "/api/rag/answer",
            json={"corpus_id": "private-corpus", "question": "What is RAG?"},
        )
        self.assertEqual(403, response.status_code)

    @patch("app.main.DynamoRepository")
    def test_list_documents_requires_corpus_owner(
        self, repository_class: Mock
    ) -> None:
        repository = repository_class.return_value
        path = "/api/corpora/corpus-1/documents"
        document = {
            "document_id": "doc-1",
            "filename": "notes.txt",
            "status": "ready",
            "created_at": "2026-09-21T12:00:00+00:00",
            "chunks_saved": 2,
        }

        repository.get_corpus.return_value = CorpusRecord(
            id="corpus-1",
            name="My notes",
            corpus_type="user_upload",
            owner_id="test-user",
            created_at=datetime.now(timezone.utc),
        )
        repository.list_document_statuses.return_value = [document]

        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        self.assertEqual([document], response.json())
        repository.list_document_statuses.assert_called_once_with("corpus-1")

        repository.list_document_statuses.reset_mock()
        repository.get_corpus.return_value = None
        self.assertEqual(404, self.client.get(path).status_code)
        repository.list_document_statuses.assert_not_called()

        repository.get_corpus.return_value = CorpusRecord(
            id="corpus-1",
            name="Someone else's notes",
            corpus_type="user_upload",
            owner_id="other-user",
            created_at=datetime.now(timezone.utc),
        )
        self.assertEqual(404, self.client.get(path).status_code)
        repository.list_document_statuses.assert_not_called()

    @patch("app.main.boto3.Session")
    @patch("app.main.DynamoRepository")
    def test_document_preview_and_delete_require_owner_and_finished_status(
        self, repository_class: Mock, session_class: Mock
    ) -> None:
        repository = repository_class.return_value
        document = {
            "corpus_id": "corpus-1", "document_id": "doc-1",
            "owner_id": "test-user", "filename": "notes.txt",
            "s3_bucket": "uploads", "s3_key": "uploads/user/doc-1.txt",
            "status": "ready", "created_at": "now", "updated_at": "now",
        }
        repository.get_document_status.return_value = document
        s3 = session_class.return_value.client.return_value
        body = Mock()
        body.read.return_value = b"preview text"
        s3.get_object.return_value = {"Body": body}
        path = "/api/corpora/corpus-1/documents/doc-1"

        preview = self.client.get(f"{path}/content")
        self.assertEqual(200, preview.status_code)
        self.assertEqual("preview text", preview.text)
        self.assertTrue(preview.headers["content-type"].startswith("text/plain"))

        deleted = self.client.delete(path)
        self.assertEqual(204, deleted.status_code)
        s3.delete_object.assert_called_once_with(
            Bucket="uploads", Key="uploads/user/doc-1.txt"
        )
        repository.delete_document.assert_called_once_with(
            corpus_id="corpus-1", document_id="doc-1"
        )

        document["status"] = "processing"
        self.assertEqual(409, self.client.delete(path).status_code)
        document["owner_id"] = "another-user"
        self.assertEqual(404, self.client.get(f"{path}/content").status_code)

    @patch("app.main.boto3.Session")
    @patch("app.main.DynamoRepository")
    def test_delete_corpus_cascades_only_after_processing_finishes(
        self, repository_class: Mock, session_class: Mock
    ) -> None:
        repository = repository_class.return_value
        repository.get_corpus.return_value = CorpusRecord(
            id="corpus-1", name="Notes", corpus_type="user_upload",
            owner_id="test-user", created_at=datetime.now(timezone.utc),
        )
        listed = {"document_id": "doc-1", "filename": "notes.txt", "status": "ready",
                  "created_at": "now", "chunks_saved": 1}
        repository.list_document_statuses.return_value = [listed]
        metadata = {
            **listed, "corpus_id": "corpus-1", "owner_id": "test-user",
            "s3_bucket": "uploads", "s3_key": "uploads/user/doc-1.txt", "updated_at": "now",
        }
        repository.get_document_status.return_value = metadata

        response = self.client.delete("/api/corpora/corpus-1")
        self.assertEqual(204, response.status_code)
        session_class.return_value.client.return_value.delete_object.assert_called_once_with(
            Bucket="uploads", Key="uploads/user/doc-1.txt"
        )
        repository.delete_corpus.assert_called_once_with("corpus-1")

        listed["status"] = "processing"
        metadata["status"] = "processing"
        repository.delete_corpus.reset_mock()
        self.assertEqual(409, self.client.delete("/api/corpora/corpus-1").status_code)
        repository.delete_corpus.assert_not_called()

if __name__ == "__main__":
    unittest.main()
