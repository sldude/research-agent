"""Offline regression tests for document citations and corpus coverage."""

import unittest
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.clients.generation import GenerationError, GenerationTruncatedError
from app.schemas.api_schemas import RagAnswer, RetrievedChunk
from app.services.rag_service import (
    answer_question, build_context, create_rag_sources, is_corpus_overview,
    resolve_references,
)
from app.database.repository import DynamoRepository


def chunk(document, index=0):
    return RetrievedChunk(
        chunk_id=f"{document}-{index}", document_id=document,
        external_id=None, title=document, content=f"Evidence {document}-{index}",
        source_url=None, distance=0.1,
    )


def generated(answer, count=None, additional=None):
    return json.dumps({"answer": answer, "requested_source_count": count,
                       "additional_source_ids": additional or []})


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

    @patch("app.services.rag_service.generate_text", return_value=generated("Overview [1][2][3][4][5]"))
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
        self.assertEqual("Overview [1][2][3][4][5]", result.answer)

    @patch("app.services.rag_service.generate_text", return_value=generated("Topics [1]"))
    def test_truncation_is_internal_without_appended_coverage_note(self, generate):
        repository = Mock()
        repository.overview_documents.return_value = ([SimpleNamespace(
            chunk_id="a", id="a", external_id=None, title="A", abstract=None,
            content="A subject", source_url=None,
        )], True)
        result = answer_question(corpus_id="c", question="What is in the corpus?", repository=repository)
        self.assertEqual("Topics [1]", result.answer)
        self.assertIn("More documents exist: True", generate.call_args.args[0])

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

    @patch("app.services.rag_service.generate_text", return_value=generated("Topic A [1]"))
    def test_small_inventory_does_not_append_uncited_documents(self, generate):
        repository = Mock()
        repository.overview_documents.return_value = ([SimpleNamespace(
            chunk_id=title, id=title, external_id=None, title=title,
            abstract=None, content="Evidence", source_url=None,
        ) for title in ("A", "B")], False)
        result = answer_question(corpus_id="c", question="What is in the corpus?", repository=repository)
        self.assertEqual("Topic A [1]", result.answer)
        self.assertEqual(["A"], [s.document_id for s in result.sources])

    def test_default_sources_match_citations_and_preserve_original_metadata(self):
        records = [chunk("a"), chunk("b"), chunk("b", 1), chunk("c")]
        records[1].title = "Original title"
        records[1].source_url = "https://example.org/paper"
        result = resolve_references(generated("Two related sentences. Same evidence [2]."),
                                    create_rag_sources(records), "Explain")
        self.assertEqual([2], [s.number for s in result.sources])
        self.assertEqual("Original title", result.sources[0].title)
        self.assertEqual("https://example.org/paper", result.sources[0].source_url)
        self.assertEqual("cited", result.sources[0].reference_type)

    def test_requested_sources_include_only_selected_relevant_extras(self):
        sources = create_rag_sources([chunk("a"), chunk("b"), chunk("irrelevant")])
        result = resolve_references(generated("Claim [1].", 5, [2]), sources,
                                    "Explain with five sources")
        self.assertEqual([1, 2], [s.number for s in result.sources])
        self.assertEqual(["cited", "additional"], [s.reference_type for s in result.sources])

    def test_unsupported_answer_has_no_sources(self):
        result = resolve_references(generated("Add relevant documents to this corpus."),
                                    create_rag_sources([chunk("a")]), "Explain")
        self.assertEqual([], result.sources)

    def test_invalid_model_selections_are_rejected(self):
        sources = create_rag_sources([chunk("a"), chunk("b")])
        for raw in (
            generated("Claim [99]."), generated("Claim [0]."),
            generated("Claim [1, 2]."), generated("Claim [1].", 3, [99]),
            generated("Claim [1].", 3, [2, 2]), generated("Claim [1].", 3, [1]),
            generated("Claim [1].", None, [2]), generated("Claim [1][2].", 1),
            generated("Claim [1].", 2, [True]), generated("Claim [1].", 2, ["2"]),
            '{"answer": "Claim [1].", "title": "Invented"}', "not JSON",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                resolve_references(raw, sources, "Explain")

    def test_api_schema_rejects_inconsistent_or_duplicate_reference_lists(self):
        a, b = create_rag_sources([chunk("a"), chunk("b")])
        for answer, sources in (("Claim [1].", []), ("Claim [1].", [a, b]),
                                ("Claim [1].", [a, a]),
                                ("Claim [1][2].", [a, b.model_copy(update={"document_id": "a"})])):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                RagAnswer(question="Explain", answer=answer, sources=sources)

    @patch("app.services.rag_service.retrieve_similar_chunks", return_value=[chunk("a")])
    @patch("app.services.rag_service.generate_text")
    def test_invalid_generation_retries_once(self, generate, retrieve):
        generate.side_effect = [generated("Claim [99]."), generated("Claim [1].")]
        result = answer_question(corpus_id="c", question="Explain")
        self.assertEqual("Claim [1].", result.answer)
        self.assertEqual(2, generate.call_count)
        self.assertGreater(generate.call_args_list[1].kwargs["max_tokens"],
                           generate.call_args_list[0].kwargs["max_tokens"])

    def test_complete_fenced_json_still_validates_references(self):
        sources = create_rag_sources([chunk("a")])
        result = resolve_references("```json\n" + generated("Claim [1].") + "\n```", sources, "Explain")
        self.assertEqual("Claim [1].", result.answer)
        with self.assertRaises(ValueError):
            resolve_references("```json\n" + generated("Claim [99].") + "\n```", sources, "Explain")

    @patch("app.services.rag_service.retrieve_similar_chunks", return_value=[chunk("a")])
    @patch("app.services.rag_service.generate_text")
    def test_truncated_response_retries_with_larger_budget(self, generate, retrieve):
        generate.side_effect = [GenerationTruncatedError("Truncated"), generated("Claim [1].")]
        result = answer_question(corpus_id="c", question="Explain", max_tokens=600)
        self.assertEqual("Claim [1].", result.answer)
        self.assertEqual([600, 1200], [call.kwargs["max_tokens"] for call in generate.call_args_list])

    @patch("app.services.rag_service.retrieve_similar_chunks", return_value=[chunk("a")])
    @patch("app.services.rag_service.generate_text")
    def test_partial_json_retries_without_serving_partial_answer(self, generate, retrieve):
        generate.side_effect = ['{"answer": "Unfinished', generated("Claim [1].")]
        result = answer_question(corpus_id="c", question="Explain")
        self.assertEqual("Claim [1].", result.answer)

    @patch("app.services.rag_service.retrieve_similar_chunks", return_value=[chunk("a")])
    @patch("app.services.rag_service.generate_text", return_value=generated("Claim [99]."))
    def test_repeated_invalid_generation_fails_without_serving_bad_citations(self, generate, retrieve):
        with self.assertRaises(GenerationError):
            answer_question(corpus_id="c", question="Explain")
        self.assertEqual(2, generate.call_count)
