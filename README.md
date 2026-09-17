# research-agent

Thank you to arXiv for use of its open access interoperability.

The backend uses Amazon DynamoDB with native vector search for corpus metadata,
document chunks, and Titan embeddings. Amazon Bedrock generates embeddings and
grounded answers.

From `backend`, install dependencies and provision the on-demand tables:

```bash
pip install -r requirements.txt
python -m app.scripts.create_dynamodb_tables
```

Provisioning creates `research-agent-corpora`, `research-agent-chunks`,
`research-agent-document-status`, and a cosine vector index named
`embedding-index` on the chunks table. Table deletion protection is enabled.
Existing tables are reused. Configure alternate names in `backend/.env` if needed.

The API template references these tables; the Python provisioning script creates
them. Keep `DYNAMODB_DOCUMENT_STATUS_TABLE` in the local environment and the SAM
`DocumentStatusTableName` parameter set to the same name. Provision tables before
deploying the API.

If you already deployed the earlier template that created `DocumentStatusTable`,
reuse its physical table name in both settings. That earlier resource has
`DeletionPolicy: Retain`, so removing it from the stack retains the table and its
data. The provisioning script will skip creating it when configured with that name.

## Bulk arXiv abstract ingestion

For asynchronous Titan V2 embedding jobs through S3 and Bedrock, see
[the batch ingestion walkthrough](backend/BATCH_INGESTION.md). It includes local
preparation, console setup in Virginia, and result import into the Ohio tables.

Download the metadata snapshot from https://www.kaggle.com/datasets/Cornell-University/arxiv
and unpack `arxiv-metadata-oai-snapshot.json` into `data/` at the project root.
This is newline-delimited JSON despite its `.json` extension. PDFs are not needed.
Keep snapshots outside `backend/` so they are not bundled into Lambda deployments.
The root `data/` folder is ignored by Git.

From `backend`, using the Python environment with the backend dependencies installed:

```powershell
# Validate the first 10,000 records locally. No AWS calls or writes.
python -m app.scripts.arxiv_bulk_import ../data/arxiv-metadata-oai-snapshot.json --limit 10000

# Embed and save the first 10,000 records to the existing main corpus.
python -m app.scripts.arxiv_bulk_import ../data/arxiv-metadata-oai-snapshot.json --limit 10000 --execute

# Resume from the checkpoint and import every remaining record.
python -m app.scripts.arxiv_bulk_import ../data/arxiv-metadata-oai-snapshot.json --all --execute
```

Execution uses the AWS profile and region in `backend/.env` and incurs Bedrock
and DynamoDB charges. Tables must already exist. The importing identity needs
`DescribeTable`, `Scan`, `GetItem`, and `PutItem` on the configured tables plus
permission to invoke the embedding model. The API Lambda role is read-only and
is not used for this job. No API deployment is required to run the importer.

The default corpus is `My arXiv research corpus`, matching the existing interactive
importer. Use `--corpus-name "..."` to choose another corpus. The job preserves
title, abstract, authors, categories, dates, license and source URL. It embeds
title + abstract with the configured model and dimensions. Unchanged embeddings
are reused; metadata-only changes preserve the vector.

`--workers` controls concurrent papers (default 2, maximum 32). Free workers take
another paper as soon as results are collected, without waiting for a whole batch.
Read-ahead is capped at four times the worker count beyond the checkpoint; a very
slow early paper can temporarily pause refill to keep memory and replay bounded.
DynamoDB requests retry transient failures with standard SDK backoff, up to eight
attempts per request. Embedding requests are paced across all workers with
`--embedding-rps 5` by default (an initial operating rate, not a detected AWS quota).
On Bedrock throttling, all workers share a roughly 60-second cooldown and the rate
halves for the rest of this invocation. Embedding calls have up to eight attempts;
other supported transient service errors use exponential backoff with jitter.
Permission and validation errors fail immediately. SDK retries are disabled for
Bedrock so retries also obey the shared limiter. The limiter applies only to this
process; other applications can consume the same account quota. Worker count and
embedding rate can change when resuming without changing the checkpoint.
Progress prints every approximately 100 records; the final output includes created,
updated and unchanged counts, elapsed seconds, successful embedding calls and
reported input tokens. Token counts cover the current invocation and are not an
AWS billing report; ambiguous network failures or interrupted calls can still bill.

A checkpoint next to the snapshot records the last consecutive successful record.
It never advances past an unfinished or failed paper, even if later papers finish.
Existing checkpoints from the batch importer remain compatible. Repeat
the same command to resume. `--limit` applies to each invocation, so another limited
run processes the next 10,000 papers. Records beyond the checkpoint may be replayed;
papers already stored are detected and do not need re-embedding. An embedding that
was generated but not stored may be charged again. Malformed records stop the run
with a line number rather than being silently skipped. Dry runs always validate
from the beginning and do not change progress.

Keep the snapshot immutable during import. Checkpoints are bound to its path, size
and modification time, the actual AWS table ARNs, corpus and embedding configuration.
Use `--checkpoint ../data/another-progress.json` for a new snapshot or target. Do not
run multiple import jobs against the same corpus concurrently. A checkpoint lock
prevents concurrent use of the same progress file; after a hard process termination,
remove its `.lock` file only once the old process has stopped.

For a refreshed snapshot, use a new checkpoint and run it through the same corpus;
unchanged records are skipped. Use snapshots in chronological order: the importer
does not guard against an older snapshot replacing newer content. Automated daily
OAI-PMH synchronization and removal of papers absent from a snapshot are not included.
