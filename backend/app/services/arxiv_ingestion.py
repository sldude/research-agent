"""Store normalized arXiv metadata and abstract embeddings in DynamoDB."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.clients.embeddings import DIMENSIONS, MODEL_ID, embed_text
from app.database.database_tables import CorpusRecord, DocumentRecord
from app.database.repository import DynamoRepository
from app.schemas.api_schemas import ArxivPaper


EmbeddingFunction = Callable[[str], list[float]]
IngestionStatus = Literal["created", "updated", "unchanged"]


@dataclass
class PaperIngestionResult:
    """Outcome for one paper processed by the ingestion service."""

    document: DocumentRecord
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
    def documents(self) -> list[DocumentRecord]:
        return [result.document for result in self.results]

    @property
    def processed(self) -> int:
        return len(self.results)


def get_or_create_arxiv_corpus(
    *,
    name: str = "arXiv abstracts",
    owner_id: str | None = None,
    repository: DynamoRepository | None = None,
) -> CorpusRecord:
    """Return a matching corpus, creating it when it does not exist."""

    return (repository or DynamoRepository()).get_or_create_corpus(
        name=name,
        corpus_type="research_abstract",
        owner_id=owner_id,
    )


def save_arxiv_paper(
    *,
    corpus: CorpusRecord,
    paper: ArxivPaper,
    repository: DynamoRepository,
    embedding_function: EmbeddingFunction = embed_text,
) -> PaperIngestionResult:
    """Insert or update one arXiv paper and its abstract embedding."""

    existing = repository.get_document(
        corpus_id=corpus.id,
        source="arxiv",
        external_id=paper.external_id,
    )
    chunk_content = f"{paper.title}\n\n{paper.abstract}".strip()
    embedding_is_current = (
        existing is not None
        and existing.content == chunk_content
        and existing.embedding_model == MODEL_ID
        and existing.embedding_dimensions == DIMENSIONS
    )

    metadata_changed = existing is None or any(
        (
            existing.title != paper.title,
            existing.abstract != paper.abstract,
            existing.authors != paper.authors,
            existing.publication_date != paper.publication_date,
            existing.source_url != paper.source_url,
            existing.license_url != paper.license_url,
        )
    )

    if embedding_is_current:
        # Avoid rewriting an unchanged item. Rewriting the vector would incur
        # DynamoDB vector-write charges even without another Bedrock call.
        if not metadata_changed:
            return PaperIngestionResult(
                document=existing,
                status="unchanged",
                bedrock_called=False,
            )
        # Metadata and embedded content move together for arXiv abstracts, so
        # this branch is unusual but retains the stored vector when possible.
        embedding = repository.get_embedding(
            corpus_id=corpus.id,
            chunk_id=existing.chunk_id,
        )
        bedrock_called = False
    else:
        embedding = embedding_function(chunk_content)
        if len(embedding) != DIMENSIONS:
            raise ValueError(
                f"Expected a {DIMENSIONS}-dimension embedding, got {len(embedding)}"
            )
        bedrock_called = True

    document = repository.put_document(
        corpus_id=corpus.id,
        source="arxiv",
        external_id=paper.external_id,
        title=paper.title,
        abstract=paper.abstract,
        authors=paper.authors,
        publication_date=paper.publication_date,
        source_url=paper.source_url,
        license_url=paper.license_url,
        content=chunk_content,
        embedding=embedding,
        embedding_model=MODEL_ID,
        created_at=existing.created_at if existing else None,
    )
    return PaperIngestionResult(
        document=document,
        status="created" if existing is None else "updated",
        bedrock_called=bedrock_called,
    )


def ingest_arxiv_papers(
    papers: Sequence[ArxivPaper],
    *,
    corpus_name: str = "arXiv abstracts",
    owner_id: str | None = None,
    embedding_function: EmbeddingFunction = embed_text,
    repository: DynamoRepository | None = None,
) -> ArxivIngestionStats:
    """Store several papers and return per-paper and summary outcomes."""

    active_repository = repository or DynamoRepository()
    corpus = get_or_create_arxiv_corpus(
        name=corpus_name,
        owner_id=owner_id,
        repository=active_repository,
    )
    results = [
        save_arxiv_paper(
            corpus=corpus,
            paper=paper,
            repository=active_repository,
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
