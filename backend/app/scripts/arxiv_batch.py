"""Prepare Titan V2 batch inputs and ingest downloaded results (no model calls)."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from app.scripts.arxiv_bulk_import import normalize_record, source_identity
from app.schemas.api_schemas import ArxivPaper


def model_input(paper, dimensions):
    return {"inputText": f"{paper.title}\n\n{paper.abstract}".strip(),
            "dimensions": dimensions, "normalize": True}


def fingerprint(document):
    if document is None:
        return None
    return hashlib.sha256(json.dumps({
        "content": document.content, "model": document.embedding_model,
        "dimensions": document.embedding_dimensions,
        "updated": str(document.updated_date),
    }, sort_keys=True).encode()).hexdigest()


def checkpoint_position(path, source, target, corpus, model, dimensions):
    state = json.loads(path.read_text(encoding="utf-8"))
    identity = {"source": source_identity(source), "targets": target,
                "corpus": corpus.name, "model": model, "dimensions": dimensions}
    if state.get("version") != 1 or state.get("identity") != identity:
        raise ValueError("Checkpoint does not match snapshot, tables, corpus or model")
    line, offset = state.get("line"), state.get("offset")
    if (type(line) is not int or line < 0 or type(offset) is not int
            or not 0 <= offset <= source.stat().st_size or (line == 0) != (offset == 0)):
        raise ValueError("Invalid checkpoint position")
    with source.open("rb") as stream:
        if 0 < offset < source.stat().st_size:
            stream.seek(offset - 1)
            if stream.read(1) != b"\n":
                raise ValueError("Checkpoint offset is not at a record boundary")
    return line, offset


def prepare(source, destination, *, repository, corpus, target, model, dimensions,
            limit=10_000, start_line=0, from_checkpoint=None):
    """Read AWS only; create a new local directory, never overwrite a batch."""
    if not 100 <= limit <= 100_000 or start_line < 0:
        raise ValueError("limit must be 100-100000 and start-line must be nonnegative")
    offset = 0
    if from_checkpoint is not None:
        if start_line:
            raise ValueError("Choose start-line or from-checkpoint, not both")
        start_line, offset = checkpoint_position(from_checkpoint, source, target, corpus, model, dimensions)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"version": 1, "source": source_identity(source), "target": target,
                "model": model, "dimensions": dimensions,
                "corpus_name": corpus.name if corpus else "My arXiv research corpus",
                "start_line": start_line, "end_line": start_line, "records": {}}
    skipped = 0
    seen = set()
    size = 0
    print(f"Starting after source line {start_line}; checking up to 100 papers per DynamoDB request.", flush=True)
    with source.open("rb") as stream, (destination / "input.jsonl").open("x", encoding="utf-8", newline="\n") as output:
        if from_checkpoint is not None:
            stream.seek(offset)
        else:
            for number in range(start_line):
                if not stream.readline():
                    raise ValueError("start-line exceeds snapshot length")
        number = start_line
        while len(seen) < limit:
            batch = []
            for _ in range(min(100, limit - len(seen))):
                raw = stream.readline()
                if not raw:
                    break
                number += 1
                try:
                    batch.append((number, normalize_record(json.loads(raw))))
                except Exception as exc:
                    raise ValueError(f"Invalid snapshot record at line {number}") from exc
            if not batch:
                break
            existing_documents = repository.get_documents(
                corpus_id=corpus.id, source="arxiv",
                external_ids=[paper.external_id for _, paper in batch],
            ) if corpus else {}
            for number, paper in batch:
                manifest["end_line"] = number
                if paper.external_id in seen:
                    continue
                existing = existing_documents.get(paper.external_id)
                body = model_input(paper, dimensions)
                if (existing is not None and existing.content == body["inputText"]
                        and existing.embedding_model == model and existing.embedding_dimensions == dimensions):
                    skipped += 1
                    continue
                record_id = hashlib.sha256(paper.external_id.encode()).hexdigest()[:32]
                line = json.dumps({"recordId": record_id, "modelInput": body}, ensure_ascii=False) + "\n"
                size += len(line.encode("utf-8"))
                if size > 1_000_000_000:
                    raise ValueError("Input exceeds the conservative 1 GB file limit; use a smaller --limit")
                output.write(line)
                seen.add(paper.external_id)
                manifest["records"][record_id] = {"paper": paper.model_dump(mode="json"),
                                                   "previous": fingerprint(existing)}
            print(f"Checked {number - start_line}; prepared {len(seen)}; skipped {skipped} existing embeddings; source line {number}", flush=True)
    if len(seen) < 100:
        raise ValueError(f"Only {len(seen)} candidates remain; batch needs at least 100. Use the on-demand importer for this tail.")
    with (destination / "input.jsonl").open("rb") as prepared_input:
        manifest["input_sha256"] = hashlib.file_digest(prepared_input, "sha256").hexdigest()
    # Written last: absent manifest means preparation failed, not a submit-ready batch.
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"prepared": len(seen), "skipped": skipped, "end_line": manifest["end_line"], "input_bytes": size}


def validated_results(files, manifest):
    seen = set()
    for path in files:
        with path.open(encoding="utf-8") as stream:
            for number, raw in enumerate(stream, 1):
                row = json.loads(raw)
                record_id = row.get("recordId")
                if record_id not in manifest["records"] or record_id in seen:
                    raise ValueError(f"Unknown or duplicate recordId in {path}:{number}: {record_id}")
                seen.add(record_id)
                paper = ArxivPaper.model_validate(manifest["records"][record_id]["paper"])
                if row.get("modelInput") != model_input(paper, manifest["dimensions"]):
                    raise ValueError(f"Input mismatch for {record_id}; use the output belonging to this manifest")
                if "error" in row:
                    yield record_id, paper, None, row["error"]
                    continue
                vector = row.get("modelOutput", {}).get("embedding")
                if (not isinstance(vector, list) or len(vector) != manifest["dimensions"]
                        or any(type(v) not in (float, int) or not math.isfinite(v) for v in vector)
                        or not any(vector)):
                    raise ValueError(f"Invalid embedding for {record_id}")
                yield record_id, paper, vector, None


def import_results(manifest_path, files, *, repository=None, corpus=None, target=None,
                   model=None, dimensions=None):
    """Validate everything before writing. Replays reuse stored vectors."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not 100 <= len(manifest["records"]) <= 100_000:
        raise ValueError("Invalid batch manifest")
    if not files:
        raise ValueError("No .jsonl.out results found; download the completed job output first")
    if repository is not None and (
        manifest["target"] != target or manifest["model"] != model
        or manifest["dimensions"] != dimensions or corpus.name != manifest["corpus_name"]
    ):
        raise ValueError("Manifest does not match configured tables, corpus or embedding model")
    seen, failures = set(), {}
    file_states = [source_identity(path) for path in files]
    for record_id, paper, vector, error in validated_results(files, manifest):
        seen.add(record_id)
        if vector is None:
            failures[record_id] = error
    missing = set(manifest["records"]) - seen
    for record_id in missing:
        failures[record_id] = {"reason": "missing output"}
    counts = {"valid": len(seen) - (len(failures) - len(missing)),
              "failed_or_missing": len(failures), "created": 0, "updated": 0, "unchanged": 0}
    report = manifest_path.parent / "import-report.json"
    report.write_text(json.dumps({"counts": counts, "failures": failures}, indent=2), encoding="utf-8")
    if repository is None:
        return counts
    if file_states != [source_identity(path) for path in files]:
        raise ValueError("Output files changed during validation")
    from app.services.arxiv_ingestion import save_arxiv_paper
    for record_id, paper, vector, error in validated_results(files, manifest):
        if vector is None:
            continue
        existing = repository.get_document(corpus_id=corpus.id, source="arxiv", external_id=paper.external_id)
        current = (existing is not None and existing.content == model_input(paper, dimensions)["inputText"]
                   and existing.embedding_model == model and existing.embedding_dimensions == dimensions)
        if current:
            # Don't overwrite metadata that another importer updated after preparation.
            counts["unchanged"] += 1
            continue
        if fingerprint(existing) != manifest["records"][record_id]["previous"]:
            failures[record_id] = {"reason": "document changed since preparation; not overwritten"}
            counts["failed_or_missing"] += 1
            continue
        result = save_arxiv_paper(corpus=corpus, paper=paper, repository=repository,
                                  embedding_function=lambda text: vector)
        counts[result.status] += 1
        if (counts["created"] + counts["updated"]) % 100 == 0:
            print(counts, flush=True)
    report.write_text(json.dumps({"counts": counts, "failures": failures}, indent=2), encoding="utf-8")
    return counts


