"""DynamoDB persistence operations for corpora and embedded document chunks."""

from datetime import date, datetime, timezone
from typing import Any
import random
import time
from uuid import NAMESPACE_URL, uuid5

from botocore.exceptions import ClientError

from app.database.database_connect import (
    DYNAMODB_CHUNKS_TABLE,
    DYNAMODB_CORPORA_TABLE,
    DYNAMODB_DOCUMENT_STATUS_TABLE,
    DYNAMODB_VECTOR_INDEX,
    create_dynamodb_client,
)
from app.database.database_tables import CorpusRecord, DocumentRecord


def _string(value: str) -> dict[str, str]:
    return {"S": value}


def _number(value: float) -> dict[str, str]:
    return {"N": str(value)}


def _optional_string(item: dict[str, Any], name: str) -> str | None:
    attribute = item.get(name)
    return attribute.get("S") if attribute else None


def _corpus_from_item(item: dict[str, Any]) -> CorpusRecord:
    return CorpusRecord(
        id=item["corpus_id"]["S"],
        name=item["name"]["S"],
        corpus_type=item["corpus_type"]["S"],
        owner_id=_optional_string(item, "owner_id"),
        created_at=datetime.fromisoformat(item["created_at"]["S"]),
    )


def _document_from_item(item: dict[str, Any]) -> DocumentRecord:
    publication_date = _optional_string(item, "publication_date")
    return DocumentRecord(
        id=item["document_id"]["S"],
        corpus_id=item["corpus_id"]["S"],
        chunk_id=item["chunk_id"]["S"],
        source=item["source"]["S"],
        external_id=_optional_string(item, "external_id"),
        title=item["title"]["S"],
        abstract=_optional_string(item, "abstract"),
        authors=[value["S"] for value in item.get("authors", {}).get("L", [])],
        publication_date=date.fromisoformat(publication_date) if publication_date else None,
        source_url=_optional_string(item, "source_url"),
        license_url=_optional_string(item, "license_url"),
        content=item["content"]["S"],
        embedding_model=_optional_string(item, "embedding_model"),
        embedding_dimensions=(
            int(item["embedding_dimensions"]["N"])
            if "embedding_dimensions" in item
            else None
        ),
        created_at=datetime.fromisoformat(item["created_at"]["S"]),
        categories=[value["S"] for value in item.get("categories", {}).get("L", [])],
        updated_date=(
            date.fromisoformat(item["updated_date"]["S"])
            if "updated_date" in item else None
        ),
    )


