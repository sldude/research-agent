from sqlalchemy import select, func
from app.database_connect import SessionLocal
from app.database_tables import PapersTable, DocumentChunkTable


from uuid import uuid4

def delete_all_papers() -> None:
    with SessionLocal() as session:
        papers = session.scalars(
            select(PapersTable)
        ).all()

        for paper in papers:
            session.delete(paper)

        session.commit()

        print(f"Deleted {len(papers)} papers")

def test_paper_chunks() -> None:
    external_id = f"test-paper-{uuid4()}"

    with SessionLocal() as session:
        # Create a paper and attach two chunks.
        paper = PapersTable(
            external_id=external_id,
            source="manual",
            title="Paper and Chunk Test",
            abstract="Testing the one-to-many relationship.",
            authors="Steven Liu",
        )

        paper.chunks.append(
            DocumentChunkTable(
                chunk_index=0,
                content="This is the first chunk.",
            )
        )

        paper.chunks.append(
            DocumentChunkTable(
                chunk_index=1,
                content="This is the second chunk.",
            )
        )

        # Cascade saves the paper and both chunks.
        session.add(paper)
        session.commit()

        paper_id = paper.id

        print(f"Created paper ID: {paper_id}")
        print(f"Created {len(paper.chunks)} chunks")

        # Retrieve the paper from the database.
        saved_paper = session.scalar(
            select(PapersTable).where(
                PapersTable.id == paper_id
            )
        )

        assert saved_paper is not None
        assert len(saved_paper.chunks) == 2

        print(f"Retrieved paper: {saved_paper.title}")

        for chunk in saved_paper.chunks:
            print(
                f"Chunk {chunk.chunk_index}: "
                f"{chunk.content}"
            )

            # Test navigation from chunk back to paper.
            assert chunk.paper.id == saved_paper.id

        # Delete the paper to test cascade deletion.
        session.delete(saved_paper)
        session.commit()

        remaining_chunks = session.scalar(
            select(func.count())
            .select_from(DocumentChunkTable)
            .where(DocumentChunkTable.paper_id == paper_id)
        )

        assert remaining_chunks == 0

        print("Paper deleted")
        print("Related chunks deleted successfully")


if __name__ == "__main__":
    delete_all_papers()
    test_paper_chunks()