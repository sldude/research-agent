"""DynamoDB persistence operations for corpora and embedded document chunks."""

from datetime import date, datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from botocore.exceptions import ClientError

from app.database.database_connect import (
    DYNAMODB_CHUNKS_TABLE,
    DYNAMODB_CORPORA_TABLE,
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
    )


class DynamoRepository:
    """Keep low-level DynamoDB request details out of application services."""

    def __init__(self, client: Any | None = None) -> None:
        self.client = client or create_dynamodb_client()

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
        content: str,
        embedding: list[float],
        embedding_model: str,
        created_at: datetime | None = None,
    ) -> DocumentRecord:
        document_id = self.document_id(source, external_id)
        chunk_id = self.chunk_id(document_id)
        timestamp = created_at or datetime.now(timezone.utc)
        item: dict[str, Any] = {
            "corpus_id": _string(corpus_id),
            "chunk_id": _string(chunk_id),
            "document_id": _string(document_id),
            "source": _string(source),
            "external_id": _string(external_id),
            "title": _string(title),
            "authors": {"L": [_string(author) for author in authors]},
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
