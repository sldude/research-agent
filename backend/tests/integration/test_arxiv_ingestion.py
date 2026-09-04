"""Live arXiv + Bedrock + DynamoDB ingestion test with targeted cleanup.

Run from the backend directory:
    python -m tests.integration.test_arxiv_ingestion
"""

from uuid import uuid4

from app.clients.arxiv_api_client import ArxivClient
from app.clients.embeddings import DIMENSIONS
from app.database.repository import DynamoRepository
from app.services.arxiv_ingestion import ingest_arxiv_papers


def run_arxiv_ingestion_integration_test() -> None:
    print("Retrieving one paper from arXiv...")
    with ArxivClient() as client:
        papers = client.search("retrieval augmented generation", max_results=1)
    if not papers:
        raise AssertionError("arXiv returned no papers for the test query")

    repository = DynamoRepository()
    corpus_name = f"arXiv ingestion integration test {uuid4()}"
    corpus_id: str | None = None
    try:
        stats = ingest_arxiv_papers(
            papers,
            corpus_name=corpus_name,
            owner_id="integration-test-user",
            repository=repository,
        )
        saved_document = stats.documents[0]
        corpus_id = saved_document.corpus_id
        loaded_document = repository.get_document(
            corpus_id=corpus_id,
            source="arxiv",
            external_id=papers[0].external_id,
        )
        assert loaded_document is not None
        assert loaded_document.external_id == papers[0].external_id
        assert len(
            repository.get_embedding(
                corpus_id=corpus_id,
                chunk_id=loaded_document.chunk_id,
            )
        ) == DIMENSIONS
        assert stats.processed == stats.created == stats.bedrock_calls == 1

        duplicate_stats = ingest_arxiv_papers(
            papers,
            corpus_name=corpus_name,
            owner_id="integration-test-user",
            repository=repository,
        )
        assert duplicate_stats.unchanged == 1
        assert duplicate_stats.bedrock_calls == 0

        print(f"Inserted paper: {loaded_document.title}")
        print("\nIngestion statistics:")
        print(f"Retrieved: {len(papers)}")
        print(f"Processed: {stats.processed}")
        print(f"Created: {stats.created}")
        print(f"Updated: {stats.updated}")
        print(f"Unchanged on duplicate import: {duplicate_stats.unchanged}")
        print(f"Bedrock calls: {stats.bedrock_calls}")
        print("arXiv ingestion integration test passed.")
    finally:
        if corpus_id is not None:
            repository.delete_corpus(corpus_id)


if __name__ == "__main__":
    run_arxiv_ingestion_integration_test()
