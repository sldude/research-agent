"""Offline API tests; no DynamoDB or Bedrock calls are made."""

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.database.database_tables import CorpusRecord
from app.main import app
from app.schemas.api_schemas import RagAnswer


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ok"}, response.json())

    @patch("app.main.DynamoRepository")
    def test_list_corpora(self, repository_class: Mock) -> None:
        repository_class.return_value.list_corpora.return_value = [
            CorpusRecord(
                id="corpus-1",
                name="Papers",
                corpus_type="research_abstract",
                owner_id=None,
                created_at=datetime.now(timezone.utc),
            )
        ]
        response = self.client.get("/api/corpora")
        self.assertEqual(200, response.status_code)
        self.assertEqual("corpus-1", response.json()[0]["id"])

    @patch("app.main.answer_question")
    @patch("app.main.DynamoRepository")
    def test_rag_answer(self, repository_class: Mock, answer: Mock) -> None:
        repository_class.return_value.get_corpus.return_value = Mock()
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


if __name__ == "__main__":
    unittest.main()
