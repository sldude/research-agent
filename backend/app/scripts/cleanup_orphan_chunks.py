"""Preview or delete orphaned upload chunks in one explicitly selected corpus."""

import argparse
import json

import boto3
from botocore.exceptions import ClientError


def cleanup(client, *, corpus_id, chunks_table, status_table, apply=False):
    request = {
        "TableName": chunks_table,
        "KeyConditionExpression": "corpus_id = :corpus",
        "FilterExpression": "#source = :upload",
        "ExpressionAttributeNames": {"#source": "source"},
        "ExpressionAttributeValues": {
            ":corpus": {"S": corpus_id}, ":upload": {"S": "upload"},
        },
        "ProjectionExpression": "corpus_id, chunk_id, document_id, external_id",
        "ConsistentRead": True,
    }
    counts = {"orphan_chunks": 0, "deleted_chunks": 0, "protected_chunks": 0}
    while True:
        page = client.query(**request)
        for item in page.get("Items", []):
            upload_id = item.get("external_id", {}).get("S")
            if not upload_id or "document_id" not in item:
                counts["protected_chunks"] += 1
                continue
            status_key = {"corpus_id": {"S": corpus_id}, "document_id": {"S": upload_id}}
            status = client.get_item(TableName=status_table, Key=status_key, ConsistentRead=True)
            # Protect every extant status, including failed or processing uploads.
            if "Item" in status:
                counts["protected_chunks"] += 1
                continue
            counts["orphan_chunks"] += 1
            print(json.dumps({"corpus_id": corpus_id, "upload_id": upload_id,
                              "chunk_id": item["chunk_id"]["S"]}))
            if apply:
                # Atomically recheck absence: an upload must not become active
                # between inspection and deletion. Never remove status or S3 data.
                try:
                    client.transact_write_items(TransactItems=[
                        {"ConditionCheck": {
                            "TableName": status_table, "Key": status_key,
                            "ConditionExpression": "attribute_not_exists(document_id)",
                        }},
                        {"Delete": {
                            "TableName": chunks_table,
                            "Key": {"corpus_id": item["corpus_id"], "chunk_id": item["chunk_id"]},
                            "ConditionExpression": "#source = :upload AND external_id = :external AND document_id = :document",
                            "ExpressionAttributeNames": {"#source": "source"},
                            "ExpressionAttributeValues": {
                                ":upload": {"S": "upload"}, ":external": {"S": upload_id},
                                ":document": item["document_id"],
                            },
                        }},
                    ])
                except ClientError as error:
                    reasons = error.response.get("CancellationReasons", [])
                    if (error.response["Error"]["Code"] == "TransactionCanceledException"
                            and any(r.get("Code") == "ConditionalCheckFailed" for r in reasons)
                            and all(r.get("Code") in ("None", "ConditionalCheckFailed") for r in reasons)):
                        counts["protected_chunks"] += 1
                        continue
                    raise
                counts["deleted_chunks"] += 1
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break
        request["ExclusiveStartKey"] = last_key
    return counts


def find_corpus(client, table, name):
    request = {
        "TableName": table,
        "FilterExpression": "#name = :name AND corpus_type = :kind",
        "ExpressionAttributeNames": {"#name": "name"},
        "ExpressionAttributeValues": {":name": {"S": name}, ":kind": {"S": "user_upload"}},
        "ProjectionExpression": "corpus_id",
        "ConsistentRead": True,
    }
    matches = []
    while True:
        page = client.scan(**request)
        matches.extend(item["corpus_id"]["S"] for item in page.get("Items", []))
        if not page.get("LastEvaluatedKey"):
            break
        request["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    if len(matches) != 1:
        raise ValueError(f"Found {len(matches)} uploaded corpora named {name!r}; use --corpus-id to select one explicitly.")
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--corpus-id")
    selection.add_argument("--corpus-name")
    parser.add_argument("--profile")
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--chunks-table", default="research-agent-chunks")
    parser.add_argument("--status-table", default="research-agent-document-status")
    parser.add_argument("--corpora-table", default="research-agent-corpora")
    parser.add_argument("--apply", action="store_true", help="Delete matching chunks; default is preview only")
    args = parser.parse_args()
    client = boto3.Session(profile_name=args.profile, region_name=args.region).client("dynamodb")
    corpus_id = args.corpus_id or find_corpus(client, args.corpora_table, args.corpus_name)
    result = cleanup(client, corpus_id=corpus_id, chunks_table=args.chunks_table,
                     status_table=args.status_table, apply=args.apply)
    print(json.dumps({"mode": "apply" if args.apply else "preview", **result}))


if __name__ == "__main__":
    main()
