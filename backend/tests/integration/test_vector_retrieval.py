"""Read-only integration test for Titan and pgvector retrieval.

Run from the backend directory:
    python -m tests.integration.test_vector_retrieval

This makes one Bedrock embedding call but does not modify the database.
"""

import argparse

from sqlalchemy import select

from app.database.database_connect import SessionLocal
from app.database.database_tables import CorpusTable
from app.services.vector_retrieval import retrieve_similar_chunks


def run_vector_retrieval_test(
    *,
    query: str,
    corpus_name: str = "My arXiv research corpus",
    limit: int = 5,
) -> None:
    """Find a stored corpus and print its nearest abstract chunks."""

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

        results = retrieve_similar_chunks(
            session,
            corpus_id=corpus.id,
            query=query,
            limit=limit,
        )

    if not results:
        raise AssertionError(
            "No embedded chunks were found for the selected corpus and model."
        )

    # The SQL query should return distances in ascending order.
    distances = [result.distance for result in results]
    assert distances == sorted(distances)
    assert len(results) <= limit

    print(f"Query: {query}")
    print(f"Corpus: {corpus_name}")
    print(f"Retrieved: {len(results)} chunk(s)\n")

    for rank, result in enumerate(results, start=1):
        print(f"{rank}. {result.title}")
        print(f"   arXiv ID: {result.external_id}")
        print(f"   Cosine distance: {result.distance:.4f}")
        print(f"   URL: {result.source_url}\n")

    print("Vector retrieval integration test passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Search a stored arXiv corpus using pgvector.",
    )
    parser.add_argument(
        "--query",
        default="How does retrieval-augmented generation help literature search?",
        help="natural-language query to embed and retrieve",
    )
    parser.add_argument(
        "--corpus-name",
        default="My arXiv research corpus",
        help="name of the stored corpus to search",
    )
    parser.add_argument(
        "--limit",
        type=int,
        choices=range(1, 21),
        default=5,
        metavar="1-20",
        help="maximum number of chunks to return (default: 5)",
    )
    arguments = parser.parse_args()

    run_vector_retrieval_test(
        query=arguments.query,
        corpus_name=arguments.corpus_name,
        limit=arguments.limit,
    )
