"""FastAPI entry point for the research-agent backend."""
import logging
import mimetypes
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import boto3
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from mangum import Mangum

from app.database.repository import DynamoRepository
from app.schemas.api_schemas import CorpusResponse, RagAnswer, RagQuestionRequest, CreateCorpusRequest, DocumentUploadRequest
from app.services.rag_service import answer_question
from app.upload_limits import MAX_UPLOAD_BYTES, UPLOAD_POST_EXPIRES_SECONDS

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

    repository = DynamoRepository()
    return [
        CorpusResponse(
            id=corpus.id,
            name=corpus.name,
            corpus_type=corpus.corpus_type,
            owner_id=corpus.owner_id,
            document_count=repository.count_documents(corpus.id, corpus.corpus_type),
        )
        for corpus in repository.list_corpora()
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

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}

@app.post("/api/corpora/{corpus_id}/documents", status_code=status.HTTP_201_CREATED,)
def upload_document(
    corpus_id: str,
    request: DocumentUploadRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Authorize an upload; the browser sends the actual file directly to S3."""
    repository = DynamoRepository()
    corpus = repository.get_corpus(corpus_id)

    if corpus is None:
        raise HTTPException(status_code=404, detail="Corpus not found.")

    if corpus.owner_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="You can only upload to a corpus you own.",
        )

    filename = request.filename
    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Supported files: PDF, TXT, and Markdown.",
        )

    if request.size_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="The file must be 5 MB (5,000,000 bytes) or smaller.",
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
    try:
        # Boto3 adds exact bucket/key conditions. S3 enforces the real file size,
        # independently of the size claimed by the browser.
        upload = s3.generate_presigned_post(
            Bucket=bucket,
            Key=object_key,
            Fields={"Content-Type": "application/octet-stream"},
            Conditions=[
                ["content-length-range", 1, MAX_UPLOAD_BYTES],
                {"Content-Type": "application/octet-stream"},
            ],
            ExpiresIn=UPLOAD_POST_EXPIRES_SECONDS,
        )
    except Exception as error:
        logger.exception("Could not authorize document upload: %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not authorize document upload. Please try again.",
        ) from error

    # Persist before returning the form so S3 events always find the metadata.
    # Signing happens first to avoid orphan records on signing failures.
    repository.create_document_status(
        corpus_id=corpus_id,
        document_id=document_id,
        owner_id=user_id,
        filename=filename,
        s3_bucket=bucket,
        s3_key=object_key,
    )

    return {
        "document_id": document_id,
        "corpus_id": corpus_id,
        "filename": filename,
        "status": "uploading",
        "upload_url": upload["url"],
        "fields": upload["fields"],
        "expires_in": UPLOAD_POST_EXPIRES_SECONDS,
    }


@app.get("/api/corpora/{corpus_id}/documents/{document_id}/status")
def get_document_status(
    corpus_id: str,
    document_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Report ingestion progress for a document owned by the caller."""
    document = DynamoRepository().get_document_status(
        corpus_id=corpus_id, document_id=document_id
    )
    if document is None or document["owner_id"] != user_id:
        raise HTTPException(status_code=404, detail="Document not found.")

    return {
        "document_id": document_id,
        "status": document["status"],
        "chunks_saved": document.get("chunks_saved"),
    }


def _document_for_owner(corpus_id: str, document_id: str, user_id: str):
    document = DynamoRepository().get_document_status(
        corpus_id=corpus_id, document_id=document_id
    )
    if document is None or document["owner_id"] != user_id:
        raise HTTPException(status_code=404, detail="Document not found.")
    return document


def _document_is_active(document: dict) -> bool:
    """Protect current ingestion while allowing stale records to be cleaned up."""
    if document["status"] == "processing":
        return document.get("lease_until", int(time.time()) + 1) > int(time.time())
    if document["status"] not in {"uploading", "uploaded"}:
        return False
    try:
        updated_at = datetime.fromisoformat(document["updated_at"])
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - updated_at).total_seconds() < 10 * 60
    except (KeyError, TypeError, ValueError):
        return True


