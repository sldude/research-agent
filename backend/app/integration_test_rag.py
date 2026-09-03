"""Read-only integration test for the complete RAG pipeline.

Run from the backend directory:
    python -m app.integration_test_rag

The test makes one Titan embedding call, one PostgreSQL retrieval query, and
one Nova generation call. It does not modify the database.
"""

import argparse
import re

from sqlalchemy import select

from app.database_connect import SessionLocal
from app.database_tables import CorpusTable
from app.rag_service import answer_question


def run_rag_integration_test(
    *,
    question: str,
    corpus_name: str = "My arXiv research corpus",
    limit: int = 5,
    max_tokens: int = 500,
) -> None:
    """Find a corpus, generate a grounded answer, and validate its sources."""

    with SessionLocal() as session:
        corpus = session.scalar(
            select(CorpusTable)
            .where(CorpusTable.name == corpus_name)
            .order_by(CorpusTable.id)
            .limit(1)
        )
        if corpus is None:
            raise AssertionError(
                f"Corpus {corpus_name!r} was not found. "
                "Permanently import at least one arXiv paper first."
            )

        result = answer_question(
            session,
            corpus_id=corpus.id,
            question=question,
            limit=limit,
            max_tokens=max_tokens,
        )

    assert result.answer.strip(), "The generation model returned an empty answer."
    assert result.sources, "No embedded sources were retrieved from the corpus."
    assert [source.number for source in result.sources] == list(
        range(1, len(result.sources) + 1)
    )

    # If Nova emitted bracketed citations, ensure every cited number maps to a
    # source supplied by the application. Answers without citations are shown
    # with a warning rather than failing nondeterministically.
    citation_numbers = {
        int(value) for value in re.findall(r"\[(\d+)\]", result.answer)
    }
    valid_numbers = {source.number for source in result.sources}
    invalid_numbers = citation_numbers - valid_numbers
    assert not invalid_numbers, f"Answer used unknown citations: {invalid_numbers}"

    print(f"Question: {result.question}")
    print(f"Corpus: {corpus_name}\n")
    print("Answer:\n")
    print(result.answer)
    print("\nSources:\n")

    for source in result.sources:
        print(f"[{source.number}] {source.title}")
        print(f"    arXiv ID: {source.external_id}")
        print(f"    Cosine distance: {source.distance:.4f}")
        print(f"    URL: {source.source_url}\n")

    if not citation_numbers:
        print("Warning: the answer did not contain a bracketed source citation.")

    print("RAG integration test passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test retrieval and grounded answer generation.",
    )
    parser.add_argument(
        "--query",
        default="How does retrieval-augmented generation help literature search?",
        help="question to answer from the stored corpus",
    )
    parser.add_argument(
        "--corpus-name",
        default="My arXiv research corpus",
        help="name of the corpus to search",
    )
    parser.add_argument(
        "--limit",
        type=int,
        choices=range(1, 21),
        default=5,
        metavar="1-20",
        help="maximum number of retrieved sources (default: 5)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        choices=range(1, 4097),
        default=500,
        metavar="1-4096",
        help="maximum generated answer tokens (default: 500)",
    )
    arguments = parser.parse_args()

    run_rag_integration_test(
        question=arguments.query,
        corpus_name=arguments.corpus_name,
        limit=arguments.limit,
        max_tokens=arguments.max_tokens,
    )
