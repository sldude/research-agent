"""Pydantic schemas used at the API and external-service boundaries.

These classes validate Python data. They do not create DynamoDB tables or
indexes; the provisioning script is responsible for infrastructure.
"""

from datetime import date
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    distance: float | None

class RagSource(BaseModel):
    """One database-backed source supplied to the generation model to answer post similarity retrieval."""

    number: int = Field(strict=True, gt=0)
    document_id: str
    external_id: str | None
    title: str
    source_url: str | None
    distance: float | None
    reference_type: Literal["cited", "additional"] = "cited"


def citation_numbers(answer: str) -> set[int]:
    """Read the supported [n] notation, rejecting ambiguous numeric brackets."""
    numbers = set()
    for value in re.findall(r"\[([^\[\]\n]+)\]", answer):
        if re.match(r"\s*\d", value):
            if not re.fullmatch(r"[1-9][0-9]*", value):
                raise ValueError("Citations must use separate positive [n] source IDs")
            numbers.add(int(value))
    return numbers


class GeneratedRagAnswer(BaseModel):
    """Model-controlled prose and selections; metadata comes from storage only."""

    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1)
    requested_source_count: int | None = Field(strict=True, gt=0)
    additional_source_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selections(self):
        ids = self.additional_source_ids
        if any(type(number) is not int or number <= 0 for number in ids):
            raise ValueError("Additional source IDs must be positive integers")
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate additional source IDs")
        cited = citation_numbers(self.answer)
        if cited.intersection(ids):
            raise ValueError("Cited sources cannot also be additional sources")
        if self.requested_source_count is None and ids:
            raise ValueError("Additional sources require a source-count request")
        if self.requested_source_count is not None and len(cited) + len(ids) > self.requested_source_count:
            raise ValueError("Source list exceeds the requested count")
        return self

class RagAnswer(BaseModel):
    """An answer with cited sources and explicitly requested relevant extras."""

    question: str
    answer: str
    sources: list[RagSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self):
        numbers = [source.number for source in self.sources]
        documents = [source.document_id for source in self.sources]
        if len(numbers) != len(set(numbers)) or len(documents) != len(set(documents)):
            raise ValueError("Duplicate source IDs or documents")
        cited = {source.number for source in self.sources if source.reference_type == "cited"}
        if citation_numbers(self.answer) != cited:
            raise ValueError("Inline citations must match the cited source list")
        return self


class CorpusResponse(BaseModel):
    """A corpus that a client can select for retrieval."""

    id: str
    name: str
    corpus_type: str
    owner_id: str | None
    document_count: int = Field(default=0, ge=0)


class RagQuestionRequest(BaseModel):
    """Input accepted by the RAG answer endpoint."""

    corpus_id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=5, ge=1, le=20)
    max_tokens: int = Field(default=500, ge=1, le=2_000)

class CreateCorpusRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class DocumentUploadRequest(BaseModel):
    """File metadata used to authorize a direct upload to S3."""

    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(strict=True, gt=0)