def aws_context():
    from app.database.repository import DynamoRepository
    from app.database.database_connect import DYNAMODB_CHUNKS_TABLE, DYNAMODB_CORPORA_TABLE
    repository = DynamoRepository()
    target = [repository.client.describe_table(TableName=name)["Table"]["TableArn"]
              for name in (DYNAMODB_CORPORA_TABLE, DYNAMODB_CHUNKS_TABLE)]
    return repository, target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="Read DynamoDB and write local batch files; no embeddings or AWS writes")
    prep.add_argument("source", type=Path)
    prep.add_argument("destination", type=Path, help="New directory for input.jsonl and manifest.json")
    prep.add_argument("--limit", type=int, default=10_000)
    position = prep.add_mutually_exclusive_group()
    position.add_argument("--start-line", type=int, default=0, help="Skip this many source lines; use previous manifest end_line")
    position.add_argument("--from-checkpoint", type=Path, help="Seek directly to the on-demand importer's saved position")
    prep.add_argument("--corpus-name", default="My arXiv research corpus")
    ingest = commands.add_parser("import-results", help="Validate downloaded results; --execute writes DynamoDB")
    ingest.add_argument("manifest", type=Path)
    ingest.add_argument("output", type=Path, help="Single result file or directory containing .jsonl.out files")
    ingest.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        if not args.source.is_file() or not 100 <= args.limit <= 100_000 or args.start_line < 0:
            parser.error("source must exist, limit must be 100-100000, and start-line must be nonnegative")
        from app.clients.embeddings import MODEL_ID, DIMENSIONS
        if MODEL_ID != "amazon.titan-embed-text-v2:0":
            parser.error("This batch workflow supports Titan Text Embeddings V2 only")
        repository, target = aws_context()
        corpus = repository.find_corpus(name=args.corpus_name, corpus_type="research_abstract", owner_id=None)
        if corpus is None:
            parser.error("Corpus does not exist; choose an existing corpus with --corpus-name")
        result = prepare(args.source, args.destination, repository=repository, corpus=corpus,
                         target=target, model=MODEL_ID, dimensions=DIMENSIONS,
                         limit=args.limit, start_line=args.start_line, from_checkpoint=args.from_checkpoint)
    else:
        files = sorted(args.output.rglob("*.jsonl.out")) if args.output.is_dir() else [args.output]
        # Always run full offline validation before creating any AWS clients.
        result = import_results(args.manifest, files)
        if args.execute:
            from app.clients.embeddings import MODEL_ID, DIMENSIONS
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            repository, target = aws_context()
            corpus = repository.find_corpus(name=manifest["corpus_name"], corpus_type="research_abstract", owner_id=None)
            if corpus is None:
                parser.error("Target corpus no longer exists")
            result = import_results(args.manifest, files, repository=repository, corpus=corpus,
                                    target=target, model=MODEL_ID, dimensions=DIMENSIONS)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