class DynamoRepository:
    """Keep low-level DynamoDB request details out of application services."""

    def __init__(self, client: Any | None = None) -> None:
        self.client = client or create_dynamodb_client()

    def get_documents(self, *, corpus_id: str, source: str, external_ids: list[str]) -> dict[str, DocumentRecord]:
        """Read up to 100 distinct documents without transferring embeddings.

        Exhausted partial-response retries must fail, never classify unread keys
        as missing (which would cause unnecessary embedding charges).
        """
        external_ids = list(dict.fromkeys(external_ids))
        if len(external_ids) > 100:
            raise ValueError("Batch lookup supports at most 100 distinct IDs")
        if not external_ids:
            return {}
        fields = ("corpus_id chunk_id document_id source external_id title abstract authors "
                  "publication_date source_url license_url content embedding_model "
                  "embedding_dimensions created_at categories updated_date").split()
        names = {f"#f{i}": field for i, field in enumerate(fields)}
        request = {DYNAMODB_CHUNKS_TABLE: {
            "Keys": [{"corpus_id": _string(corpus_id),
                      "chunk_id": _string(self.chunk_id(self.document_id(source, value)))}
                     for value in external_ids],
            "ConsistentRead": True,
            "ProjectionExpression": ", ".join(names),
            "ExpressionAttributeNames": names,
        }}
        documents = {}
        for attempt in range(8):
            response = self.client.batch_get_item(RequestItems=request)
            for item in response.get("Responses", {}).get(DYNAMODB_CHUNKS_TABLE, []):
                document = _document_from_item(item)
                documents[document.external_id] = document
            request = response.get("UnprocessedKeys", {})
            if not any(value.get("Keys") for value in request.values()):
                return documents
            if attempt < 7:
                time.sleep(random.uniform(0, min(0.1 * 2 ** attempt, 5)))
        raise RuntimeError("DynamoDB left batch lookup keys unprocessed after 8 attempts; retry preparation")

    def list_corpora(self) -> list[CorpusRecord]:
        items: list[dict[str, Any]] = []
        request: dict[str, Any] = {"TableName": DYNAMODB_CORPORA_TABLE}
        while True:
            response = self.client.scan(**request)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            request["ExclusiveStartKey"] = last_key

        return sorted(
            (_corpus_from_item(item) for item in items),
            key=lambda corpus: corpus.created_at,
        )

    def get_corpus(self, corpus_id: str) -> CorpusRecord | None:
        response = self.client.get_item(
            TableName=DYNAMODB_CORPORA_TABLE,
            Key={"corpus_id": _string(corpus_id)},
            ConsistentRead=True,
        )
        item = response.get("Item")
        return _corpus_from_item(item) if item else None

    def find_corpus(
        self,
        *,
        name: str,
        corpus_type: str,
        owner_id: str | None,
    ) -> CorpusRecord | None:
        return next(
            (
                corpus
                for corpus in self.list_corpora()
                if corpus.name == name
                and corpus.corpus_type == corpus_type
                and corpus.owner_id == owner_id
            ),
            None,
        )

    def get_or_create_corpus(
        self,
        *,
        name: str,
        corpus_type: str,
        owner_id: str | None,
    ) -> CorpusRecord:
        existing = self.find_corpus(
            name=name,
            corpus_type=corpus_type,
            owner_id=owner_id,
        )
        if existing is not None:
            return existing

        identity = f"{owner_id or ''}|{corpus_type}|{name}"
        corpus = CorpusRecord(
            id=str(uuid5(NAMESPACE_URL, identity)),
            name=name,
            corpus_type=corpus_type,
            owner_id=owner_id,
            created_at=datetime.now(timezone.utc),
        )
        item: dict[str, Any] = {
            "corpus_id": _string(corpus.id),
            "name": _string(corpus.name),
            "corpus_type": _string(corpus.corpus_type),
            "created_at": _string(corpus.created_at.isoformat()),
        }
        if owner_id is not None:
            item["owner_id"] = _string(owner_id)

        try:
            self.client.put_item(
                TableName=DYNAMODB_CORPORA_TABLE,
                Item=item,
                ConditionExpression="attribute_not_exists(corpus_id)",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

        return self.get_corpus(corpus.id) or corpus

    @staticmethod
    def document_id(source: str, external_id: str) -> str:
        return f"{source}:{external_id}"

    @staticmethod
    def chunk_id(document_id: str, chunk_index: int = 0) -> str:
        return f"{document_id}#chunk:{chunk_index:06d}"

    def get_document(
        self,
        *,
        corpus_id: str,
        source: str,
        external_id: str,
    ) -> DocumentRecord | None:
        document_id = self.document_id(source, external_id)
        response = self.client.get_item(
            TableName=DYNAMODB_CHUNKS_TABLE,
            Key={
                "corpus_id": _string(corpus_id),
                "chunk_id": _string(self.chunk_id(document_id)),
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        return _document_from_item(item) if item else None

    def get_embedding(self, *, corpus_id: str, chunk_id: str) -> list[float]:
        """Load an existing vector when metadata changes but content does not."""

        response = self.client.get_item(
            TableName=DYNAMODB_CHUNKS_TABLE,
            Key={
                "corpus_id": _string(corpus_id),
                "chunk_id": _string(chunk_id),
            },
            ProjectionExpression="embedding",
            ConsistentRead=True,
        )
        values = response.get("Item", {}).get("embedding", {}).get("L", [])
        if not values:
            raise RuntimeError("The stored document has no embedding")
        return [float(value["N"]) for value in values]

    def put_document(
        self,
        *,
        corpus_id: str,
        source: str,
        external_id: str,
        title: str,
        abstract: str | None,
        authors: list[str],
        publication_date: date | None,
        source_url: str | None,
        license_url: str | None,
        chunk_index: int = 0,
        content: str,
        embedding: list[float],
        embedding_model: str,
        created_at: datetime | None = None,
        categories: list[str] | None = None,
        updated_date: date | None = None,
    ) -> DocumentRecord:
        document_id = self.document_id(source, external_id)
        chunk_id = self.chunk_id(document_id, chunk_index)
        timestamp = created_at or datetime.now(timezone.utc)
        item: dict[str, Any] = {
            "corpus_id": _string(corpus_id),
            "chunk_id": _string(chunk_id),
            "document_id": _string(document_id),
            "source": _string(source),
            "external_id": _string(external_id),
            "title": _string(title),
            "authors": {"L": [_string(author) for author in authors]},
            "categories": {"L": [_string(value) for value in (categories or [])]},
            "content": _string(content),
            "embedding": {"L": [_number(value) for value in embedding]},
            "embedding_model": _string(embedding_model),
            "embedding_dimensions": {"N": str(len(embedding))},
            "created_at": _string(timestamp.isoformat()),
        }
        optional_values = {
            "abstract": abstract,
            "publication_date": publication_date.isoformat() if publication_date else None,
            "source_url": source_url,
            "license_url": license_url,
            "updated_date": updated_date.isoformat() if updated_date else None,
        }
        for name, value in optional_values.items():
            if value is not None:
                item[name] = _string(value)

        self.client.put_item(
            TableName=DYNAMODB_CHUNKS_TABLE,
            Item=item,
            ReturnConsumedCapacity="INDEXES",
        )
        return _document_from_item(item)

    def search_documents(
        self,
        *,
        corpus_id: str,
        embedding: list[float],
        embedding_model: str,
        limit: int,
    ) -> list[tuple[DocumentRecord, float]]:
        response = self.client.search_vectors(
            TableName=DYNAMODB_CHUNKS_TABLE,
            IndexName=DYNAMODB_VECTOR_INDEX,
            SearchVector=[_number(value) for value in embedding],
            TopK=limit,
            SearchConditionExpression=(
                "#corpus_id = :corpus_id AND #embedding_model = :embedding_model"
            ),
            ExpressionAttributeNames={
                "#corpus_id": "corpus_id",
                "#embedding_model": "embedding_model",
            },
            ExpressionAttributeValues={
                ":corpus_id": _string(corpus_id),
                ":embedding_model": _string(embedding_model),
            },
            ReturnConsumedCapacity="TOTAL",
        )
        results = response.get("SearchResults", [])
        if not results:
            return []

        keys = [
            {
                "corpus_id": result["Item"]["corpus_id"],
                "chunk_id": result["Item"]["chunk_id"],
            }
            for result in results
        ]
        batch_response = self.client.batch_get_item(
            RequestItems={
                DYNAMODB_CHUNKS_TABLE: {"Keys": keys, "ConsistentRead": True}
            }
        )
        items = batch_response.get("Responses", {}).get(DYNAMODB_CHUNKS_TABLE, [])
        by_key = {item["chunk_id"]["S"]: item for item in items}

        ranked: list[tuple[DocumentRecord, float]] = []
        for result in results:
            chunk_id = result["Item"]["chunk_id"]["S"]
            if item := by_key.get(chunk_id):
                ranked.append((_document_from_item(item), float(result["Score"])))
        return ranked

    def delete_corpus(self, corpus_id: str) -> None:
        """Delete one corpus and its chunks; intended for test cleanup."""

        response = self.client.query(
            TableName=DYNAMODB_CHUNKS_TABLE,
            KeyConditionExpression="#corpus_id = :corpus_id",
            ExpressionAttributeNames={"#corpus_id": "corpus_id"},
            ExpressionAttributeValues={":corpus_id": _string(corpus_id)},
            ProjectionExpression="corpus_id, chunk_id",
        )
        requests = [
            {
                "DeleteRequest": {
                    "Key": {
                        "corpus_id": item["corpus_id"],
                        "chunk_id": item["chunk_id"],
                    }
                }
            }
            for item in response.get("Items", [])
        ]
        for start in range(0, len(requests), 25):
            self.client.batch_write_item(
                RequestItems={DYNAMODB_CHUNKS_TABLE: requests[start : start + 25]}
            )
        self.client.delete_item(
            TableName=DYNAMODB_CORPORA_TABLE,
            Key={"corpus_id": _string(corpus_id)},
        )

    def create_document_status(
        self,
        *,
        corpus_id: str,
        document_id: str,
        owner_id: str,
        filename: str,
        s3_bucket: str,
        s3_key: str,
    ) -> None:
        """Create an ``uploading`` record before writing the file to S3.

        Store the owner, original filename, intended S3 location, and UTC
        timestamps under the corpus/document key. The caller must authorize
        the upload; this method does not check ownership or upload the file.
        A conditional write prevents replacing an existing record and raises
        ClientError if the key already exists or the database request fails.
        """

        timestamp = datetime.now(timezone.utc).isoformat()

        self.client.put_item(
            TableName=DYNAMODB_DOCUMENT_STATUS_TABLE,
            Item={
                "corpus_id": _string(corpus_id),
                "document_id": _string(document_id),
                "owner_id": _string(owner_id),
                "filename": _string(filename),
                "s3_bucket": _string(s3_bucket),
                "s3_key": _string(s3_key),
                "status": _string("uploading"),
                "created_at": _string(timestamp),
                "updated_at": _string(timestamp),
            },
            ConditionExpression=(
                "attribute_not_exists(corpus_id) "
                "AND attribute_not_exists(document_id)"
            ),
        )

    def get_document_status(
        self,
        *,
        corpus_id: str,
        document_id: str,
    ) -> dict[str, Any] | None:
        """Return upload metadata and status, or None when the record is absent.

        Use a strongly consistent read and convert the initial string fields
        from DynamoDB's wire format into a plain dictionary. Additional fields,
        such as processing leases, are not included. The caller must enforce
        access control before exposing the record to a user.
        """

        response = self.client.get_item(
            TableName=DYNAMODB_DOCUMENT_STATUS_TABLE,
            Key={
                "corpus_id": _string(corpus_id),
                "document_id": _string(document_id),
            },
            ConsistentRead=True,
        )

        item = response.get("Item")
        if item is None:
            return None

        # These initial record fields are all stored as strings.
        return {
            name: item[name]["S"]
            for name in (
                "corpus_id",
                "document_id",
                "owner_id",
                "filename",
                "s3_bucket",
                "s3_key",
                "status",
                "created_at",
                "updated_at",
            )
        }

    def finish_document_upload(
        self,
        *,
        corpus_id: str,
        document_id: str,
        succeeded: bool,
    ) -> None:
        """Record the S3 upload outcome while the status is still ``uploading``.

        Set the status to ``uploaded`` on success or ``upload_failed`` on
        failure, and refresh the UTC update timestamp. The conditional update
        preserves any status already advanced by an ingestion worker. Missing
        records and records in another state are left unchanged; other AWS
        errors propagate to the caller. This does not mark ingestion complete.
        """

        try:
            self.client.update_item(
                TableName=DYNAMODB_DOCUMENT_STATUS_TABLE,
                Key={
                    "corpus_id": _string(corpus_id),
                    "document_id": _string(document_id),
                },
                UpdateExpression=(
                    "SET #status = :next_status, updated_at = :now"
                ),
                ConditionExpression="#status = :uploading",
                ExpressionAttributeNames={
                    "#status": "status",
                },
                ExpressionAttributeValues={
                    ":uploading": _string("uploading"),
                    ":next_status": _string(
                        "uploaded" if succeeded else "upload_failed"
                    ),
                    ":now": _string(
                        datetime.now(timezone.utc).isoformat()
                    ),
                },
            )
        except ClientError as error:
            if (
                error.response["Error"]["Code"]
                != "ConditionalCheckFailedException"
            ):
                raise
            # A worker may already have advanced the status.
            # Do not overwrite its processing or completion state.
