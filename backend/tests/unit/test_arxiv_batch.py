"""Offline end-to-end tests for batch preparation and result ingestion."""

import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.clients.embeddings import DIMENSIONS, MODEL_ID
from app.database.database_tables import CorpusRecord
from app.database.repository import DynamoRepository
from app.scripts.arxiv_batch import import_results, prepare, model_input
from app.schemas.api_schemas import ArxivPaper
from app.scripts.arxiv_bulk_import import source_identity


class MemoryClient:
    def __init__(self):
        self.items = {}
        self.batch_requests = []

    def batch_get_item(self, **kwargs):
        self.batch_requests.append(kwargs)
        table, request = next(iter(kwargs["RequestItems"].items()))
        items = [self.items[key["chunk_id"]["S"]] for key in request["Keys"]
                 if key["chunk_id"]["S"] in self.items]
        return {"Responses": {table: list(reversed(items))}}

    def get_item(self, **kwargs):
        item = self.items.get(kwargs["Key"]["chunk_id"]["S"])
        return {"Item": item} if item else {}

    def put_item(self, **kwargs):
        item = kwargs["Item"]
        self.items[item["chunk_id"]["S"]] = item
        return {}


class BatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "snapshot.json"
        self.source.write_text("\n".join(json.dumps({
            "id": f"2401.{n:05d}", "title": f"Title {n}", "abstract": "Abstract",
            "categories": "cs.AI", "authors_parsed": [["Smith", "Jane", ""]],
        }) for n in range(100)), encoding="utf-8")
        self.repository = DynamoRepository(client=MemoryClient())
        self.corpus = CorpusRecord("test", "My arXiv research corpus", "research_abstract", None, datetime.now(timezone.utc))
        self.destination = self.root / "batch"
        self.settings = dict(repository=self.repository, corpus=self.corpus, target=["table-arn"],
                             model=MODEL_ID, dimensions=DIMENSIONS)

    def prepared_results(self):
        prepare(self.source, self.destination, limit=100, **self.settings)
        self.manifest_path = self.destination / "manifest.json"
        manifest = json.loads(self.manifest_path.read_text())
        rows = []
        for record_id, record in manifest["records"].items():
            paper = ArxivPaper.model_validate(record["paper"])
            rows.append({"recordId": record_id, "modelInput": model_input(paper, DIMENSIONS),
                         "modelOutput": {"embedding": [0.1] * DIMENSIONS}})
        self.output = self.root / "input.jsonl.out"
        self.write_results(list(reversed(rows)))
        return rows

    def write_results(self, rows):
        self.output.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    def test_out_of_order_results_dry_run_then_import_and_replay(self):
        self.prepared_results()
        self.assertEqual(len(self.repository.client.batch_requests), 1)
        request = next(iter(self.repository.client.batch_requests[0]["RequestItems"].values()))
        self.assertEqual(len(request["Keys"]), 100)
        self.assertNotIn("embedding", request["ExpressionAttributeNames"].values())
        result = import_results(self.manifest_path, [self.output])
        self.assertEqual(result["valid"], 100)
        self.assertEqual(len(self.repository.client.items), 0)
        result = import_results(self.manifest_path, [self.output], **self.settings)
        self.assertEqual(result["created"], 100)
        result = import_results(self.manifest_path, [self.output], **self.settings)
        self.assertEqual(result["unchanged"], 100)

    def test_error_and_missing_results_are_reported(self):
        rows = self.prepared_results()
        del rows[0]["modelOutput"]
        rows[0]["error"] = {"errorCode": 400, "errorMessage": "bad input"}
        self.write_results(rows[:-1])
        result = import_results(self.manifest_path, [self.output], **self.settings)
        self.assertEqual(result["created"], 98)
        self.assertEqual(result["failed_or_missing"], 2)

    def test_invalid_last_record_prevents_all_writes(self):
        rows = self.prepared_results()
        rows[-1]["modelOutput"]["embedding"] = [float("nan")] * DIMENSIONS
        self.write_results(rows)
        with self.assertRaisesRegex(ValueError, "Invalid embedding"):
            import_results(self.manifest_path, [self.output], **self.settings)
        self.assertFalse(self.repository.client.items)

    def test_wrong_input_duplicate_and_wrong_target_rejected(self):
        rows = self.prepared_results()
        with self.assertRaisesRegex(ValueError, "configured tables"):
            import_results(self.manifest_path, [self.output], **{**self.settings, "target": ["other"]})
        self.write_results(rows + [rows[0]])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            import_results(self.manifest_path, [self.output])
        rows[0]["modelInput"]["inputText"] = "different paper"
        self.write_results(rows)
        with self.assertRaisesRegex(ValueError, "Input mismatch"):
            import_results(self.manifest_path, [self.output])

    def test_prepare_skips_current_embeddings_and_does_not_overwrite_files(self):
        self.prepared_results()
        import_results(self.manifest_path, [self.output], **self.settings)
        with self.assertRaises(FileExistsError):
            prepare(self.source, self.destination, limit=100, **self.settings)
        with self.assertRaisesRegex(ValueError, "Only 0 candidates"):
            prepare(self.source, self.root / "new-batch", limit=100, **self.settings)
        self.assertFalse((self.root / "new-batch" / "manifest.json").exists())

    def test_document_changed_after_preparation_is_not_overwritten(self):
        rows = self.prepared_results()
        import_results(self.manifest_path, [self.output], **self.settings)
        item = next(iter(self.repository.client.items.values()))
        item["content"]["S"] = "Newer content"
        result = import_results(self.manifest_path, [self.output], **self.settings)
        self.assertEqual(result["failed_or_missing"], 1)
        self.assertEqual(item["content"]["S"], "Newer content")

    def test_checkpoint_seek_and_identity_validation(self):
        first = json.dumps({"id": "2301.00001", "title": "Old", "abstract": "Done"}).encode() + b"\n"
        self.source.write_bytes(first + self.source.read_bytes())
        checkpoint = self.root / "checkpoint.json"
        state = {"version": 1, "line": 1, "offset": len(first), "identity": {
            "source": source_identity(self.source), "targets": ["table-arn"],
            "corpus": self.corpus.name, "model": MODEL_ID, "dimensions": DIMENSIONS}}
        checkpoint.write_text(json.dumps(state))
        result = prepare(self.source, self.destination, limit=100, from_checkpoint=checkpoint, **self.settings)
        self.assertEqual(result["end_line"], 101)
        self.assertEqual(result["prepared"], 100)
        self.assertNotIn("2301.00001", (self.destination / "manifest.json").read_text())
        state["identity"]["dimensions"] = 1
        checkpoint.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "does not match"):
            prepare(self.source, self.root / "wrong", limit=100, from_checkpoint=checkpoint, **self.settings)
        self.assertFalse((self.root / "wrong").exists())

    def test_partial_reads_are_retried_not_classified_as_missing(self):
        client = self.repository.client
        original = client.batch_get_item
        requests = []
        def partial(**kwargs):
            requests.append(kwargs)
            table, request = next(iter(kwargs["RequestItems"].items()))
            if len(requests) == 1:
                return {"UnprocessedKeys": {table: {**request, "Keys": request["Keys"][:1]}}}
            return original(**kwargs)
        with patch.object(client, "batch_get_item", side_effect=partial), patch("app.database.repository.time.sleep"):
            prepare(self.source, self.destination, limit=100, **self.settings)
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(next(iter(requests[1]["RequestItems"].values()))["Keys"]), 1)

    def test_exhausted_partial_reads_abort_preparation(self):
        def partial(**kwargs):
            return {"UnprocessedKeys": kwargs["RequestItems"]}
        with patch.object(self.repository.client, "batch_get_item", side_effect=partial), patch("app.database.repository.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "unprocessed"):
                prepare(self.source, self.destination, limit=100, **self.settings)
        self.assertFalse((self.destination / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
