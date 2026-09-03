"""FastAPI entry point for the research-agent backend."""

from collections.abc import Generator

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.database_connect import SessionLocal
from app.database.database_tables import CorpusTable
from app.schemas.api_schemas import CorpusResponse, RagAnswer, RagQuestionRequest
from app.services.rag_service import answer_question


app = FastAPI(
    title="Research Agent API",
    description="Retrieve research abstracts and generate grounded answers.",
    version="0.1.0",
)


def get_database_session() -> Generator[Session, None, None]:
    """Give one SQLAlchemy session to a request and always close it."""

    with SessionLocal() as session:
        yield session


@app.get("/health")
def health_check() -> dict[str, str]:
    """Confirm that the API process is running without calling AWS or RDS."""

    return {"status": "ok"}


@app.get("/api/corpora", response_model=list[CorpusResponse])
def list_corpora(
    session: Session = Depends(get_database_session),
) -> list[CorpusResponse]:
    """Return corpora that can be selected for a RAG request."""

    corpora = session.scalars(select(CorpusTable).order_by(CorpusTable.id)).all()
    return [
        CorpusResponse(
            id=corpus.id,
            name=corpus.name,
            corpus_type=corpus.corpus_type,
            owner_id=corpus.owner_id,
        )
        for corpus in corpora
    ]


@app.post("/api/rag/answer", response_model=RagAnswer)
def generate_rag_answer(
    request: RagQuestionRequest,
    session: Session = Depends(get_database_session),
) -> RagAnswer:
    """Answer a question using embedded chunks from one selected corpus."""

    corpus = session.get(CorpusTable, request.corpus_id)
    if corpus is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Corpus {request.corpus_id} was not found.",
        )

    return answer_question(
        session,
        corpus_id=request.corpus_id,
        question=request.question,
        limit=request.limit,
        max_tokens=request.max_tokens,
    )
