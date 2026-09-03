"""Store normalized arXiv metadata and abstract embeddings in PostgreSQL."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.embeddings import DIMENSIONS, MODEL_ID, embed_text
from app.database.database_tables import CorpusTable, DocumentChunkTable, DocumentTable
from app.schemas.api_schemas import ArxivPaper


# Accepting an embedding function as an argument makes this service testable
# without calling Bedrock. In normal use it defaults to the real Titan function.
EmbeddingFunction = Callable[[str], list[float]]
IngestionStatus = Literal["created", "updated", "unchanged"]


@dataclass
class PaperIngestionResult:
    """Outcome for one paper processed by the ingestion service."""

    document: DocumentTable
    status: IngestionStatus
    bedrock_called: bool


@dataclass
class ArxivIngestionStats:
    """Documents and summary counts produced by one ingestion batch."""

    results: list[PaperIngestionResult]
    created: int
    updated: int
    unchanged: int
    bedrock_calls: int

    @property
    def documents(self) -> list[DocumentTable]:
        """Provide convenient access to all processed document objects."""

        return [result.document for result in self.results]

    @property
    def processed(self) -> int:
        """Return the total number of papers handled in this batch."""

        return len(self.results)


def get_or_create_arxiv_corpus(
    session: Session,
    *,
    name: str = "arXiv abstracts",
    owner_id: str | None = None,
) -> CorpusTable:
    """Return a matching corpus, creating it when it does not exist."""

    corpus = session.scalar(
        select(CorpusTable).where(
            CorpusTable.name == name,
            CorpusTable.corpus_type == "research_abstract",
            CorpusTable.owner_id == owner_id,
        )
    )
    if corpus is not None:
        return corpus

    corpus = CorpusTable(
        name=name,
        corpus_type="research_abstract",
        owner_id=owner_id,
    )
    session.add(corpus)

    # Flush sends the INSERT without committing, which assigns corpus.id and
    # keeps transaction control with the caller.
    session.flush()
    return corpus


def save_arxiv_paper(
    session: Session,
    *,
    corpus: CorpusTable,
    paper: ArxivPaper,
    embedding_function: EmbeddingFunction = embed_text,
) -> PaperIngestionResult:
    """Insert or update one arXiv paper and its single abstract chunk."""

    document = session.scalar(
        select(DocumentTable).where(
            DocumentTable.corpus_id == corpus.id,
            DocumentTable.source == "arxiv",
            DocumentTable.external_id == paper.external_id,
        )
    )

    document_is_new = document is None
    if document_is_new:
        document = DocumentTable(
            corpus=corpus,
            source="arxiv",
            external_id=paper.external_id,
            title=paper.title,
        )
        session.add(document)

    authors_json = json.dumps(paper.authors, ensure_ascii=False)
    metadata_changed = document_is_new or any(
        (
            document.title != paper.title,
            document.abstract != paper.abstract,
            document.authors != authors_json,
            document.publication_date != paper.publication_date,
            document.source_url != paper.source_url,
            document.license_url != paper.license_url,
        )
    )

    # Refresh mutable metadata when arXiv reports a newer paper version.
    document.title = paper.title
    document.abstract = paper.abstract
    document.authors = authors_json
    document.publication_date = paper.publication_date
    document.source_url = paper.source_url
    document.license_url = paper.license_url

    # Include the title in the embedded text because it often contains the
    # clearest description of the paper's subject. No PDF is downloaded.
    chunk_content = f"{paper.title}\n\n{paper.abstract}".strip()

    chunk = next(
        (existing for existing in document.chunks if existing.chunk_index == 0),
        None,
    )
    if chunk is None:
        chunk = DocumentChunkTable(chunk_index=0)
        document.chunks.append(chunk)

    # Reuse the existing vector when the embedded text, model, and dimensions
    # are unchanged. This prevents duplicate imports from making paid Bedrock
    # calls while still allowing changed abstracts or model upgrades to be
    # re-embedded automatically.
    embedding_is_current = (
        chunk.content == chunk_content
        and chunk.embedding is not None
        and len(chunk.embedding) == DIMENSIONS
        and chunk.embedding_model == MODEL_ID
    )

    if not embedding_is_current:
        chunk.embedding = embedding_function(chunk_content)
        chunk.embedding_model = MODEL_ID

    chunk.content = chunk_content
    chunk.start_offset = 0
    chunk.end_offset = len(chunk_content)

    session.flush()

    if document_is_new:
        status: IngestionStatus = "created"
    elif metadata_changed or not embedding_is_current:
        status = "updated"
    else:
        status = "unchanged"

    return PaperIngestionResult(
        document=document,
        status=status,
        bedrock_called=not embedding_is_current,
    )


def ingest_arxiv_papers(
    session: Session,
    papers: Sequence[ArxivPaper],
    *,
    corpus_name: str = "arXiv abstracts",
    owner_id: str | None = None,
    embedding_function: EmbeddingFunction = embed_text,
) -> ArxivIngestionStats:
    """Store several papers in one corpus without committing the transaction."""

    corpus = get_or_create_arxiv_corpus(
        session,
        name=corpus_name,
        owner_id=owner_id,
    )

    results = [
        save_arxiv_paper(
            session,
            corpus=corpus,
            paper=paper,
            embedding_function=embedding_function,
        )
        for paper in papers
    ]

    return ArxivIngestionStats(
        results=results,
        created=sum(result.status == "created" for result in results),
        updated=sum(result.status == "updated" for result in results),
        unchanged=sum(result.status == "unchanged" for result in results),
        bedrock_calls=sum(result.bedrock_called for result in results),
    )