@app.get("/api/corpora/{corpus_id}/documents/{document_id}/content")
def get_document_content(
    corpus_id: str,
    document_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Return a private uploaded file for an authenticated in-app preview."""
    document = _document_for_owner(corpus_id, document_id, user_id)
    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE"),
        region_name=os.getenv("AWS_REGION", "us-east-2"),
    )
    try:
        result = session.client("s3").get_object(
            Bucket=document["s3_bucket"], Key=document["s3_key"]
        )
    except Exception as error:
        logger.exception("Document preview failed: %s", document_id)
        raise HTTPException(status_code=502, detail="Could not load document.") from error

    media_type = mimetypes.guess_type(document["filename"])[0] or "application/octet-stream"
    safe_name = document["filename"].replace('"', "")
    return Response(
        content=result["Body"].read(),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@app.delete("/api/corpora/{corpus_id}/documents/{document_id}", status_code=204)
def delete_document(
    corpus_id: str,
    document_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Delete a finished upload, its file, and all indexed chunks."""
    repository = DynamoRepository()
    document = repository.get_document_status(
        corpus_id=corpus_id, document_id=document_id
    )
    if document is None or document["owner_id"] != user_id:
        raise HTTPException(status_code=404, detail="Document not found.")
    if _document_is_active(document):
        raise HTTPException(
            status_code=409,
            detail="Wait for recent document processing to finish before deleting it.",
        )

    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE"),
        region_name=os.getenv("AWS_REGION", "us-east-2"),
    )
    try:
        session.client("s3").delete_object(
            Bucket=document["s3_bucket"], Key=document["s3_key"]
        )
        repository.delete_document(corpus_id=corpus_id, document_id=document_id)
    except Exception as error:
        logger.exception("Document deletion failed: %s", document_id)
        raise HTTPException(status_code=502, detail="Could not delete document.") from error
    return Response(status_code=204)


@app.delete("/api/corpora/{corpus_id}", status_code=204)
def delete_corpus(
    corpus_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Delete a user-owned corpus and all of its stored documents."""
    repository = DynamoRepository()
    corpus = repository.get_corpus(corpus_id)
    if corpus is None or corpus.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Corpus not found.")

    documents = repository.list_document_statuses(corpus_id)
    document_metadata = [
        repository.get_document_status(
            corpus_id=corpus_id, document_id=document["document_id"]
        )
        for document in documents
    ]
    if any(
        document is not None and _document_is_active(document)
        for document in document_metadata
    ):
        raise HTTPException(
            status_code=409,
            detail="Wait for recent document processing to finish before deleting this corpus.",
        )

    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE"),
        region_name=os.getenv("AWS_REGION", "us-east-2"),
    )
    s3 = session.client("s3")
    try:
        for document in document_metadata:
            if document is not None:
                s3.delete_object(
                    Bucket=document["s3_bucket"], Key=document["s3_key"]
                )
        repository.delete_corpus(corpus_id)
    except Exception as error:
        logger.exception("Corpus deletion failed: %s", corpus_id)
        raise HTTPException(status_code=502, detail="Could not delete corpus.") from error
    return Response(status_code=204)


@app.post("/api/corpora", response_model=CorpusResponse)
def create_corpus(
    request: CreateCorpusRequest,
    user_id: str = Depends(get_current_user_id),
) -> CorpusResponse:
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Enter a corpus name.")

    repository = DynamoRepository()
    corpus = repository.get_or_create_corpus(
        name=name,
        corpus_type="user_upload",
        owner_id=user_id,
    )

    return CorpusResponse(
        id=corpus.id,
        name=corpus.name,
        corpus_type=corpus.corpus_type,
        owner_id=corpus.owner_id,
        document_count=repository.count_documents(corpus.id, corpus.corpus_type),
    )

# get listed documents within corpus belonging to user
@app.get("/api/corpora/{corpus_id}/documents")
def list_documents(
    corpus_id: str,
    user_id: str = Depends(get_current_user_id),
):
    repository = DynamoRepository()
    corpus = repository.get_corpus(corpus_id)

    if corpus is None or corpus.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Corpus not found.")

    return repository.list_document_statuses(corpus_id)

# API Gateway sends an event to Lambda rather than an ordinary ASGI request.
# Mangum translates that event into the ASGI format expected by FastAPI.
handler = Mangum(app, lifespan="off")
