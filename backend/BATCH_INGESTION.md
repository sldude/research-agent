# arXiv batch embedding pilot

Run commands below from `backend` with the backend virtual environment active.
Keep `AWS_REGION=us-east-2` in `.env`: it selects the existing DynamoDB tables.
Bedrock batch jobs and the staging bucket use Virginia (`us-east-1`) separately.
Stop the on-demand importer before preparing a batch, and leave its checkpoint
in place. Do not run concurrent importers against this corpus.

## Prepare 10,000 new embeddings

```powershell
python -m app.scripts.arxiv_batch prepare ../data/arxiv-metadata-oai-snapshot.json ../data/batch-pilot --limit 10000
```

This makes **read-only DynamoDB requests** (which incur read charges) and writes
local files. It does not generate embeddings, create AWS resources, or write to
DynamoDB. It skips records whose content, model and dimensions already match.
Metadata-only changes on those skipped papers are not synchronized by this tool.
The snapshot is streamed, but metadata for the selected batch is held in memory.
Preparation checks up to 100 distinct papers per DynamoDB request without
downloading their vectors. It retries unprocessed keys and stops if they cannot
be read, rather than treating them as missing. Progress includes checked and
skipped papers, even when no new candidates have been collected.

To skip the completed prefix of your on-demand import, use its existing checkpoint:

```powershell
python -m app.scripts.arxiv_batch prepare ../data/arxiv-metadata-oai-snapshot.json ../data/batch-pilot-fast --limit 10000 --from-checkpoint ../data/arxiv-metadata-oai-snapshot.json.checkpoint.json
```

Stop the previous preparation process first and use a **new output directory**.
The checkpoint is read-only and must match the snapshot, tables, corpus and model.
The tool seeks directly to its byte offset; records after that point are still
checked against DynamoDB. This deliberately trusts that the completed prefix is
already imported; omit the option if you need to check that prefix again. Use the
chosen output directory in subsequent upload/import commands as well.

The new directory contains:

- `input.jsonl`: upload this file to Bedrock.
- `manifest.json`: keep locally for matching results to papers and the target tables.

The directory must not already exist. An interrupted/failed preparation has no
valid manifest; use a fresh directory and repeat. The minimum is 100 candidates;
use the on-demand importer for a tail smaller than 100. Maximum `--limit` is
100000 and input bytes are capped at a conservative 1 GB. Verify the actual
applied batch quotas in Virginia before submitting.

## Create the staging bucket and upload

Create the separate staging stack in **US East (N. Virginia), us-east-1**:

```powershell
aws cloudformation deploy --template-file batch-infrastructure.json --stack-name research-agent-batch --capabilities CAPABILITY_IAM --profile research-agent --region us-east-1
aws cloudformation describe-stacks --stack-name research-agent-batch --query "Stacks[0].Outputs" --output table --profile research-agent --region us-east-1
```

This creates a bucket with a generated unique name, blocked public access and
default encryption, plus a Bedrock role scoped to the staging input/output paths.
It does not modify the API stack or Ohio tables. Your identity needs CloudFormation,
S3 and IAM creation permissions. The bucket is retained if the stack is deleted;
there is no automatic expiration of inputs or results. Copy `BucketName` and
`BatchRoleArn` from the outputs. Put that bucket name in place of `YOUR-BUCKET`:

```powershell
aws s3 cp ../data/batch-pilot/input.jsonl s3://YOUR-BUCKET/arxiv/pilot/input/input.jsonl --profile research-agent --region us-east-1
```

Upload only `input.jsonl`; do not upload the snapshot or the local manifest into
the job's input prefix.

## Submit the pilot in the Bedrock console

Switch to Virginia, then Batch inference → Create job:

- Job name: `arxiv-pilot-001`
- Model: Amazon Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`)
- Invocation type: `InvokeModel`
- Input: `s3://YOUR-BUCKET/arxiv/pilot/input/input.jsonl`
- Output: `s3://YOUR-BUCKET/arxiv/pilot/output/`
- Service access: Use an existing service role; select the role identified by
  `BatchRoleArn` in the stack outputs. Your console identity needs permission to
  pass that role and submit the job.

Submitting starts billable embedding work. Once submitted, processing happens
on AWS; the local importer need not stay running. Wait until the job reaches a
terminal status before downloading its results. Do not resubmit merely because
it is queued. Inspect job errors if it fails validation.

## Download, validate, and import

```powershell
aws s3 cp s3://YOUR-BUCKET/arxiv/pilot/output/ ../data/batch-pilot/results/ --recursive --profile research-agent --region us-east-1

# Offline validation; writes a local import-report.json, no AWS calls.
python -m app.scripts.arxiv_batch import-results ../data/batch-pilot/manifest.json ../data/batch-pilot/results

# Store valid results in the existing Ohio tables; no embedding API calls.
python -m app.scripts.arxiv_batch import-results ../data/batch-pilot/manifest.json ../data/batch-pilot/results --execute
```

The loader discovers `.jsonl.out` files recursively and ignores AWS's summary
`manifest.json.out`. Keep each job's outputs in its own directory. Do not modify
the manifest or outputs while importing. All records are validated before writes:
unknown/duplicate IDs, mismatched input text, wrong vector dimensions, non-finite
values, and target/model mismatches are rejected. Match by ID, never output order.

Valid records from partially successful jobs can be imported. Missing records and
per-record errors appear in `import-report.json`; a nonzero failed-or-missing count
means the batch is not fully ingested. Re-run preparation over the same source
range into a new directory to retry failures while skipping successful imports.
For fewer than 100 remaining candidates use the on-demand importer.

If importing stops because of an AWS error, re-run the same import command.
Already stored embeddings are skipped. Records changed since preparation are
reported as conflicts and not overwritten. The report is final only after a
successful run; revalidate/reimport after an interruption. No checkpoint from the
on-demand importer is advanced or deleted.

## Continue beyond the pilot

After resolving the pilot's errors, read `end_line` from its local manifest and use
that value with `--start-line` to prepare the next batch in a fresh directory:

```powershell
python -m app.scripts.arxiv_batch prepare ../data/arxiv-metadata-oai-snapshot.json ../data/batch-002 --limit 100000 --start-line END_LINE
```

Replace `END_LINE` with the numeric value. Keep the same immutable snapshot.
Use distinct S3 input/output prefixes per job. Automated job orchestration is not
included; start with the pilot before submitting larger jobs. Allow for S3 storage,
requests, data transfer, Bedrock batch inference, and DynamoDB read/write charges.

AWS references:
- https://docs.aws.amazon.com/bedrock/latest/userguide/batch-inference-supported.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/batch-inference-data.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/batch-inference-create.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/batch-inference-results.html
