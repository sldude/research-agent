"""FastAPI entry point for the research-agent backend."""
import logging
import os
from pathlib import Path
from uuid import uuid4
import boto3
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status, UploadFile
from mangum import Mangum

from app.database.repository import DynamoRepository
from app.schemas.api_schemas import CorpusResponse, RagAnswer, RagQuestionRequest, CreateCorpusRequest
from app.services.rag_service import answer_question

logger = logging.getLogger(__name__)

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

MAX_UPLOAD_BYTES = 3 * 1024 * 1024  # Initial application limit: 3 MiB
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}

@app.post("/api/corpora/{corpus_id}/documents", status_code=status.HTTP_201_CREATED,)
def upload_document(
    corpus_id: str,
    file: UploadFile,
    user_id: str = Depends(get_current_user_id),
):
    repository = DynamoRepository()
    corpus = repository.get_corpus(corpus_id)

    if corpus is None:
        raise HTTPException(status_code=404, detail="Corpus not found.")

    if corpus.owner_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="You can only upload to a corpus you own.",
        )

    filename = file.filename or "document"
    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Supported files: PDF, TXT, and Markdown.",
        )

    # Read at most the limit plus one byte to detect oversized files.
    contents = file.file.read(MAX_UPLOAD_BYTES + 1)

    if not contents:
        raise HTTPException(status_code=400, detail="The file is empty.")

    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="The file must be 3 MiB or smaller.",
        )

    bucket = os.getenv("DOCUMENT_UPLOAD_BUCKET")
    if not bucket:
        raise HTTPException(
            status_code=503,
            detail="Document storage is not configured.",
        )

    document_id = str(uuid4())
    object_key = (
        f"uploads/{user_id}/{corpus_id}/{document_id}{extension}"
    )

    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE"),
        region_name=os.getenv("AWS_REGION", "us-east-2"),
    )
    s3 = session.client("s3")
    # Create metadata before S3 can notify the ingestion worker.
    repository.create_document_status(
        corpus_id=corpus_id,
        document_id=document_id,
        owner_id=user_id,
        filename=filename,
        s3_bucket=bucket,
        s3_key=object_key,
    )

    try:
        s3.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=contents,
            ContentType="application/octet-stream",
        )
    except Exception as error:
        logger.exception("Document upload failed: %s", document_id)
        try:
            repository.finish_document_upload(
                corpus_id=corpus_id,
                document_id=document_id,
                succeeded=False,
            )
        except Exception:
            logger.exception("Could not record upload failure: %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document upload failed. Please try again.",
        ) from error

    repository.finish_document_upload(
        corpus_id=corpus_id,
        document_id=document_id,
        succeeded=True,
    )

    return {
        "document_id": document_id,
        "corpus_id": corpus_id,
        "filename": filename,
        "size_bytes": len(contents),
        "status": "uploaded",
    }


@app.post("/api/corpora", response_model=CorpusResponse)
def create_corpus(
    request: CreateCorpusRequest,
    user_id: str = Depends(get_current_user_id),
) -> CorpusResponse:
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Enter a corpus name.")

    corpus = DynamoRepository().get_or_create_corpus(
        name=name,
        corpus_type="user_upload",
        owner_id=user_id,
    )

    return CorpusResponse(
        id=corpus.id,
        name=corpus.name,
        corpus_type=corpus.corpus_type,
        owner_id=corpus.owner_id,
    )

# API Gateway sends an event to Lambda rather than an ordinary ASGI request.
# Mangum translates that event into the ASGI format expected by FastAPI.
handler = Mangum(app, lifespan="off")
