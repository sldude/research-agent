import json
import os

import boto3
from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
AWS_PROFILE = os.getenv("AWS_PROFILE")
MODEL_ID = os.getenv(
    "BEDROCK_EMBEDDING_MODEL_ID",
    "amazon.titan-embed-text-v2:0",
)
DIMENSIONS = int(
    os.getenv("BEDROCK_EMBEDDING_DIMENSIONS", "1024")
)

# creates bedrock
def create_bedrock_client():
    session = boto3.Session(
        profile_name=AWS_PROFILE,
        region_name=AWS_REGION,
    )
    return session.client("bedrock-runtime")


bedrock = create_bedrock_client()


def embed_text(text: str) -> list[float]:
    cleaned_text = text.strip()

    if not cleaned_text:
        raise ValueError("Cannot embed empty text")

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(
            {
                "inputText": cleaned_text,
                "dimensions": DIMENSIONS,
                "normalize": True,
            }
        ),
    )

    result = json.loads(response["body"].read())
    embedding = result["embedding"]

    if len(embedding) != DIMENSIONS:
        raise RuntimeError(
            f"Expected {DIMENSIONS} values, got {len(embedding)}"
        )

    return embedding