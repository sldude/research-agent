"""Retrieve document chunks using pgvector cosine distance."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api_schemas import RetrievedChunk
from app.database_tables import DocumentChunkTable, DocumentTable
from app.embeddings import MODEL_ID, embed_text


def retrieve_similar_chunks(
    session: Session,
    *,
    corpus_id: int,
    query: str,
    limit: int = 5,
) -> list[RetrievedChunk]:
    """Return the chunks most semantically similar to a question."""

    cleaned_query = query.strip()

    if not cleaned_query:
        raise ValueError("query cannot be empty")

    if corpus_id <= 0:
        raise ValueError("corpus_id must be positive")

    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")

    # Titan must use the same model and dimensions that were used when the
    # document chunks were originally embedded.
    query_embedding = embed_text(cleaned_query)

    # pgvector calculates this expression inside PostgreSQL for each row.
    distance = DocumentChunkTable.embedding.cosine_distance(
        query_embedding
    )

    statement = (
        select(
            DocumentChunkTable.id.label("chunk_id"),
            DocumentTable.id.label("document_id"),
            DocumentTable.external_id,
            DocumentTable.title,
            DocumentChunkTable.content,
            DocumentTable.source_url,
            distance.label("distance"),
        )
        .join(
            DocumentTable,
            DocumentTable.id == DocumentChunkTable.document_id,
        )
        .where(
            DocumentTable.corpus_id == corpus_id,
            DocumentChunkTable.embedding.is_not(None),
            DocumentChunkTable.embedding_model == MODEL_ID,
        )
        .order_by(distance)
        .limit(limit)
    )

    rows = session.execute(statement).all()

    return [
        RetrievedChunk(
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            external_id=row.external_id,
            title=row.title,
            content=row.content,
            source_url=row.source_url,
            distance=float(row.distance),
        )
        for row in rows
    ]
