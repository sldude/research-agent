"""Search arXiv and permanently store matching paper abstracts
and related metadata ingested from API into database tables
"""

import argparse

from app.clients.arxiv_api_client import ArxivClient
from app.services.arxiv_ingestion import ingest_arxiv_papers


def import_arxiv_metadata(max_results: int = 5) -> None:
    """Prompt for a topic and permanently import up to max_results papers."""

    if not 1 <= max_results <= 100:
        raise ValueError("max_results must be between 1 and 100")

    query = input("arXiv search topic: ").strip()

    if not query:
        print("No query provided.")
        return

    with ArxivClient() as client:
        papers = client.search(
            query,
            max_results=max_results,
        )

    if not papers:
        print("No papers found.")
        return

    print("\nPapers to save:")

    for paper in papers:
        print(f"- {paper.title} ({paper.external_id})")

    confirmation = input("\nSave these papers? [y/N]: ").strip().lower()

    if confirmation != "y":
        print("Nothing was saved.")
        return

    stats = ingest_arxiv_papers(
        papers,
        corpus_name="My arXiv research corpus",
        owner_id=None,
    )

    print("\nProcessed papers:")
    for result in stats.results:
        print(f"- [{result.status}] {result.document.title}")

    print("\nIngestion summary:")
    print(f"Processed: {stats.processed}")
    print(f"Created: {stats.created}")
    print(f"Updated: {stats.updated}")
    print(f"Unchanged: {stats.unchanged}")
    print(f"Bedrock calls: {stats.bedrock_calls}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Search arXiv and permanently import paper metadata.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=5,
        choices=range(1, 101),
        metavar="1-100",
        help="maximum number of papers to import (default: 5)",
    )
    arguments = parser.parse_args()
    import_arxiv_metadata(max_results=arguments.max_results)
