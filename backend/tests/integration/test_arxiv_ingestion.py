"""Live arXiv + Bedrock + PostgreSQL test that always rolls back its inserts.

Run from the backend directory after applying migrations:
    python -m tests.integration.test_arxiv_ingestion
"""

from sqlalchemy import func, select

from app.clients.arxiv_api_client import ArxivClient
from app.clients.embeddings import DIMENSIONS
from app.database.database_connect import SessionLocal
from app.database.database_tables import DocumentChunkTable, DocumentTable
from app.services.arxiv_ingestion import ingest_arxiv_papers


def run_arxiv_ingestion_integration_test() -> None:
    print("Retrieving one paper from arXiv...")
    with ArxivClient() as client:
        papers = client.search(
            "retrieval augmented generation",
            max_results=1,
        )

    if not papers:
        raise AssertionError("arXiv returned no papers for the test query")

    with SessionLocal() as session:
        try:
            print("Embedding and inserting the paper without committing...")
            stats = ingest_arxiv_papers(
                session,
                papers,
                corpus_name="arXiv ingestion integration test",
                owner_id="integration-test-user",
            )
            document_id = stats.documents[0].id

            # Expire ORM state so the assertions load the inserted rows back
            # from PostgreSQL rather than relying on the in-memory objects.
            session.expire_all()
            saved_document = session.get(DocumentTable, document_id)
            chunk_count = session.scalar(
                select(func.count())
                .select_from(DocumentChunkTable)
                .where(DocumentChunkTable.document_id == document_id)
            )

            assert saved_document is not None
            assert saved_document.source == "arxiv"
            assert saved_document.external_id == papers[0].external_id
            assert len(saved_document.chunks) == 1
            assert saved_document.chunks[0].embedding is not None
            assert len(saved_document.chunks[0].embedding) == DIMENSIONS
            assert chunk_count == 1
            assert stats.processed == 1
            assert stats.created == 1
            assert stats.bedrock_calls == 1

            print(f"Inserted paper: {saved_document.title}")
            print(f"Document ID: {saved_document.id}")
            print(f"Embedding dimensions: {DIMENSIONS}")
            print("\nIngestion statistics:")
            print(f"Retrieved: {len(papers)}")
            print(f"Processed: {stats.processed}")
            print(f"Created: {stats.created}")
            print(f"Updated: {stats.updated}")
            print(f"Unchanged: {stats.unchanged}")
            print(f"Bedrock calls: {stats.bedrock_calls}")
            print("arXiv ingestion integration test passed.")
        finally:
            # This test proves inserts work while leaving no sample data behind.
            session.rollback()


if __name__ == "__main__":
    run_arxiv_ingestion_integration_test()
