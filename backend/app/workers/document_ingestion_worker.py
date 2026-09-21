"""Process uploaded documents in response to direct S3 notifications."""

import logging
import math
import os
import time
from pathlib import PurePosixPath
from urllib.parse import unquote_plus
from uuid import uuid4

import boto3

from app.database.repository import DynamoRepository
from app.services.document_ingestion import ingest_document


logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 3 * 1024 * 1024


def handler(event, context):
    """Process S3 uploads, skipping documents already marked ready."""

    # S3 sends a different event shape when testing a notification.
    if event.get("Event") == "s3:TestEvent":
        return

    repository = DynamoRepository()
    s3 = boto3.client("s3")
    expected_bucket = os.environ["DOCUMENT_UPLOAD_BUCKET"]

    for record in event["Records"]:
        if record.get("eventSource") != "aws:s3":
            continue

        if not record.get("eventName", "").startswith("ObjectCreated:"):
            continue

        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        if bucket != expected_bucket:
            raise ValueError("Unexpected upload bucket.")

        # Expected: uploads/{owner_id}/{corpus_id}/{document_id}.ext
        parts = key.split("/")
        if len(parts) != 4 or parts[0] != "uploads":
            raise ValueError("Unexpected upload key.")

        _, owner_id, corpus_id, object_name = parts
        document_id = PurePosixPath(object_name).stem

        document = repository.get_document_status(
            corpus_id=corpus_id,
            document_id=document_id,
        )

        if document is None:
            raise ValueError("Document status record not found.")

        if (
            document["owner_id"] != owner_id
            or document["s3_bucket"] != bucket
            or document["s3_key"] != key
        ):
            raise ValueError("Upload does not match its document record.")

        if document["status"] == "ready":
            continue

        attempt_id = str(uuid4())

        # The lease outlasts the remaining lifetime of this invocation.
        lease_until = (
            int(time.time())
            + math.ceil(context.get_remaining_time_in_millis() / 1000)
            + 30
        )

        # This method must use an atomic conditional DynamoDB update.
        claim = repository.claim_document_processing(
            corpus_id=corpus_id,
            document_id=document_id,
            lease_owner=attempt_id,
            lease_until=lease_until,
        )

        if claim == "ready":
            continue

        if claim != "claimed":
            # Raise so the event can be retried rather than silently lost.
            raise RuntimeError("Document is being processed by another worker.")

        try:
            response = s3.get_object(Bucket=bucket, Key=key)
            body = response["Body"]
            try:
                contents = body.read(MAX_UPLOAD_BYTES + 1)
            finally:
                body.close()

            if len(contents) > MAX_UPLOAD_BYTES:
                raise ValueError("Document exceeds the upload size limit.")

            result = ingest_document(
                corpus_id=corpus_id,
                document_id=document_id,
                owner_id=owner_id,
                filename=document["filename"],
                contents=contents,
                repository=repository,
            )

            repository.mark_document_ready(
                corpus_id=corpus_id,
                document_id=document_id,
                lease_owner=attempt_id,
                chunks_saved=result["chunks_saved"],
            )

        except Exception:
            logger.exception("Document ingestion failed: %s", document_id)

            try:
                repository.mark_document_failed(
                    corpus_id=corpus_id,
                    document_id=document_id,
                    lease_owner=attempt_id,
                )
            except Exception:
                logger.exception(
                    "Could not record ingestion failure: %s", document_id
                )

            # Let Lambda's asynchronous retry handling see the failure.
            raise