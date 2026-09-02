from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, func, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

# define base template table class
class BaseTable(DeclarativeBase):
    pass


# define corpus table consisting of user-uploaded documents and related research abstracts
class CorpusTable(BaseTable):
    __tablename__ = "corpora"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    # corpus type can be research_abstract or user_upload for user-uploaded paper/document
    corpus_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    owner_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    documents: Mapped[list["DocumentTable"]] = relationship(
        back_populates="corpus",
        cascade="all, delete-orphan",
    )


# define table to store papers and associated info
class DocumentTable(BaseTable):
    __tablename__ = "documents"

    __table_args__ = (
        UniqueConstraint(
            "corpus_id",
            "source",
            "external_id",
            name="uq_documents_corpus_source_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    corpus_id: Mapped[int] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    external_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    abstract: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    authors: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    publication_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    source_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    license_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    original_filename: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    storage_key: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    content_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    checksum: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    corpus: Mapped["CorpusTable"] = relationship(
        back_populates="documents",
    )
    chunks: Mapped[list["DocumentChunkTable"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


# Define chunks belonging to either an abstract or an uploaded document.
class DocumentChunkTable(BaseTable):
    __tablename__ = "documentchunks"
    # Each chunk index is unique within its parent document.
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_documentchunks_document_chunk",
        ),
    )
    # chunk primary key id
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key = True,
    )
    # Every chunk must refer to an existing document.
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer, 
        nullable = False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable = False,
    )
    # per-chunk embedding
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024),
        nullable=True,
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable = True
    )
    # Optional character offsets locate the chunk in extracted document text.
    start_offset: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    end_offset: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone = True),
        server_default = func.now(),
        nullable = False
    )
    document: Mapped["DocumentTable"] = relationship(
        back_populates="chunks",
    )
