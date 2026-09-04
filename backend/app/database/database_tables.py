"""Application records stored in DynamoDB.

DynamoDB does not require ORM table classes or schema migrations. These
dataclasses provide typed records for the service and API layers.
"""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class CorpusRecord:
    id: str
    name: str
    corpus_type: str
    owner_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class DocumentRecord:
    id: str
    corpus_id: str
    chunk_id: str
    source: str
    external_id: str | None
    title: str
    abstract: str | None
    authors: list[str]
    publication_date: date | None
    source_url: str | None
    license_url: str | None
    content: str
    embedding_model: str | None
    embedding_dimensions: int | None
    created_at: datetime
