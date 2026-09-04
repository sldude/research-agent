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

Provisioning creates `research-agent-corpora`, `research-agent-chunks`, and a
cosine vector index named `embedding-index`. Table deletion protection is
enabled. Configure alternate names in `backend/.env` if needed.
