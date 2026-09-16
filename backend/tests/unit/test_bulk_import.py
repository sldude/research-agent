"""Offline coverage of parsing and crash-safe snapshot progress."""

from datetime import date
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from app.scripts.arxiv_bulk_import import (
    checkpoint_lock, normalize_record, run_import, save_checkpoint, source_identity,
    EmbeddingRateLimiter, invoke_embedding,
)


def record(number=1):
    return {
        "id": f"2401.{number:05d}", "title": "A\n title", "abstract": "An abstract",
        "authors_parsed": [["Smith", "Jane", "Jr."]],
        "categories": "cs.AI cs.IR", "license": None,
        "versions": [{"version": "v1", "created": "Mon, 1 Jan 2024 12:00:00 GMT"}],
        "update_date": "2024-02-01",
    }


class BulkImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "snapshot.json"
        self.checkpoint = Path(self.directory.name) / "progress.json"
        self.source.write_text("\n".join(json.dumps(record(n)) for n in range(1, 6)) + "\n", encoding="utf-8")
        self.identity = {"source": source_identity(self.source)}

    def test_metadata_and_legacy_id(self):
        raw = record()
        raw["id"] = "hep-th/9901001v2"
        paper = normalize_record(raw)
        self.assertEqual(paper.external_id, "hep-th/9901001")
        self.assertEqual(paper.title, "A title")
        self.assertEqual(paper.authors, ["Jane Smith Jr."])
        self.assertEqual(paper.categories, ["cs.AI", "cs.IR"])
        self.assertEqual(paper.publication_date, date(2024, 1, 1))
        self.assertEqual(paper.updated_date, date(2024, 2, 1))

    def test_embedding_attempts_are_paced_and_share_cooldown(self):
        clock = [100.0]
        def advance(seconds):
            clock[0] += seconds
        with patch("app.scripts.arxiv_bulk_import.time.monotonic", side_effect=lambda: clock[0]), patch(
            "app.scripts.arxiv_bulk_import.time.sleep", side_effect=advance
        ), patch("app.scripts.arxiv_bulk_import.random.uniform", return_value=0):
            limiter = EmbeddingRateLimiter(5)
            limiter.acquire()
            limiter.acquire()
            self.assertAlmostEqual(clock[0], 100.2)
            limiter.throttled()
            limiter.throttled()  # Another in-flight rejection must not halve again.
            self.assertEqual(limiter.rate, 2.5)
            limiter.acquire()
            self.assertGreaterEqual(clock[0], 160.2)
            limiter.acquire()
            self.assertAlmostEqual(clock[0], 160.6)

    def test_throttling_retries_same_request_through_limiter(self):
        from botocore.exceptions import ClientError
        error = ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel")
        client, limiter = Mock(), Mock()
        client.invoke_model.side_effect = [error, {"body": "success"}]
        self.assertEqual(invoke_embedding(client, limiter, modelId="test"), {"body": "success"})
        self.assertEqual(limiter.acquire.call_count, 2)
        limiter.throttled.assert_called_once()
        self.assertEqual(client.invoke_model.call_args_list[0], client.invoke_model.call_args_list[1])

    def test_throttling_eventually_stops_and_permission_error_is_not_retried(self):
        from botocore.exceptions import ClientError
        for code, attempts in [("ThrottlingException", 8), ("AccessDeniedException", 1)]:
            client, limiter = Mock(), Mock()
            client.invoke_model.side_effect = ClientError({"Error": {"Code": code}}, "InvokeModel")
            with self.assertRaises(ClientError):
                invoke_embedding(client, limiter, modelId="test")
            self.assertEqual(client.invoke_model.call_count, attempts)

    def test_invalid_embedding_rates_rejected(self):
        for rate in [0, -1, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                EmbeddingRateLimiter(rate)

    def test_checkpoint_retries_temporary_replace_lock(self):
        save_checkpoint(self.checkpoint, {"line": 10})
        replace = Path.replace
        attempts = []
        def locked_then_available(source, target):
            attempts.append(target)
            self.assertEqual(json.loads(self.checkpoint.read_text()), {"line": 10})
            if len(attempts) < 3:
                raise PermissionError("Windows file lock")
            return replace(source, target)
        with patch.object(Path, "replace", locked_then_available), patch(
            "app.scripts.arxiv_bulk_import.time.sleep"
        ) as sleep:
            save_checkpoint(self.checkpoint, {"line": 20})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(json.loads(self.checkpoint.read_text()), {"line": 20})

    def test_checkpoint_permanent_lock_preserves_previous_progress(self):
        save_checkpoint(self.checkpoint, {"line": 10})
        with patch.object(Path, "replace", side_effect=PermissionError("locked")) as replace, patch(
            "app.scripts.arxiv_bulk_import.time.sleep"
        ):
            with self.assertRaisesRegex(PermissionError, "previous checkpoint was preserved"):
                save_checkpoint(self.checkpoint, {"line": 20})
        self.assertEqual(replace.call_count, 8)
        self.assertEqual(json.loads(self.checkpoint.read_text()), {"line": 10})

    def test_checkpoint_retries_locked_temporary_file(self):
        write_text = Path.write_text
        attempts = []
        def locked_then_available(path, *args, **kwargs):
            attempts.append(path)
            if len(attempts) == 1:
                raise PermissionError("temporary file locked")
            return write_text(path, *args, **kwargs)
        with patch.object(Path, "write_text", locked_then_available), patch(
            "app.scripts.arxiv_bulk_import.time.sleep"
        ):
            save_checkpoint(self.checkpoint, {"line": 20})
        self.assertEqual(json.loads(self.checkpoint.read_text()), {"line": 20})

    def test_checkpoint_other_io_errors_fail_without_retry(self):
        with patch.object(Path, "replace", side_effect=OSError("disk error")), patch(
            "app.scripts.arxiv_bulk_import.time.sleep"
        ) as sleep:
            with self.assertRaisesRegex(OSError, "disk error"):
                save_checkpoint(self.checkpoint, {"line": 20})
        sleep.assert_not_called()

    def test_dry_run_does_not_write_checkpoint(self):
        summary = run_import(self.source, self.checkpoint, self.identity, limit=2)
        self.assertEqual(summary["processed"], 2)
        self.assertFalse(self.checkpoint.exists())

    def test_limit_then_resume_to_eof(self):
        seen = []
        def process(paper):
            seen.append(paper.external_id)
            return "created"
        run_import(self.source, self.checkpoint, self.identity, process, limit=3)
        summary = run_import(self.source, self.checkpoint, self.identity, process, limit=None)
        self.assertEqual(summary["processed"], 2)
        self.assertEqual(len(set(seen)), 5)
        self.assertEqual(len(seen), 5)
        self.assertEqual(run_import(self.source, self.checkpoint, self.identity, process)["processed"], 0)

    def test_failed_record_replays_without_skipping_records(self):
        stored = set()
        def fail(paper):
            if paper.external_id == "2401.00004":
                raise RuntimeError("temporary failure")
            stored.add(paper.external_id)
            return "created"
        with self.assertRaises(RuntimeError):
            run_import(self.source, self.checkpoint, self.identity, fail, workers=2)
        checkpoint_line = json.loads(self.checkpoint.read_text())["line"] if self.checkpoint.exists() else 0
        self.assertLess(checkpoint_line, 4)
        def resume(paper):
            status = "unchanged" if paper.external_id in stored else "created"
            stored.add(paper.external_id)
            return status
        summary = run_import(self.source, self.checkpoint, self.identity, resume)
        self.assertEqual(summary["processed"], 5 - checkpoint_line)
        self.assertEqual(len(stored), 5)

    def test_refills_worker_before_slow_first_record_finishes(self):
        third_started = Event()
        def process(paper):
            if paper.external_id == "2401.00001":
                if not third_started.wait(5):
                    raise AssertionError("Worker was not refilled while first record was waiting")
            if paper.external_id == "2401.00003":
                # Record 2 finished, but progress cannot skip unfinished record 1.
                self.assertFalse(self.checkpoint.exists())
                third_started.set()
            return "created"
        summary = run_import(self.source, self.checkpoint, self.identity, process, workers=2)
        self.assertEqual(summary["created"], 5)
        self.assertEqual(json.loads(self.checkpoint.read_text())["line"], 5)

    def test_early_failure_does_not_checkpoint_later_successes(self):
        third_started = Event()
        stored = set()
        def process(paper):
            if paper.external_id == "2401.00001":
                if not third_started.wait(5):
                    raise AssertionError("Worker was not refilled")
                raise RuntimeError("first paper failed")
            stored.add(paper.external_id)
            if paper.external_id == "2401.00003":
                third_started.set()
            return "created"
        with self.assertRaisesRegex(RuntimeError, "first paper failed"):
            run_import(self.source, self.checkpoint, self.identity, process, workers=2)
        self.assertFalse(self.checkpoint.exists())
        def resume(paper):
            status = "unchanged" if paper.external_id in stored else "created"
            stored.add(paper.external_id)
            return status
        summary = run_import(self.source, self.checkpoint, self.identity, resume, workers=2)
        self.assertEqual(summary["processed"], 5)
        self.assertEqual(len(stored), 5)

    def test_wrong_snapshot_or_target_rejected(self):
        run_import(self.source, self.checkpoint, self.identity, lambda _: "created", limit=1)
        with self.assertRaisesRegex(ValueError, "does not match"):
            run_import(self.source, self.checkpoint, {"source": "changed"}, lambda _: "created")

    def test_malformed_record_does_not_advance_checkpoint(self):
        self.source.write_text(json.dumps(record()) + "\nnot json\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 2"):
            run_import(self.source, self.checkpoint, self.identity, lambda _: "created", workers=2)
        self.assertFalse(self.checkpoint.exists())

    def test_lock_prevents_concurrent_resume_and_cleans_up(self):
        with checkpoint_lock(self.checkpoint):
            with self.assertRaises(FileExistsError):
                with checkpoint_lock(self.checkpoint):
                    self.fail("Second lock should not succeed")
        with checkpoint_lock(self.checkpoint):
            pass

    def test_metadata_update_reuses_embedding_and_unchanged_skips_write(self):
        from datetime import datetime, timezone
        from app.clients.embeddings import DIMENSIONS, MODEL_ID
        from app.database.database_tables import CorpusRecord
        from app.database.repository import DynamoRepository
        from app.services.arxiv_ingestion import save_arxiv_paper

        client = Mock()
        repository = DynamoRepository(client=client)
        corpus = CorpusRecord("test", "test", "research_abstract", None, datetime.now(timezone.utc))
        paper = normalize_record(record())
        embed = Mock(return_value=[0.0] * DIMENSIONS)
        client.get_item.return_value = {}
        first = save_arxiv_paper(corpus=corpus, paper=paper, repository=repository, embedding_function=embed)
        item = client.put_item.call_args.kwargs["Item"]
        client.get_item.return_value = {"Item": item}
        second = save_arxiv_paper(corpus=corpus, paper=paper, repository=repository, embedding_function=embed)
        self.assertEqual((first.status, second.status), ("created", "unchanged"))
        self.assertEqual(client.put_item.call_count, 1)
        paper.categories = ["cs.LG"]
        third = save_arxiv_paper(corpus=corpus, paper=paper, repository=repository, embedding_function=embed)
        self.assertEqual(third.status, "updated")
        self.assertEqual(third.document.categories, ["cs.LG"])
        self.assertEqual(third.document.embedding_model, MODEL_ID)
        embed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
