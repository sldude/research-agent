from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, func, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# define base template table class
class BaseTable(DeclarativeBase):
    pass

# define table to store papers and associated info
class PapersTable(BaseTable):
    __tablename__ = "papers"
    # primary key paper ID
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    authors: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # this column has list of mapped chunks associated with paper
    chunks: Mapped[list["DocumentChunkTable"]] = relationship(
        back_populates = "paper",
        cascade ="all, delete-orphan"
    )

# define document chunks table to store chunks for each paper
class DocumentChunkTable(BaseTable):
    __tablename__="documentchunks"
    # have each pair of paper_id and chunk_index be unique
    __table_args__ = (
        UniqueConstraint(
            "paper_id",
            "chunk_index",
            #name for constraint
            name = "uq_documentchunks_paper_chunk"
        ),
    )
    # chunk primary key id
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key = True,
    )
    # every chunk's paper id must refer to existing row in papers table
    paper_id:Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete = "CASCADE"),
        nullable = False,
        index = True,
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer, 
        nullable = False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable = False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone = True),
        server_default = func.now(),
        nullable = False
    )
    # this references the paper from papers table
    paper: Mapped["PapersTable"] = relationship(
        back_populates = "chunks"
    )
