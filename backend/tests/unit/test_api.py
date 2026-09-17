"""Offline API tests; no DynamoDB or Bedrock calls are made."""

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

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
    def test_upload_status_lifecycle(
        self, repository_class: Mock, session_class: Mock, logger: Mock
    ) -> None:
        for failure in (None, "create", "s3", "failure_status"):
            with self.subTest(failure=failure):
                repository = Mock()
                repository.get_corpus.return_value = CorpusRecord(
                    id="corpus-1", name="Uploads", corpus_type="user_upload",
                    owner_id="test-user", created_at=datetime.now(timezone.utc),
                )
                repository_class.return_value = repository
                s3 = Mock()
                session_class.return_value.client.return_value = s3
                events = []

                def create(**kwargs):
                    events.append("create")
                    if failure == "create":
                        raise RuntimeError("Database unavailable")

                def upload(**kwargs):
                    events.append("upload")
                    if failure in ("s3", "failure_status"):
                        raise RuntimeError("S3 unavailable")

                def finish(**kwargs):
                    events.append("finish")
                    if failure == "failure_status":
                        raise RuntimeError("Database unavailable")

                repository.create_document_status.side_effect = create
                repository.finish_document_upload.side_effect = finish
                s3.put_object.side_effect = upload
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/api/corpora/corpus-1/documents",
                    files={"file": ("notes.txt", b"Example text", "text/plain")},
                )

                if failure == "create":
                    self.assertEqual(500, response.status_code)
                    self.assertEqual(["create"], events)
                    s3.put_object.assert_not_called()
                    repository.finish_document_upload.assert_not_called()
                    continue

                self.assertEqual(["create", "upload", "finish"], events)
                metadata = repository.create_document_status.call_args.kwargs
                repository.finish_document_upload.assert_called_once_with(
                    corpus_id="corpus-1",
                    document_id=metadata["document_id"],
                    succeeded=failure is None,
                )
                self.assertEqual("test-user", metadata["owner_id"])
                self.assertEqual("notes.txt", metadata["filename"])
                self.assertEqual("test-uploads", metadata["s3_bucket"])
                self.assertEqual(
                    metadata["s3_key"], s3.put_object.call_args.kwargs["Key"]
                )
                self.assertEqual(201 if failure is None else 502, response.status_code)
                if failure is None:
                    self.assertEqual("uploaded", response.json()["status"])
                    self.assertEqual(metadata["document_id"], response.json()["document_id"])
                else:
                    self.assertEqual(
                        "Document upload failed. Please try again.",
                        response.json()["detail"],
                    )

    def test_cors_preflight(self) -> None:
        response = self.client.options("/api/corpora")
        self.assertEqual(204, response.status_code)

    @patch("app.main.DynamoRepository")
    def test_list_corpora(self, repository_class: Mock) -> None:
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


if __name__ == "__main__":
    unittest.main()
