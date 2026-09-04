"""FastAPI entry point for the research-agent backend."""

from fastapi import FastAPI, HTTPException, status
from mangum import Mangum

from app.database.repository import DynamoRepository
from app.schemas.api_schemas import CorpusResponse, RagAnswer, RagQuestionRequest
from app.services.rag_service import answer_question


app = FastAPI(
    title="Research Agent API",
    description="Retrieve research abstracts and generate grounded answers.",
    version="0.2.0",
)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Confirm that the API process is running without calling AWS."""

    return {"status": "ok"}


@app.get("/api/corpora", response_model=list[CorpusResponse])
def list_corpora() -> list[CorpusResponse]:
    """Return corpora that can be selected for a RAG request."""

    return [
        CorpusResponse(
            id=corpus.id,
            name=corpus.name,
            corpus_type=corpus.corpus_type,
            owner_id=corpus.owner_id,
        )
        for corpus in DynamoRepository().list_corpora()
    ]


@app.post("/api/rag/answer", response_model=RagAnswer)
def generate_rag_answer(request: RagQuestionRequest) -> RagAnswer:
    """Answer a question using embedded chunks from one selected corpus."""

    repository = DynamoRepository()
    if repository.get_corpus(request.corpus_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Corpus {request.corpus_id} was not found.",
        )

    return answer_question(
        corpus_id=request.corpus_id,
        question=request.question,
        limit=request.limit,
        max_tokens=request.max_tokens,
        repository=repository,
    )


# API Gateway sends an event to Lambda rather than an ordinary ASGI request.
# Mangum translates that event into the ASGI format expected by FastAPI.
handler = Mangum(app, lifespan="off")
