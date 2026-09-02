"""Pydantic schemas used at the API and external-service boundaries.

These classes validate Python data. They do not create database tables; the
SQLAlchemy models in ``database_tables.py`` are responsible for persistence.
"""

from datetime import date

from pydantic import BaseModel, Field


class ArxivPaper(BaseModel):
    """A normalized paper returned by the arXiv Atom API.

    Keeping this model independent from ``DocumentTable`` lets us inspect or
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
    chunk_id: int
    document_id: int
    external_id: str | None
    title: str
    content: str
    source_url: str | None
    distance: float