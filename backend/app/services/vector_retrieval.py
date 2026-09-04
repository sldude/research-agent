"""Retrieve document chunks using DynamoDB cosine vector search."""

from app.clients.embeddings import MODEL_ID, embed_text
from app.database.repository import DynamoRepository
from app.schemas.api_schemas import RetrievedChunk


def retrieve_similar_chunks(
    *,
    corpus_id: str,
    query: str,
    limit: int = 5,
    repository: DynamoRepository | None = None,
) -> list[RetrievedChunk]:
    """Return the chunks most semantically similar to a question."""

    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("query cannot be empty")
    if not corpus_id.strip():
        raise ValueError("corpus_id cannot be empty")
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")

    query_embedding = embed_text(cleaned_query)
    records = (repository or DynamoRepository()).search_documents(
        corpus_id=corpus_id,
        embedding=query_embedding,
        embedding_model=MODEL_ID,
        limit=limit,
    )
    return [
        RetrievedChunk(
            chunk_id=document.chunk_id,
            document_id=document.id,
            external_id=document.external_id,
            title=document.title,
            content=document.content,
            source_url=document.source_url,
            distance=score,
        )
        for document, score in records
    ]
