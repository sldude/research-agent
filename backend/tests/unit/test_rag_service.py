"""Offline regression tests for document citations and corpus coverage."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.schemas.api_schemas import RetrievedChunk
from app.services.rag_service import (
    answer_question, build_context, create_rag_sources, is_corpus_overview,
)
from app.database.repository import DynamoRepository


def chunk(document, index=0):
    return RetrievedChunk(
        chunk_id=f"{document}-{index}", document_id=document,
        external_id=None, title=document, content=f"Evidence {document}-{index}",
        source_url=None, distance=0.1,
    )


class RagTests(unittest.TestCase):
    def test_five_chunks_of_one_document_have_one_reference(self):
        chunks = [chunk("one", i) for i in range(5)]
        self.assertEqual([1], [s.number for s in create_rag_sources(chunks)])
        context = build_context(chunks)
        self.assertEqual(1, context.count("[1]"))
        self.assertNotIn("[2]", context)
        for i in range(5):
            self.assertIn(f"Evidence one-{i}", context)

    def test_interleaved_documents_use_matching_numbers(self):
        chunks = [chunk("a"), chunk("b"), chunk("a", 1), chunk("c")]
        sources = create_rag_sources(chunks)
        self.assertEqual(["a", "b", "c"], [s.document_id for s in sources])
        self.assertIn("[3]\nTitle: c", build_context(chunks))

    def test_overview_intent(self):
        for question in ("What is inside the corpus?", "What topics are in this corpus?",
                         "Summarize my documents", "What does this corpus contain?",
                         "Give me a corpus overview"):
            self.assertTrue(is_corpus_overview(question), question)
        for question in ("What information in the corpus is related to transformers?",
                         "What is attention?", "Summarize the corpus evidence about RAG"):
            self.assertFalse(is_corpus_overview(question), question)

    @patch("app.services.rag_service.generate_text", return_value="Overview [1][2][3][4][5]")
    @patch("app.services.rag_service.retrieve_similar_chunks")
    def test_overview_includes_five_documents_independent_of_search_limit(self, retrieve, generate):
        repository = Mock()
        repository.overview_documents.return_value = ([SimpleNamespace(
            chunk_id=str(i), id=str(i), external_id=None, title=f"Document {i}",
            abstract=None, content=f"Distinct subject {i}", source_url=None,
        ) for i in range(5)], False)
        result = answer_question(corpus_id="corpus", question="What is inside the corpus?",
                                 limit=1, repository=repository)
        retrieve.assert_not_called()
        self.assertEqual(5, len(result.sources))
        for i in range(5):
            self.assertIn(f"Distinct subject {i}", generate.call_args.args[0])
        self.assertTrue(all(s.distance is None for s in result.sources))
        self.assertIn("opening excerpts", result.answer)

    @patch("app.services.rag_service.generate_text", return_value="Topics [1]")
    def test_truncation_is_disclosed_independently_of_model(self, generate):
        repository = Mock()
        repository.overview_documents.return_value = ([SimpleNamespace(
            chunk_id="a", id="a", external_id=None, title="A", abstract=None,
            content="A subject", source_url=None,
        )], True)
        result = answer_question(corpus_id="c", question="What is in the corpus?", repository=repository)
        self.assertIn("Partial overview", result.answer)

    @patch("app.services.rag_service.generate_text")
    def test_empty_overview_skips_generation(self, generate):
        repository = Mock()
        repository.overview_documents.return_value = ([], False)
        result = answer_question(corpus_id="c", question="What is in the corpus?", repository=repository)
        self.assertEqual([], result.sources)
        generate.assert_not_called()

    @patch("app.database.repository._document_from_item")
    def test_inventory_seeks_past_dominant_document_and_checks_overflow(self, convert):
        client = Mock()
        client.query.side_effect = [{"Items": [{}]}, {"Items": [{}]}, {"Items": [{}]}]
        convert.side_effect = [SimpleNamespace(id=value, source="arxiv") for value in ("a", "b", "c")]
        records, truncated = DynamoRepository(client).overview_documents("corpus", limit=2)
        self.assertEqual(["a", "b"], [record.id for record in records])
        self.assertTrue(truncated)
        calls = client.query.call_args_list
        self.assertEqual({"S": "a#chunk:~"}, calls[1].kwargs["ExpressionAttributeValues"][":after"])
        self.assertEqual({"S": "corpus"}, calls[1].kwargs["ExpressionAttributeValues"][":corpus"])
        self.assertNotIn("embedding", calls[0].kwargs["ExpressionAttributeNames"].values())

    def test_empty_inventory(self):
        client = Mock()
        client.query.return_value = {"Items": []}
        self.assertEqual(([], False), DynamoRepository(client).overview_documents("c"))

    @patch("app.services.rag_service.generate_text", return_value="Topic A [1]")
    def test_small_inventory_mentions_document_omitted_by_model(self, generate):
        repository = Mock()
        repository.overview_documents.return_value = ([SimpleNamespace(
            chunk_id=title, id=title, external_id=None, title=title,
            abstract=None, content="Evidence", source_url=None,
        ) for title in ("A", "B")], False)
        result = answer_question(corpus_id="c", question="What is in the corpus?", repository=repository)
        self.assertIn("B [2]", result.answer)
