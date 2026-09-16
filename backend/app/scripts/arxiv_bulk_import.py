"""Stream an unpacked Kaggle arXiv JSONL snapshot into the existing corpus.

No AWS requests are made without --execute. Workers refill as papers finish.
Checkpoints advance only through consecutive successful records.
"""

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import date
from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import random
import re
from threading import Lock
import time

from app.schemas.api_schemas import ArxivPaper


class EmbeddingRateLimiter:
    """Space request starts across all workers, including retries."""

    def __init__(self, requests_per_second):
        if not math.isfinite(requests_per_second) or requests_per_second <= 0:
            raise ValueError("embedding rate must be finite and positive")
        self.rate = requests_per_second
        self.next_request = 0.0
        self.cooldown_until = 0.0
        self.lock = Lock()

    def acquire(self):
        while True:
            with self.lock:
                now = time.monotonic()
                delay = max(self.next_request, self.cooldown_until) - now
                if delay <= 0:
                    self.next_request = now + 1 / self.rate
                    return
            # Recheck the shared cooldown after waking; don't reserve future slots.
            time.sleep(min(delay, 1.0))

    def throttled(self):
        with self.lock:
            now = time.monotonic()
            if now >= self.cooldown_until:
                self.rate /= 2
                self.cooldown_until = now + 60 + random.uniform(0, 5)
                print(f"Bedrock throttled: pausing embedding requests for about 60 seconds; "
                      f"new rate {self.rate:g}/second.", flush=True)


def invoke_embedding(client, limiter, **request):
    """Bounded retries; SDK retries must be disabled so every attempt is paced."""
    from botocore.exceptions import ClientError

    for attempt in range(8):
        limiter.acquire()
        try:
            return client.invoke_model(**request)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ThrottlingException":
                limiter.throttled()
            elif code not in {"ServiceUnavailableException", "InternalServerException",
                              "ModelTimeoutException", "ModelNotReadyException"}:
                raise
            if attempt == 7:
                raise
            if code != "ThrottlingException":
                time.sleep(random.uniform(0, min(2 ** attempt, 30)))


def normalize_record(record: dict) -> ArxivPaper:
    def required(name):
        value = record.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Missing or empty {name}")
        return " ".join(value.split())

    external_id = re.sub(r"v\d+$", "", required("id"))
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})", external_id):
        raise ValueError(f"Invalid arXiv ID: {external_id}")
    authors = []
    for parts in record.get("authors_parsed", []):
        # Kaggle stores [family name, given names, suffix].
        family, given, *suffix = parts
        authors.append(" ".join(" ".join([given, family, *suffix]).split()))
    if not authors and record.get("authors"):
        authors = [" ".join(record["authors"].split())]
    versions = record.get("versions") or []
    first_version = next((v for v in versions if v.get("version") == "v1"), None)
    published = (
        parsedate_to_datetime(first_version["created"]).date()
        if first_version else None
    )
    return ArxivPaper(
        external_id=external_id,
        title=required("title"),
        abstract=required("abstract"),
        authors=authors,
        categories=(record.get("categories") or "").split(),
        publication_date=published,
        updated_date=date.fromisoformat(record["update_date"]) if record.get("update_date") else None,
        source_url=f"https://arxiv.org/abs/{external_id}",
        license_url=record.get("license"),
    )


def source_identity(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def save_checkpoint(path: Path, state: dict) -> None:
    """Atomically save progress, tolerating brief Windows file locks.

    Never delete or truncate the previous checkpoint as a replacement fallback.
    If access remains blocked, stop with the previous progress still available.
    """
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(state, indent=2) + "\n"
    for attempt in range(8):
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)
            return
        except PermissionError as exc:
            if attempt == 7:
                raise PermissionError(
                    f"Cannot save checkpoint {path} after 8 attempts. "
                    "The previous checkpoint was preserved. Close programs locking "
                    "the checkpoint files, check folder permissions, then rerun to resume."
                ) from exc
            time.sleep(min(0.1 * 2 ** attempt, 1.0))


