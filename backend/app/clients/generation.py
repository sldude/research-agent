"""Generate text with a configurable Amazon Bedrock conversation model."""

import os
from functools import lru_cache

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv


load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
AWS_PROFILE = os.getenv("AWS_PROFILE")
GENERATION_MODEL_ID = os.getenv(
    "BEDROCK_GENERATION_MODEL_ID",
    # Nova 2 Lite requires an inference profile for on-demand requests. The
    # US profile can route the request across supported US Bedrock regions.
    "us.amazon.nova-2-lite-v1:0",
)


class GenerationError(RuntimeError):
    """Raised when Bedrock cannot generate a usable text response."""


@lru_cache(maxsize=1)
def create_bedrock_client():
    """Create one reusable Bedrock Runtime client on the first model call."""

    # profile_name is useful during local development. When this application is
    # deployed to AWS, boto3 can instead use the IAM role attached to the app.
    session = boto3.Session(
        profile_name=AWS_PROFILE,
        region_name=AWS_REGION,
    )
    return session.client("bedrock-runtime")


def generate_text(
    prompt: str,
    *,
    system_prompt: str | None = None,
    max_tokens: int = 500,
    temperature: float = 0.1,
) -> str:
    """Send one prompt through Bedrock Converse and return generated text."""

    cleaned_prompt = prompt.strip()
    if not cleaned_prompt:
        raise ValueError("prompt cannot be empty")
    if not 1 <= max_tokens <= 4096:
        raise ValueError("max_tokens must be between 1 and 4096")
    if not 0.0 <= temperature <= 1.0:
        raise ValueError("temperature must be between 0.0 and 1.0")

    request = {
        "modelId": GENERATION_MODEL_ID,
        "messages": [
            {
                "role": "user",
                "content": [{"text": cleaned_prompt}],
            }
        ],
        "inferenceConfig": {
            "maxTokens": max_tokens,
            "temperature": temperature,
        },
    }

    # Bedrock's system field is optional. The RAG service uses it for
    # stable grounding and citation instructions that are separate from data.
    if system_prompt and system_prompt.strip():
        request["system"] = [{"text": system_prompt.strip()}]

    try:
        response = create_bedrock_client().converse(**request)
    except (BotoCoreError, ClientError) as exc:
        raise GenerationError(
            f"Bedrock generation failed for {GENERATION_MODEL_ID}: {exc}"
        ) from exc

    try:
        content_blocks = response["output"]["message"]["content"]
        text_blocks = [
            block["text"]
            for block in content_blocks
            if isinstance(block, dict) and block.get("text")
        ]
    except (KeyError, TypeError) as exc:
        raise GenerationError("Bedrock returned an unexpected response") from exc

    generated_text = "\n".join(text_blocks).strip()
    if not generated_text:
        raise GenerationError("Bedrock returned no generated text")

    return generated_text
