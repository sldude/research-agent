"""Opt-in integration test for Titan embeddings and PostgreSQL pgvector.

Run from the backend directory with:
    python -m app.integration_test_embeddings

The test invokes Amazon Bedrock once and rolls back its database transaction,
so it does not leave test records in the database.
"""

from uuid import uuid4

from sqlalchemy import select

from app.database_connect import SessionLocal
from app.database_tables import DocumentChunkTable, PapersTable
from app.embeddings import DIMENSIONS, MODEL_ID, embed_text


def run_embedding_integration_test() -> None:
    content = "Retrieval-augmented generation for scientific literature."

    print("Generating an embedding with Amazon Bedrock...")
    embedding = embed_text(content)
    assert len(embedding) == DIMENSIONS

    external_id = f"integration-test-{uuid4()}"

    with SessionLocal() as session:
        try:
            paper = PapersTable(
                external_id=external_id,
                source="integration-test",
                title="Embedding Integration Test",
            )
            chunk = DocumentChunkTable(
                chunk_index=0,
                content=content,
                embedding=embedding,
                embedding_model=MODEL_ID,
            )
            paper.chunks.append(chunk)
            session.add(paper)

            # Send the inserts to PostgreSQL without committing them.
            session.flush()
            chunk_id = chunk.id

            # Clear ORM-cached values so this retrieval reads from PostgreSQL.
            session.expire_all()
            saved_chunk = session.scalar(
                select(DocumentChunkTable).where(
                    DocumentChunkTable.id == chunk_id
                )
            )

            assert saved_chunk is not None
            assert saved_chunk.embedding is not None
            assert len(saved_chunk.embedding) == DIMENSIONS
            assert saved_chunk.embedding_model == MODEL_ID

            cosine_distance = session.scalar(
                select(
                    DocumentChunkTable.embedding.cosine_distance(embedding)
                ).where(DocumentChunkTable.id == chunk_id)
            )

            assert cosine_distance is not None
            assert abs(float(cosine_distance)) < 1e-5

            print(f"Stored and retrieved a {DIMENSIONS}-dimension vector.")
            print(f"Cosine distance from the original: {cosine_distance}")
            print("Embedding integration test passed.")
        finally:
            # Remove all changes made by this test, even if an assertion fails.
            session.rollback()


if __name__ == "__main__":
    run_embedding_integration_test()
