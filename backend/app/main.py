"""FastAPI entry point for the research-agent backend."""

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from mangum import Mangum

from app.database.repository import DynamoRepository
from app.schemas.api_schemas import CorpusResponse, RagAnswer, RagQuestionRequest
from app.services.rag_service import answer_question


app = FastAPI(
    title="Research Agent API",
    description="Retrieve research abstracts and generate grounded answers.",
    version="0.2.0",
)


def get_current_user_id(request: Request) -> str:
    """Return the stable Cognito user ID from API Gateway's validated JWT.

    API Gateway validates the token before invoking Lambda. Mangum preserves
    the original API Gateway event in the ASGI scope so the application can use
    its claims for per-user authorization.
    """

    event = request.scope.get("aws.event", {})
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user information is missing.",
        )
    return user_id


@app.get("/health")
def health_check() -> dict[str, str]:
    """Confirm that the API process is running without calling AWS."""

    return {"status": "ok"}


@app.options("/{path:path}", include_in_schema=False)
def cors_preflight(path: str) -> Response:
    """Accept API Gateway's unauthenticated browser CORS preflight request."""

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/corpora", response_model=list[CorpusResponse])
def list_corpora(user_id: str = Depends(get_current_user_id)) -> list[CorpusResponse]:
    """Return shared corpora and private corpora owned by the current user."""

    return [
        CorpusResponse(
            id=corpus.id,
            name=corpus.name,
            corpus_type=corpus.corpus_type,
            owner_id=corpus.owner_id,
        )
        for corpus in DynamoRepository().list_corpora()
        if corpus.owner_id is None or corpus.owner_id == user_id
    ]


@app.post("/api/rag/answer", response_model=RagAnswer)
def generate_rag_answer(
    request: RagQuestionRequest,
    user_id: str = Depends(get_current_user_id),
) -> RagAnswer:
    """Answer from a shared corpus or one owned by the current user."""

    repository = DynamoRepository()
    corpus = repository.get_corpus(request.corpus_id)
    if corpus is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Corpus {request.corpus_id} was not found.",
        )
    if corpus.owner_id is not None and corpus.owner_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this corpus.",
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
