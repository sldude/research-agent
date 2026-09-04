"""Pydantic schemas used at the API and external-service boundaries.

These classes validate Python data. They do not create DynamoDB tables or
indexes; the provisioning script is responsible for infrastructure.
"""

from datetime import date

from pydantic import BaseModel, Field


class ArxivPaper(BaseModel):
    """A normalized paper returned by the arXiv Atom API.

    Keeping this model independent from stored document records lets us inspect or
    display search results before deciding which papers should be saved.
    """

    external_id: str
    title: str
    abstract: str
    authors: list[str] = Field(default_factory=list)
    publication_date: date | None = None
    updated_date: date | None = None
    source_url: str
    categories: list[str] = Field(default_factory=list)
    license_url: str | None = None


class ArxivSearchRequest(BaseModel):
    """Input that a future FastAPI arXiv search endpoint can accept."""

    query: str = Field(min_length=1, max_length=300)
    start: int = Field(default=0, ge=0)
    max_results: int = Field(default=10, ge=1, le=100)


class ArxivSearchResponse(BaseModel):
    """Response shape returned to the frontend after an arXiv search."""

    query: str
    start: int
    count: int
    papers: list[ArxivPaper] = Field(default_factory=list)

# schema for chunk similarity ranking retrieval
class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    external_id: str | None
    title: str
    content: str
    source_url: str | None
    distance: float

class RagSource(BaseModel):
    """One database-backed source supplied to the generation model to answer post similarity retrieval."""

    number: int
    document_id: str
    external_id: str | None
    title: str
    source_url: str | None
    distance: float

class RagAnswer(BaseModel):
    """A generated answer with the sources supplied to the model."""

    question: str
    answer: str
    sources: list[RagSource] = Field(default_factory=list)


class CorpusResponse(BaseModel):
    """A corpus that a client can select for retrieval."""

    id: str
    name: str
    corpus_type: str
    owner_id: str | None


class RagQuestionRequest(BaseModel):
    """Input accepted by the RAG answer endpoint."""

    corpus_id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=5, ge=1, le=20)
    max_tokens: int = Field(default=500, ge=1, le=2_000)
