"""revised database schema for generalized abstract and user document upload corpuses

Revision ID: c03c177d09ff
Revises: cb09d5a09b40
Create Date: 2026-09-02 12:15:38.970988

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = 'c03c177d09ff'
down_revision: Union[str, Sequence[str], None] = 'cb09d5a09b40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('corpora',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('corpus_type', sa.String(length=30), nullable=False),
    sa.Column('owner_id', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_corpora_owner_id'), 'corpora', ['owner_id'], unique=False)
    op.create_table('documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('corpus_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=30), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=True),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('abstract', sa.Text(), nullable=True),
    sa.Column('authors', sa.Text(), nullable=True),
    sa.Column('publication_date', sa.Date(), nullable=True),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('license_url', sa.Text(), nullable=True),
    sa.Column('original_filename', sa.Text(), nullable=True),
    sa.Column('storage_key', sa.Text(), nullable=True),
    sa.Column('content_type', sa.String(length=100), nullable=True),
    sa.Column('checksum', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['corpus_id'], ['corpora.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('corpus_id', 'source', 'external_id', name='uq_documents_corpus_source_external_id')
    )
    op.create_index(op.f('ix_documents_corpus_id'), 'documents', ['corpus_id'], unique=False)

    # The old tables contain test data only. Rebuilding the child table is safer
    # than trying to populate a new non-null document_id on existing rows.
    op.drop_table('documentchunks')
    op.drop_table('papers')

    op.create_table('documentchunks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding', Vector(dim=1024), nullable=True),
    sa.Column('embedding_model', sa.String(length=100), nullable=True),
    sa.Column('start_offset', sa.Integer(), nullable=True),
    sa.Column('end_offset', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'chunk_index', name='uq_documentchunks_document_chunk')
    )
    op.create_index(op.f('ix_documentchunks_document_id'), 'documentchunks', ['document_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('documentchunks')
    op.drop_table('documents')
    op.drop_table('corpora')

    op.create_table('papers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=100), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('abstract', sa.Text(), nullable=True),
    sa.Column('authors', sa.Text(), nullable=True),
    sa.Column('publication_date', sa.Date(), nullable=True),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('external_id')
    )
    op.create_table('documentchunks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('paper_id', sa.Integer(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding', Vector(dim=1024), nullable=True),
    sa.Column('embedding_model', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('paper_id', 'chunk_index', name='uq_documentchunks_paper_chunk')
    )
    op.create_index(op.f('ix_documentchunks_paper_id'), 'documentchunks', ['paper_id'], unique=False)