@contextmanager
def checkpoint_lock(path: Path):
    lock = path.with_suffix(path.suffix + ".lock")
    # Exclusive creation prevents two processes sharing a progress cursor.
    with lock.open("x", encoding="utf-8") as handle:
        handle.write("Import in progress. Remove only after the process has stopped.\n")
    try:
        yield
    finally:
        lock.unlink()


def run_import(source, checkpoint, identity, process=None, *, limit=10_000, workers=2):
    """Keep workers busy with bounded read-ahead and ordered checkpoints.

    limit counts records in this invocation, not all previous invocations.
    process receives an ArxivPaper and returns created/updated/unchanged.
    process=None validates without side effects. At most workers * 4 records
    may be ahead of the checkpoint, preventing unbounded memory/replay if an
    early record stalls while later ones complete.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if not 1 <= workers <= 32:
        raise ValueError("workers must be between 1 and 32")
    state = {"version": 1, "identity": identity, "offset": 0, "line": 0}
    if process is not None and checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        if state.get("version") != 1 or state.get("identity") != identity:
            raise ValueError("Checkpoint does not match snapshot or target. Use a new checkpoint.")
        if not isinstance(state.get("offset"), int) or not 0 <= state["offset"] <= source.stat().st_size:
            raise ValueError("Invalid checkpoint offset")
        if not isinstance(state.get("line"), int) or state["line"] < 0:
            raise ValueError("Invalid checkpoint line")

    counts = {"processed": 0, "created": 0, "updated": 0, "unchanged": 0}
    started = time.monotonic()
    with source.open("rb") as stream, ThreadPoolExecutor(max_workers=workers) as pool:
        stream.seek(state["offset"])
        line_number = state["line"]
        submitted = 0
        pending = {}
        completed = {}
        exhausted = False
        next_report = 100
        try:
            while True:
                while (
                    not exhausted
                    and (limit is None or submitted < limit)
                    and len(pending) < workers
                    and line_number - state["line"] < workers * 4
                ):
                    raw = stream.readline()
                    if not raw:
                        exhausted = True
                        break
                    line_number += 1
                    try:
                        paper = normalize_record(json.loads(raw))
                    except (ValueError, TypeError, KeyError, AttributeError) as exc:
                        raise ValueError(f"Invalid record at line {line_number}: {exc}") from exc
                    submitted += 1
                    if process is None:
                        counts["processed"] += 1
                        state.update(line=line_number, offset=stream.tell())
                        if counts["processed"] >= next_report:
                            print(f"Line {line_number}: {counts}", flush=True)
                            next_report += 100
                    else:
                        pending[pool.submit(process, paper)] = (line_number, stream.tell())

                if not pending:
                    break
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                failure = None
                for future in done:
                    number, offset = pending.pop(future)
                    try:
                        outcome = future.result()
                        if outcome not in {"created", "updated", "unchanged"}:
                            raise ValueError(f"Unexpected ingestion status: {outcome}")
                    except Exception as exc:
                        failure = failure or exc
                    else:
                        completed[number] = offset
                        counts[outcome] += 1
                        counts["processed"] += 1

                previous_line = state["line"]
                while state["line"] + 1 in completed:
                    number = state["line"] + 1
                    state.update(line=number, offset=completed.pop(number))
                if state["line"] != previous_line:
                    save_checkpoint(checkpoint, state)
                if counts["processed"] >= next_report:
                    print(f"Checkpoint line {state['line']}: {counts}", flush=True)
                    next_report += 100
                if failure is not None:
                    raise failure
        finally:
            # Stop submitting on failure or Ctrl+C. Already running AWS calls
            # finish before the executor exits and the checkpoint lock releases.
            for future in pending:
                future.cancel()
    return {**counts, "line": line_number, "seconds": round(time.monotonic() - started, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Unpacked arxiv-metadata-oai-snapshot.json (JSONL)")
    parser.add_argument("--execute", action="store_true", help="Write to AWS; default is offline validation")
    limits = parser.add_mutually_exclusive_group()
    limits.add_argument("--limit", type=int, default=10_000, help="Papers per invocation (default 10000)")
    limits.add_argument("--all", action="store_true", help="Process every remaining record")
    parser.add_argument("--workers", type=int, default=2, help="Concurrent papers, 1-32 (default 2)")
    parser.add_argument("--embedding-rps", type=float, default=5.0,
                        help="Initial embedding requests/second across all workers (default 5); reduces on throttling")
    parser.add_argument("--corpus-name", default="My arXiv research corpus")
    parser.add_argument("--checkpoint", type=Path, help="Default: SOURCE.checkpoint.json")
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error("source must be an existing unpacked JSONL file")
    if args.limit < 1 or not 1 <= args.workers <= 32:
        parser.error("limit must be positive and workers must be between 1 and 32")
    if not math.isfinite(args.embedding_rps) or args.embedding_rps <= 0:
        parser.error("embedding-rps must be finite and positive")
    if not args.corpus_name.strip():
        parser.error("corpus name must not be empty")
    checkpoint = args.checkpoint or args.source.with_suffix(args.source.suffix + ".checkpoint.json")
    if checkpoint.resolve() == args.source.resolve():
        parser.error("checkpoint must not overwrite the source")
    identity = {"source": source_identity(args.source)}
    options = {"limit": None if args.all else args.limit, "workers": args.workers}
    if not args.execute:
        print("Offline validation: no AWS calls or checkpoint changes.")
        print(json.dumps(run_import(args.source, checkpoint, identity, **options), indent=2))
        return

    # Imports and clients are deliberately delayed until --execute.
    from botocore.config import Config
    import boto3
    from app.clients.embeddings import AWS_PROFILE, AWS_REGION, MODEL_ID, DIMENSIONS
    from app.database.database_connect import DYNAMODB_CHUNKS_TABLE, DYNAMODB_CORPORA_TABLE
    from app.database.repository import DynamoRepository
    from app.services.arxiv_ingestion import get_or_create_arxiv_corpus, save_arxiv_paper

    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint_lock(checkpoint):
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        config = Config(retries={"mode": "standard", "total_max_attempts": 8}, max_pool_connections=args.workers)
        dynamo = session.client("dynamodb", config=config)
        bedrock = session.client("bedrock-runtime", config=Config(
            retries={"mode": "standard", "total_max_attempts": 1},
            max_pool_connections=args.workers,
        ))
        limiter = EmbeddingRateLimiter(args.embedding_rps)
        # Bind progress to the actual account's tables, not only a profile alias.
        targets = [dynamo.describe_table(TableName=name)["Table"]["TableArn"]
                   for name in (DYNAMODB_CORPORA_TABLE, DYNAMODB_CHUNKS_TABLE)]
        identity.update(targets=targets, corpus=args.corpus_name, model=MODEL_ID, dimensions=DIMENSIONS)
        if checkpoint.exists():
            previous = json.loads(checkpoint.read_text(encoding="utf-8"))
            if previous.get("identity") != identity:
                raise ValueError("Checkpoint does not match snapshot or target. Use a new checkpoint.")
        repository = DynamoRepository(client=dynamo)
        corpus = get_or_create_arxiv_corpus(name=args.corpus_name, repository=repository)
        metrics = {"embedding_calls": 0, "embedding_tokens": 0}
        metrics_lock = Lock()

        def embed(text):
            response = invoke_embedding(bedrock, limiter,
                modelId=MODEL_ID, contentType="application/json", accept="application/json",
                body=json.dumps({"inputText": text, "dimensions": DIMENSIONS, "normalize": True}),
            )
            with response["body"] as body:
                result = json.loads(body.read())
            with metrics_lock:
                metrics["embedding_calls"] += 1
                metrics["embedding_tokens"] += result.get("inputTextTokenCount", 0)
            return result["embedding"]

        def process(paper):
            try:
                return save_arxiv_paper(corpus=corpus, paper=paper, repository=repository,
                                        embedding_function=embed).status
            except Exception as exc:
                raise RuntimeError(f"Failed paper {paper.external_id}; rerun to resume: {exc}") from exc

        print(f"Importing into {corpus.name} ({corpus.id}); checkpoint: {checkpoint}", flush=True)
        try:
            summary = run_import(args.source, checkpoint, identity, process, **options)
            print(json.dumps(summary, indent=2))
        finally:
            print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
